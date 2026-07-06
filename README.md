## Mini CCTV

Always-on mini CCTV dengan:

- Ultra-low-latency live streaming via WebRTC (event-driven, tanpa artificial sleep)
- Realtime audio via WebRTC (ALSA input → aiortc MediaRelay)
- Camera ON/OFF toggle langsung dari browser (instan, WebRTC tetap connected)

## Architecture

- **Backend**: FastAPI + aiortc + OpenCV
  - Camera service always on dengan auto-reconnect
  - WebRTC signaling endpoint untuk browser clients
  - Event-driven frame delivery: `threading.Event` → `run_in_executor` → `CameraTrack.recv()`
  - Audio via ALSA MediaPlayer + MediaRelay
  - `/api/camera/toggle` — pause/resume kamera tanpa disconnect WebRTC
- **Frontend**: React Router single-page live viewer
  - Tombol **CAMERA ON/OFF** di pojok kanan atas (optimistic update)
  - Tombol **AUDIO ON/OFF** di pojok kiri atas
  - Status badge koneksi WebRTC
  - Auto-reconnect saat koneksi putus

## Environment variables

Copy `.env.example` ke `.env` dan sesuaikan:

```bash
cp .env.example .env
```

| Variable | Default | Keterangan |
|---|---|---|
| `CAMERA_DEVICE` | `/dev/video1` | Path device kamera |
| `CAMERA_WIDTH` | `960` | Resolusi horizontal |
| `CAMERA_HEIGHT` | `540` | Resolusi vertikal |
| `CAMERA_FPS` | `25` | Target FPS kamera |
| `STREAM_TARGET_FPS` | `18` | Target FPS output WebRTC |
| `CAMERA_OPEN_RETRY_MS` | `2000` | Jeda retry saat device belum tersedia |
| `CAMERA_READ_FAIL_THRESHOLD` | `20` | Gagal read beruntun sebelum auto-reconnect |
| `AUDIO_ENABLED` | `true` | Aktifkan audio realtime |
| `AUDIO_DEVICE` | `hw:1,0` | Input ALSA |
| `AUDIO_SAMPLE_RATE` | `16000` | Sample rate audio |
| `AUDIO_CHANNELS` | `1` | Jumlah channel audio |
| `ALSA_CONFIG_PATH` | `/usr/share/alsa/alsa.conf` | Path konfigurasi ALSA |
| `ALSA_CONFIG_DIR` | `/usr/share/alsa` | Direktori konfigurasi ALSA |

## Development

Install frontend dependencies:

```bash
npm install
```

Install backend dependencies (dengan `uv`):

```bash
cd backend
uv sync
```

Jalankan frontend + backend sekaligus:

```bash
npm run dev
```

## API Endpoints

| Method | Path | Keterangan |
|--------|------|------------|
| `POST` | `/api/webrtc/offer` | WebRTC SDP offer/answer signaling |
| `GET`  | `/api/status` | Runtime metrics |
| `POST` | `/api/camera/toggle` | Toggle kamera on/off — returns `{ paused: bool }` |

## Runtime status (`GET /api/status`)

```json
{
  "camera_running": true,
  "camera_connected": true,
  "camera_paused": false,
  "camera_fps_detected": 25,
  "camera_fps_estimate": 24.97,
  "camera_open_failures": 0,
  "camera_reconnects": 0,
  "camera_read_failures": 0,
  "stream_target_fps": 18,
  "last_frame_age_ms": 3.2,
  "uptime_seconds": 3600,
  "active_peers": 1,
  "audio_enabled": true,
  "audio_device": "hw:1,0"
}
```
