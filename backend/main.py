import asyncio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

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

state = CameraState()

async def open_camera():
    if state.process is None:
        state.process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "v4l2",
            "-input_format", "mjpeg",
            "-video_size", "1920x1080",
            "-framerate", "30",
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
            "-f", "alsa",
            "-channels", "1",
            "-i", "hw:1,0",
            "-c:a", "libmp3lame",
            "-f", "mp3",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )

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

async def generate_frames():
    while state.is_on:
        if state.process is None:
            await asyncio.sleep(0.1)
            continue
            
        chunk = await state.process.stdout.read(8192)
        if not chunk:
            # Subprocess might have died or ended
            await asyncio.sleep(0.1)
            continue
            
        yield chunk

async def generate_audio():
    while state.is_on:
        if state.audio_process is None:
            await asyncio.sleep(0.1)
            continue
            
        chunk = await state.audio_process.stdout.read(4096)
        if not chunk:
            await asyncio.sleep(0.1)
            continue
            
        yield chunk

@app.get("/api/video_feed")
async def video_feed():
    # Only stream if on
    if not state.is_on:
        return {"error": "Camera is currently off."}
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=ffmpeg")

@app.get("/api/audio_feed")
async def audio_feed():
    # Only stream if on
    if not state.is_on:
        return {"error": "Camera is currently off."}
    return StreamingResponse(generate_audio(), media_type="audio/mpeg")

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
