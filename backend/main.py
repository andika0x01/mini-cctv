import asyncio
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from threading import Event, Lock, Thread

import cv2
import httpx
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRelay
from av import VideoFrame
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def load_env_files() -> None:
    backend_dir = Path(__file__).resolve().parent
    _load_env_file(backend_dir / ".env")
    _load_env_file(backend_dir.parent / ".env")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be > 0")
    return parsed


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class Config:
    camera_device: str
    camera_width: int
    camera_height: int
    camera_fps: int
    stream_target_fps: int
    camera_open_retry_ms: int
    camera_read_fail_threshold: int
    person_confidence_threshold: float
    person_detection_interval_ms: int
    person_detection_max_width: int
    person_report_cooldown_seconds: int
    yolo_model_path: str
    yolo_input_size: int
    audio_enabled: bool
    audio_device: str
    audio_sample_rate: int
    audio_channels: int
    alsa_config_path: str
    alsa_config_dir: str
    clip_pre_seconds: int
    clip_post_seconds: int
    telegram_bot_token: str
    telegram_chat_id: str


def load_config() -> Config:
    load_env_files()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    default_model = str(Path(__file__).resolve().parent / "models" / "yolo11n.onnx")
    person_detection_max_width = env_int("PERSON_DETECTION_MAX_WIDTH", 416)
    yolo_input_size = min(env_int("YOLO_INPUT_SIZE", 320), person_detection_max_width)
    return Config(
        camera_device=os.getenv("CAMERA_DEVICE", "/dev/video1"),
        camera_width=env_int("CAMERA_WIDTH", 960),
        camera_height=env_int("CAMERA_HEIGHT", 540),
        camera_fps=env_int("CAMERA_FPS", 25),
        stream_target_fps=env_int("STREAM_TARGET_FPS", 18),
        camera_open_retry_ms=env_int("CAMERA_OPEN_RETRY_MS", 2000),
        camera_read_fail_threshold=env_int("CAMERA_READ_FAIL_THRESHOLD", 20),
        person_confidence_threshold=float(os.getenv("PERSON_CONFIDENCE_THRESHOLD", "0.45")),
        person_detection_interval_ms=env_int("PERSON_DETECTION_INTERVAL_MS", 250),
        person_detection_max_width=person_detection_max_width,
        person_report_cooldown_seconds=env_int("PERSON_REPORT_COOLDOWN_SECONDS", 20),
        yolo_model_path=os.getenv("YOLO_MODEL_PATH", default_model),
        yolo_input_size=yolo_input_size,
        audio_enabled=env_bool("AUDIO_ENABLED", True),
        audio_device=os.getenv("AUDIO_DEVICE", "hw:1,0"),
        audio_sample_rate=env_int("AUDIO_SAMPLE_RATE", 16000),
        audio_channels=env_int("AUDIO_CHANNELS", 1),
        alsa_config_path=os.getenv("ALSA_CONFIG_PATH", "/usr/share/alsa/alsa.conf"),
        alsa_config_dir=os.getenv("ALSA_CONFIG_DIR", "/usr/share/alsa"),
        clip_pre_seconds=env_int("CLIP_PRE_SECONDS", 3),
        clip_post_seconds=env_int("CLIP_POST_SECONDS", 7),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
    )


class OfferRequest(BaseModel):
    sdp: str
    type: str


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def send_video(self, file_path: Path, caption: str) -> None:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendVideo"
        with file_path.open("rb") as video_file:
            files = {
                "video": (file_path.name, video_file, "video/mp4"),
            }
            data = {
                "chat_id": self._chat_id,
                "caption": caption,
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, data=data, files=files)
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram send failed: {response.status_code} {response.text}")


class YoloPersonDetector:
    def __init__(self, model_path: str, input_size: int, confidence_threshold: float):
        if not 0 < confidence_threshold <= 1:
            raise ValueError("PERSON_CONFIDENCE_THRESHOLD must be between 0 and 1")
        model = Path(model_path)
        if not model.exists():
            raise RuntimeError(
                f"YOLO model not found: {model_path}. "
                "Place yolov8n.onnx at backend/models/yolov8n.onnx or set YOLO_MODEL_PATH."
            )
        self._input_size = input_size
        self._confidence_threshold = confidence_threshold
        self._net = cv2.dnn.readNetFromONNX(str(model))

    def detect_person_confidence(self, frame: np.ndarray) -> float:
        blob = cv2.dnn.blobFromImage(
            frame,
            scalefactor=1 / 255.0,
            size=(self._input_size, self._input_size),
            swapRB=True,
            crop=False,
        )
        self._net.setInput(blob)
        output = self._net.forward()
        predictions = np.squeeze(output)

        if predictions.ndim == 1:
            return 0.0
        if predictions.ndim == 2 and predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T
        if predictions.ndim != 2 or predictions.shape[1] < 5:
            return 0.0

        # YOLOv8 ONNX: [x, y, w, h, class0, class1, ...]
        # YOLOv5-like ONNX: [x, y, w, h, obj, class0, class1, ...]
        if predictions.shape[1] >= 85:
            person_scores = predictions[:, 4] * predictions[:, 5]
        else:
            person_scores = predictions[:, 4]
        if person_scores.size == 0:
            return 0.0
        return float(np.max(person_scores))

    def has_person(self, frame: np.ndarray) -> tuple[bool, float]:
        confidence = self.detect_person_confidence(frame)
        return confidence >= self._confidence_threshold, confidence


class CameraService:
    def __init__(
        self,
        config: Config,
        notifier: TelegramNotifier,
        loop: asyncio.AbstractEventLoop,
        detector: YoloPersonDetector,
    ):
        self.config = config
        self._notifier = notifier
        self._loop = loop
        self._detector = detector
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
        self._last_person_report_at = 0.0
        self._person_task_lock = asyncio.Lock()
        self._black_frame = np.zeros((initial_height, initial_width, 3), dtype=np.uint8)

    @property
    def is_running(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    @property
    def effective_fps(self) -> int:
        return self._effective_fps

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
                continue
            if timestamp - self._last_person_report_at < self.config.person_report_cooldown_seconds:
                continue

            self._last_person_report_at = timestamp
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


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

peer_connections: set[RTCPeerConnection] = set()
camera_service: CameraService | None = None
audio_service: AudioInputService | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global camera_service, audio_service
    config = load_config()
    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    detector = YoloPersonDetector(
        config.yolo_model_path,
        config.yolo_input_size,
        config.person_confidence_threshold,
    )
    camera_service = CameraService(config, notifier, asyncio.get_running_loop(), detector)
    audio_service = AudioInputService(config)
    audio_service.start()
    camera_service.start()
    try:
        yield
    finally:
        await asyncio.gather(*(peer.close() for peer in list(peer_connections)), return_exceptions=True)
        peer_connections.clear()
        if camera_service is not None:
            camera_service.stop()
        if audio_service is not None:
            audio_service.stop()


app.router.lifespan_context = lifespan
@app.post("/api/webrtc/offer")
async def create_webrtc_offer(offer: OfferRequest):
    if camera_service is None or not camera_service.is_running:
        raise HTTPException(status_code=503, detail="Camera service is not running")

    peer = RTCPeerConnection()
    peer_connections.add(peer)

    @peer.on("connectionstatechange")
    async def on_connectionstatechange():
        if peer.connectionState in {"failed", "closed", "disconnected"}:
            await peer.close()
            peer_connections.discard(peer)

    try:
        peer.addTrack(CameraTrack(camera_service))
        wants_audio = "m=audio " in offer.sdp
        if wants_audio and audio_service is not None and audio_service.enabled:
            peer.addTrack(audio_service.subscribe_track())
        await peer.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
        answer = await peer.createAnswer()
        await peer.setLocalDescription(answer)
    except Exception as exc:
        await peer.close()
        peer_connections.discard(peer)
        raise HTTPException(status_code=500, detail=f"WebRTC negotiation failed: {exc}") from exc

    if peer.localDescription is None:
        raise HTTPException(status_code=500, detail="Failed to create local WebRTC description")
    return {"sdp": peer.localDescription.sdp, "type": peer.localDescription.type}


@app.get("/api/status")
async def status():
    if camera_service is None:
        return {"camera_running": False, "active_peers": len(peer_connections)}
    metrics = camera_service.metrics_snapshot()
    metrics["active_peers"] = len(peer_connections)
    metrics["audio_enabled"] = audio_service.enabled if audio_service is not None else False
    metrics["audio_device"] = audio_service.active_device if audio_service is not None else None
    return metrics


app.mount("/assets", StaticFiles(directory="../build/client/assets"), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = Path("../build/client") / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse("../build/client/index.html")
