import asyncio
import os
import time
from collections import deque
from datetime import datetime
from fractions import Fraction
from threading import Event, Lock, Thread

import cv2
import numpy as np
from aiortc import VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRelay
from av import VideoFrame

from .config import Config


class CameraService:
    def __init__(self, config: Config):
        self.config = config
        self._capture: cv2.VideoCapture | None = None
        self._capture_thread: Thread | None = None
        self._stop_event = Event()
        self._new_frame_event = Event()
        self._async_new_frame_event: asyncio.Event | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._frame_lock = Lock()
        self._latest_frame: np.ndarray | None = None
        self._effective_fps = max(1, config.camera_fps)
        self._stream_fps = max(1, min(config.stream_target_fps, self._effective_fps))
        self._metrics_lock = Lock()
        self._started_at = time.time()
        self._capture_timestamps: deque[float] = deque(maxlen=180)
        self._capture_fps_estimate = 0.0
        self._last_frame_at = 0.0
        self._camera_connected = False
        self._camera_open_failures = 0
        self._camera_reconnects = 0
        self._consecutive_read_failures = 0
        self._black_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self._paused = False

    @property
    def is_running(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        # Stop capture thread and release the camera device so the physical
        # camera actually turns off (LED goes out, /dev/videoX is freed).
        self._stop_event.set()
        self._new_frame_event.set()  # unblock any run_in_executor waiters
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        with self._metrics_lock:
            self._camera_connected = False
        print("Camera paused — device released.", flush=True)

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        # Reopen device and restart capture thread.
        self._stop_event.clear()
        self._capture = self._open_camera_capture()
        self._capture_thread = Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._capture_thread.start()
        print("Camera resumed.", flush=True)

    @property
    def stream_fps(self) -> int:
        return self._stream_fps

    def start(self) -> None:
        self._stop_event.clear()
        self._capture = self._open_camera_capture()
        self._capture_thread = Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._capture_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._new_frame_event.set()  # unblock any waiters
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        with self._metrics_lock:
            self._camera_connected = False
            self._consecutive_read_failures = 0

    def latest_frame(self) -> np.ndarray:
        if self._paused:
            return self._black_frame
        with self._frame_lock:
            if self._latest_frame is None:
                return self._black_frame
            return self._latest_frame

    def wait_new_frame(self, timeout: float = 0.2) -> None:
        """Block caller thread until a new frame arrives or timeout elapses.
        Safe to call from multiple threads simultaneously."""
        self._new_frame_event.wait(timeout=timeout)
        self._new_frame_event.clear()

    async def wait_new_frame_async(self, timeout: float) -> None:
        if self._async_new_frame_event is None:
            self._event_loop = asyncio.get_running_loop()
            self._async_new_frame_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._async_new_frame_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self._async_new_frame_event.clear()

    def _open_camera_capture(self) -> cv2.VideoCapture | None:
        capture = cv2.VideoCapture(self.config.camera_device, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            with self._metrics_lock:
                self._camera_connected = False
                self._camera_open_failures += 1
                failures = self._camera_open_failures
            print(
                f"Camera unavailable ({self.config.camera_device}) [attempt {failures}]. "
                f"Retrying in {self.config.camera_open_retry_ms}ms...",
                flush=True,
            )
            return None

        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera_height)
        capture.set(cv2.CAP_PROP_FPS, self.config.camera_fps)
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
        detected_fps = int(round(capture.get(cv2.CAP_PROP_FPS)))
        self._effective_fps = detected_fps if detected_fps > 0 else max(1, self.config.camera_fps)
        self._stream_fps = max(1, min(self.config.stream_target_fps, self._effective_fps))
        self._black_frame = np.zeros((actual_height, actual_width, 3), dtype=np.uint8)
        with self._metrics_lock:
            self._camera_connected = True
            self._consecutive_read_failures = 0
        print(f"Camera connected: {self.config.camera_device}", flush=True)
        return capture

    def _release_capture(self, reconnect: bool, reason: str) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        with self._metrics_lock:
            if reconnect:
                self._camera_reconnects += 1
            self._camera_connected = False
            self._consecutive_read_failures = 0
        print(reason, flush=True)

    def _capture_loop(self) -> None:
        retry_delay = max(0.1, self.config.camera_open_retry_ms / 1000.0)
        while not self._stop_event.is_set():
            if self._capture is None or not self._capture.isOpened():
                self._capture = self._open_camera_capture()
                if self._capture is None:
                    time.sleep(retry_delay)
                    continue

            ok, frame = self._capture.read()
            if not ok:
                with self._metrics_lock:
                    self._consecutive_read_failures += 1
                    failures = self._consecutive_read_failures
                if failures >= self.config.camera_read_fail_threshold:
                    self._release_capture(
                        reconnect=True,
                        reason=(
                            f"Camera read failed {failures}x. Reconnecting "
                            f"to {self.config.camera_device}..."
                        ),
                    )
                    continue
                time.sleep(0.05)
                continue

            with self._metrics_lock:
                self._consecutive_read_failures = 0
            stamped = self._add_timestamp(frame)
            timestamp = time.time()
            self._record_capture_tick(timestamp)
            with self._frame_lock:
                self._latest_frame = stamped
            self._new_frame_event.set()
            if self._event_loop is not None and self._async_new_frame_event is not None:
                self._event_loop.call_soon_threadsafe(self._async_new_frame_event.set)

    def _record_capture_tick(self, timestamp: float) -> None:
        with self._metrics_lock:
            self._last_frame_at = timestamp
            self._capture_timestamps.append(timestamp)
            if len(self._capture_timestamps) >= 2:
                span = self._capture_timestamps[-1] - self._capture_timestamps[0]
                if span > 0:
                    self._capture_fps_estimate = (len(self._capture_timestamps) - 1) / span

    def _add_timestamp(self, frame: np.ndarray) -> np.ndarray:
        label = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} WIB"
        frame_h, frame_w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.55, min(0.8, frame_w / 1920))
        thickness = 2
        (text_w, text_h), baseline = cv2.getTextSize(label, font, scale, thickness)
        x = frame_w - text_w - 20
        y = 20 + text_h
        cv2.rectangle(
            frame,
            (x - 8, y - text_h - 8),
            (x + text_w + 8, y + baseline + 8),
            (0, 0, 0),
            thickness=-1,
        )
        cv2.putText(frame, label, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
        return frame

    def metrics_snapshot(self) -> dict[str, float | int | bool]:
        with self._metrics_lock:
            return {
                "camera_running": self.is_running,
                "camera_connected": self._camera_connected,
                "uptime_seconds": int(max(0.0, time.time() - self._started_at)),
                "camera_fps_detected": self._effective_fps,
                "camera_fps_estimate": round(self._capture_fps_estimate, 2),
                "camera_open_failures": self._camera_open_failures,
                "camera_reconnects": self._camera_reconnects,
                "camera_read_failures": self._consecutive_read_failures,
                "stream_target_fps": self._stream_fps,
                "last_frame_age_ms": round(max(0.0, (time.time() - self._last_frame_at) * 1000.0), 1),
            }


class CameraTrack(VideoStreamTrack):
    def __init__(self, camera_service: CameraService):
        super().__init__()
        self._camera_service = camera_service
        self._pts = 0
        self._time_base = Fraction(1, max(1, self._camera_service.stream_fps))

    async def recv(self) -> VideoFrame:
        # Wait for the capture thread to signal a new frame — event-driven,
        # no artificial sleep. Timeout = 2 frame periods so we always deliver
        # something (e.g. black frame) even if the camera stalls.
        timeout = float(self._time_base) * 2
        await self._camera_service.wait_new_frame_async(timeout)
        frame = self._camera_service.latest_frame()
        self._pts += 1
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = self._pts
        video_frame.time_base = self._time_base
        return video_frame


class AudioInputService:
    def __init__(self, config: Config):
        self._config = config
        self._relay = MediaRelay()
        self._player: MediaPlayer | None = None
        self._active_device: str | None = None

    @property
    def enabled(self) -> bool:
        return self._config.audio_enabled

    @property
    def active_device(self) -> str | None:
        return self._active_device

    def start(self) -> None:
        if not self._config.audio_enabled:
            return
        os.environ.setdefault("ALSA_CONFIG_PATH", self._config.alsa_config_path)
        os.environ.setdefault("ALSA_CONFIG_DIR", self._config.alsa_config_dir)
        options = {
            "channels": str(self._config.audio_channels),
            "sample_rate": str(self._config.audio_sample_rate),
        }
        candidates = []
        for device in (self._config.audio_device, "hw:1,0", "hw:0,0"):
            if device not in candidates:
                candidates.append(device)

        errors: list[str] = []
        for device in candidates:
            try:
                player = MediaPlayer(device, format="alsa", options=options)
                if player.audio is None:
                    errors.append(f"{device}: no audio track")
                    continue
                self._player = player
                self._active_device = device
                return
            except OSError as exc:
                errors.append(f"{device}: {exc}")

        detail = "; ".join(errors) if errors else "no candidate device"
        raise RuntimeError(f"Failed to open audio input device ({detail})")

    def stop(self) -> None:
        if self._player is not None:
            if self._player.audio is not None:
                self._player.audio.stop()
            self._player = None
            self._active_device = None

    def subscribe_track(self):
        if not self._config.audio_enabled:
            return None
        if self._player is None or self._player.audio is None:
            raise RuntimeError("Audio input service is not initialized")
        return self._relay.subscribe(self._player.audio)
