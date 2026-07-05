## Mini CCTV

Always-on mini CCTV with:

- Ultra-low-latency live streaming via WebRTC (sub-second target)
- 24/7 server-side YOLO person detection
- Auto send short person-event clips to Telegram

## Architecture (current)

- **Backend**: FastAPI + aiortc + OpenCV
  - Camera service is always on
  - WebRTC signaling endpoint for browser clients
  - YOLO person detection loop with cooldown + clip creation
  - Telegram delivery on person detection events
- **Frontend**: React Router single-page live viewer
  - No recording timeline
  - No lock/unlock controls
  - No camera ON/OFF toggle

## Required environment variables

Set these before starting backend:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional tuning variables:

- `CAMERA_DEVICE` (default: `/dev/video1`)
- `CAMERA_WIDTH` (default: `960`)
- `CAMERA_HEIGHT` (default: `540`)
- `CAMERA_FPS` (default: `25`)
- `STREAM_TARGET_FPS` (default: `18`, output WebRTC target)
- `CAMERA_OPEN_RETRY_MS` (default: `2000`, jeda retry buka kamera saat device belum tersedia)
- `CAMERA_READ_FAIL_THRESHOLD` (default: `20`, jumlah gagal read beruntun sebelum auto reconnect kamera)
- `YOLO_MODEL_PATH` (default: `models/yolo11n.onnx` jika proses dijalankan dari folder `backend`)
- `YOLO_INPUT_SIZE` (default: `320`, untuk respons deteksi lebih cepat)
- `PERSON_CONFIDENCE_THRESHOLD` (default: `0.45`)
- `PERSON_DETECTION_INTERVAL_MS` (default: `250`)
- `PERSON_DETECTION_MAX_WIDTH` (default: `416`, frame detector akan di-resize agar lebih ringan)
- `PERSON_REPORT_COOLDOWN_SECONDS` (default: `20`, dipakai sebagai durasi reset scenario: report baru dikirim lagi jika person **hilang** minimal selama durasi ini)
- `SCENARIO_STATE_DB_PATH` (default: `backend/data/scenario_state.sqlite3`, sqlite state untuk deduplikasi report antar restart)
- `AUDIO_ENABLED` (default: `true`)
- `AUDIO_DEVICE` (default: `hw:1,0`, input ALSA)
- `AUDIO_SAMPLE_RATE` (default: `16000`)
- `AUDIO_CHANNELS` (default: `1`)
- `ALSA_CONFIG_PATH` (default: `/usr/share/alsa/alsa.conf`)
- `ALSA_CONFIG_DIR` (default: `/usr/share/alsa`)
- `CLIP_PRE_SECONDS` (default: `3`)
- `CLIP_POST_SECONDS` (default: `7`)

Contoh profil balanced untuk STB low-resource (S905X 2GB):

```env
CAMERA_WIDTH=960
CAMERA_HEIGHT=540
CAMERA_FPS=25
STREAM_TARGET_FPS=18
YOLO_INPUT_SIZE=320
PERSON_DETECTION_MAX_WIDTH=416
PERSON_DETECTION_INTERVAL_MS=250
AUDIO_ENABLED=true
AUDIO_DEVICE=hw:1,0
```

> Model YOLO ONNX harus tersedia di `backend/models/yolo11n.onnx` (atau set `YOLO_MODEL_PATH`).
> Contoh download:
> `mkdir -p backend/models && curl -L -o backend/models/yolo11n.onnx https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n.onnx`

## Development

Install frontend dependencies:

```bash
npm install
```

Install backend dependencies (with `uv`):

```bash
cd backend
uv sync
```

Run both frontend + backend:

```bash
npm run dev
```

## Runtime status for tuning

Backend exposes `GET /api/status` with runtime metrics useful for profiling/tuning:
- `camera_fps_detected`, `camera_fps_estimate`
- `camera_connected`, `camera_open_failures`, `camera_reconnects`, `camera_read_failures`
- `stream_target_fps`
- `detection_infer_avg_ms`, `detection_infer_peak_ms`
- `detection_queue_size`, `detection_dropped_frames`
- `last_frame_age_ms`, `active_peers`
- `audio_enabled`
- `audio_device`
