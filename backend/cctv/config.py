import os
from dataclasses import dataclass
from pathlib import Path


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
    backend_dir = Path(__file__).resolve().parent.parent
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
    scenario_state_db_path: str
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

    backend_dir = Path(__file__).resolve().parent.parent
    default_model = str(backend_dir / "models" / "yolo11n.onnx")
    person_detection_max_width = env_int("PERSON_DETECTION_MAX_WIDTH", 416)
    yolo_input_size = min(env_int("YOLO_INPUT_SIZE", 320), person_detection_max_width)
    default_state_db = str(backend_dir / "data" / "scenario_state.sqlite3")
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
        scenario_state_db_path=os.getenv("SCENARIO_STATE_DB_PATH", default_state_db),
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

