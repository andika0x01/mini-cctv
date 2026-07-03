import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Graceful shutdown: ensure ffmpeg processes are killed
    await close_camera()

app = FastAPI(lifespan=lifespan)

# Allow CORS so the frontend can hit the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state for camera
class CameraState:
    def __init__(self):
        self.is_on = False
        self.process = None
        self.audio_process = None
        self.audio_task = None

state = CameraState()
active_websockets = set()

async def audio_broadcaster():
    try:
        while state.is_on:
            if state.audio_process is None:
                await asyncio.sleep(0.1)
                continue
                
            chunk = await state.audio_process.stdout.read(2048)
            if not chunk:
                await asyncio.sleep(0.1)
                continue
                
            # Broadcast to all connected clients
            dead_sockets = set()
            for ws in active_websockets:
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    dead_sockets.add(ws)
                    
            for ws in dead_sockets:
                active_websockets.discard(ws)
    except asyncio.CancelledError:
        pass

async def open_camera():
    if state.process is None:
        state.process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-f", "v4l2",
            "-input_format", "mjpeg",
            "-video_size", "1920x1080",
            "-framerate", "15",
            "-i", "/dev/video1",
            "-c:v", "copy",
            "-f", "mpjpeg",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
    if state.audio_process is None:
        state.audio_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-f", "alsa",
            "-channels", "1",
            "-i", "hw:1,0",
            "-af", "afftdn",           # Denoise filter
            "-c:a", "pcm_s16le",       # Raw 16-bit PCM
            "-ar", "16000",            # 16kHz sample rate (good for speech/cctv, saves bandwidth)
            "-f", "s16le",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
    if state.audio_task is None:
        state.audio_task = asyncio.create_task(audio_broadcaster())

async def close_camera():
    if state.process is not None:
        try:
            state.process.terminate()
            await state.process.wait()
        except Exception:
            pass
        state.process = None
        
    if state.audio_process is not None:
        try:
            state.audio_process.terminate()
            await state.audio_process.wait()
        except Exception:
            pass
        state.audio_process = None
        
    if state.audio_task is not None:
        state.audio_task.cancel()
        state.audio_task = None

async def generate_frames():
    try:
        while state.is_on:
            if state.process is None:
                await asyncio.sleep(0.1)
                continue
                
            chunk = await state.process.stdout.read(8192)
            if not chunk:
                await asyncio.sleep(0.1)
                continue
                
            yield chunk
    except asyncio.CancelledError:
        pass

@app.get("/api/video_feed")
async def video_feed():
    # Wait up to 5 seconds for camera to turn on (useful for optimistic UI requests)
    for _ in range(50):
        if state.is_on and state.process is not None:
            break
        await asyncio.sleep(0.1)
        
    if not state.is_on:
        return {"error": "Camera is currently off."}
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=ffmpeg")

@app.websocket("/api/audio_ws")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        # Keep the connection alive, the broadcaster sends the data
        while True:
            # We must await something to keep the connection open and detect client disconnects
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.discard(websocket)
    except Exception:
        active_websockets.discard(websocket)

class ToggleRequest(BaseModel):
    is_on: bool

@app.post("/api/toggle")
async def toggle_camera(req: ToggleRequest):
    state.is_on = req.is_on
    if state.is_on:
        await open_camera()
    else:
        await close_camera()
    return {"status": "ok", "is_on": state.is_on}

@app.get("/api/status")
async def get_status():
    return {"is_on": state.is_on}
