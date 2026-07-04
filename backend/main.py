import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import ctypes
import os
import signal
import sys
import subprocess
import time
import glob
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

async def cleanup_loop():
    while True:
        try:
            now = time.time()
            # Clean up orphaned live HLS segments older than 12 hours
            for f in glob.glob("../.cctv-data/live_seg_*.ts"):
                if os.stat(f).st_mtime < now - 43200:
                    os.remove(f)
        except Exception:
            pass
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(cleanup_loop())
    yield
    cleanup_task.cancel()
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

state = CameraState()

def set_pdeathsig():
    try:
        libc = ctypes.CDLL("libc.so.6")
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:
        pass

async def open_camera():
    if state.process is None:
        # Restore auto-exposure so the camera can adjust brightness in low light
        try:
            subprocess.run(["v4l2-ctl", "-d", "/dev/video1", "-c", "auto_exposure=3"], check=False)
        except Exception as e:
            print(f"Warning: Failed to set v4l2-ctl exposure: {e}", file=sys.stderr)
            
        # Ensure data directory exists
        data_dir = "../.cctv-data"
        os.makedirs(data_dir, exist_ok=True)
                
        state.process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "15", "-i", "/dev/video1",
            "-f", "alsa", "-channels", "1", "-i", "hw:1,0",
            # Shared filter graph: timestamp overlay
            "-filter_complex",
            "[0:v]scale=854:480,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf:text='%{localtime} WIB':fontcolor=white:fontsize=24:x=w-tw-15:y=15:box=1:boxcolor=black@0.5:boxborderw=5[vout]",
            "-map", "[vout]", "-map", "1:a",
            # Video and audio codec
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "28", "-pix_fmt", "yuv420p",
            "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "64k",
            # Unified Output: Live HLS with 12-hour DVR window
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "43200",
            "-hls_flags", "append_list+delete_segments+independent_segments+discont_start+split_by_time",
            "-strftime", "1",
            "-hls_segment_filename", f"{data_dir}/live_seg_%Y%m%d_%H%M%S.ts",
            f"{data_dir}/live.m3u8",
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            preexec_fn=set_pdeathsig
        )

async def close_camera():
    if state.process is not None:
        try:
            state.process.kill()
            await state.process.wait()
        except Exception:
            pass
        state.process = None

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

# Mount CCTV data directory for HLS
os.makedirs("../.cctv-data", exist_ok=True)
app.mount("/data", StaticFiles(directory="../.cctv-data"), name="data")

# Mount React SPA build directory (Must be placed AFTER all API routes)
app.mount("/assets", StaticFiles(directory="../build/client/assets"), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = f"../build/client/{full_path}"
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse("../build/client/index.html")
