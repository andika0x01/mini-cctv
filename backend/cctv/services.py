import asyncio
import os
import time
from collections import deque
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from threading import Event, Lock, Thread

import cv2
import numpy as np
from aiortc import VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRelay
from av import VideoFrame

from .config import Config
from .detector import YoloPersonDetector
from .notifier import TelegramNotifier
from .scenario_state import ScenarioStateStore


class CameraService:
    def __init__(
        self,
        config: Config,
        notifier: TelegramNotifier,
        loop: asyncio.AbstractEventLoop,
        detector: YoloPersonDetector,
        scenario_store: ScenarioStateStore,
    ):
        self.config = config
        self._notifier = notifier
        self._loop = loop
        self._detector = detector
        self._scenario_store = scenario_store
        self._capture: cv2.VideoCapture | None = None
        self._capture_thread: Thread | None = None
        self._stop_event = Event()
        self._frame_lock = Lock()
        self._latest_frame: np.ndarray | None = None
        self._effective_fps = max(1, config.camera_fps)
        self._stream_fps = max(1, min(config.stream_target_fps, self._effective_fps))
        initial_width = 1280
        initial_height = 720
        self._frame_buffer: deque[tuple[float, np.ndarray]] = deque(
            maxlen=(config.clip_pre_seconds + config.clip_post_seconds + 3) * self._effective_fps
        )
        self._detection_lock = Lock()
        self._detection_event = Event()
        self._detection_queue: deque[tuple[float, np.ndarray]] = deque(maxlen=1)
        self._detection_thread: Thread | None = None
        self._metrics_lock = Lock()
        self._started_at = time.time()
        self._capture_timestamps: deque[float] = deque(maxlen=180)
        self._capture_fps_estimate = 0.0
        self._detection_latency_ms: deque[float] = deque(maxlen=60)
        self._last_frame_at = 0.0
        self._detection_dropped_frames = 0
        self._camera_connected = False
        self._camera_open_failures = 0
        self._camera_reconnects = 0
        self._consecutive_read_failures = 0
        self._last_detection_at = 0.0
        self._scenario_key = "person_present"
        self._person_scenario_active = self._scenario_store.is_active(self._scenario_key)
        self._last_person_seen_at = time.time() if self._person_scenario_active else 0.0
        self._person_task_lock = asyncio.Lock()
        self._black_frame = np.zeros((initial_height, initial_width, 3), dtype=np.uint8)

    @property
    def is_running(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    @property
    def stream_fps(self) -> int:
        return self._stream_fps

    def start(self) -> None:
        self._stop_event.clear()
        self._capture = self._open_camera_capture()
        with self._detection_lock:
            self._detection_queue.clear()

        self._capture_thread = Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._detection_thread = Thread(target=self._detection_loop, name="person-detection", daemon=True)
        self._capture_thread.start()
        self._detection_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._detection_event.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=5)
        if self._detection_thread is not None:
            self._detection_thread.join(timeout=5)
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        with self._metrics_lock:
            self._camera_connected = False
            self._consecutive_read_failures = 0

    def latest_frame(self) -> np.ndarray:
        with self._frame_lock:
            if self._latest_frame is None:
                return self._black_frame.copy()
            return self._latest_frame.copy()

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
        with self._frame_lock:
            self._frame_buffer = deque(
                maxlen=(self.config.clip_pre_seconds + self.config.clip_post_seconds + 3) * self._effective_fps
            )
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
            stamped = self._add_timestamp(frame.copy())
            timestamp = time.time()
            self._record_capture_tick(timestamp)
            with self._frame_lock:
                self._latest_frame = stamped
                self._frame_buffer.append((timestamp, stamped.copy()))
            self._queue_detection_frame(frame, timestamp)

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

    def _queue_detection_frame(self, frame: np.ndarray, timestamp: float) -> None:
        with self._detection_lock:
            if len(self._detection_queue) == self._detection_queue.maxlen:
                with self._metrics_lock:
                    self._detection_dropped_frames += 1
            self._detection_queue.append((timestamp, frame.copy()))
            self._detection_event.set()

    def _resize_for_detection(self, frame: np.ndarray) -> np.ndarray:
        max_width = self.config.person_detection_max_width
        frame_h, frame_w = frame.shape[:2]
        if frame_w <= max_width:
            return frame
        ratio = max_width / frame_w
        resized_h = max(1, int(frame_h * ratio))
        return cv2.resize(frame, (max_width, resized_h), interpolation=cv2.INTER_AREA)

    def _detection_loop(self) -> None:
        while not self._stop_event.is_set():
            self._detection_event.wait(timeout=0.1)
            if self._stop_event.is_set():
                break

            with self._detection_lock:
                if not self._detection_queue:
                    self._detection_event.clear()
                    continue
                timestamp, frame = self._detection_queue.pop()
                self._detection_queue.clear()
                self._detection_event.clear()

            if timestamp - self._last_detection_at < self.config.person_detection_interval_ms / 1000.0:
                continue
            self._last_detection_at = timestamp

            detection_frame = self._resize_for_detection(frame)
            infer_started_at = time.perf_counter()
            has_person, confidence = self._detector.has_person(detection_frame)
            infer_elapsed_ms = (time.perf_counter() - infer_started_at) * 1000.0
            with self._metrics_lock:
                self._detection_latency_ms.append(infer_elapsed_ms)
            if not has_person:
                if (
                    self._person_scenario_active
                    and (timestamp - self._last_person_seen_at) >= self.config.person_report_cooldown_seconds
                ):
                    self._person_scenario_active = False
                    self._scenario_store.set_active(self._scenario_key, False, timestamp)
                continue

            self._last_person_seen_at = timestamp
            if self._person_scenario_active:
                continue

            self._person_scenario_active = True
            self._scenario_store.set_active(self._scenario_key, True, timestamp)
            future = asyncio.run_coroutine_threadsafe(self._handle_person_event(timestamp, confidence), self._loop)

            def _consume_future_result(done_future: asyncio.Future) -> None:
                try:
                    done_future.result()
                except Exception as exc:
                    print(f"Person event handling failed: {exc}", flush=True)

            future.add_done_callback(_consume_future_result)

    def metrics_snapshot(self) -> dict[str, float | int | bool]:
        with self._detection_lock:
            detection_queue_size = len(self._detection_queue)
        with self._frame_lock:
            frame_buffer_size = len(self._frame_buffer)
        with self._metrics_lock:
            detection_avg_ms = (
                sum(self._detection_latency_ms) / len(self._detection_latency_ms)
                if self._detection_latency_ms
                else 0.0
            )
            detection_peak_ms = max(self._detection_latency_ms) if self._detection_latency_ms else 0.0
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
                "frame_buffer_size": frame_buffer_size,
                "detection_queue_size": detection_queue_size,
                "detection_dropped_frames": self._detection_dropped_frames,
                "detection_infer_avg_ms": round(detection_avg_ms, 2),
                "detection_infer_peak_ms": round(detection_peak_ms, 2),
            }

    def _frames_between(self, start_ts: float, end_ts: float) -> list[np.ndarray]:
        with self._frame_lock:
            matches = [frame.copy() for ts, frame in self._frame_buffer if start_ts <= ts <= end_ts]
        return matches

    async def _handle_person_event(self, triggered_at: float, confidence: float) -> None:
        if self._person_task_lock.locked():
            return

        async with self._person_task_lock:
            pre_start = triggered_at - self.config.clip_pre_seconds
            pre_frames = self._frames_between(pre_start, triggered_at)
            await asyncio.sleep(self.config.clip_post_seconds)
            post_end = triggered_at + self.config.clip_post_seconds + 0.5
            post_frames = self._frames_between(triggered_at, post_end)
            clip_frames = pre_frames + post_frames

            if not clip_frames:
                raise RuntimeError("No frames available for person clip")

            clip_path = await asyncio.to_thread(self._write_clip, clip_frames, triggered_at)
            try:
                local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(triggered_at))
                await self._notifier.send_video(
                    clip_path,
                    f"Person detected at {local_time} (confidence={confidence:.2f})",
                )
            finally:
                clip_path.unlink(missing_ok=True)

    def _write_clip(self, frames: list[np.ndarray], triggered_at: float) -> Path:
        output = Path(f"/tmp/person_{int(triggered_at)}.mp4")
        frame_h, frame_w = frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output), fourcc, self._effective_fps, (frame_w, frame_h))
        if not writer.isOpened():
            raise RuntimeError("Failed to initialize video writer for person clip")

        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        return output


class CameraTrack(VideoStreamTrack):
    def __init__(self, camera_service: CameraService):
        super().__init__()
        self._camera_service = camera_service
        self._pts = 0
        self._time_base = Fraction(1, max(1, self._camera_service.stream_fps))

    async def recv(self) -> VideoFrame:
        frame = self._camera_service.latest_frame()
        await asyncio.sleep(float(self._time_base))
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

