import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from aiortc import RTCPeerConnection, RTCSessionDescription
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .detector import YoloPersonDetector
from .models import OfferRequest
from .notifier import TelegramNotifier
from .scenario_state import ScenarioStateStore
from .services import AudioInputService, CameraService, CameraTrack

peer_connections: set[RTCPeerConnection] = set()
camera_service: CameraService | None = None
audio_service: AudioInputService | None = None
scenario_store: ScenarioStateStore | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global camera_service, audio_service, scenario_store
    config = load_config()
    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    detector = YoloPersonDetector(
        config.yolo_model_path,
        config.yolo_input_size,
        config.person_confidence_threshold,
    )
    scenario_store = ScenarioStateStore(config.scenario_state_db_path)
    camera_service = CameraService(
        config,
        notifier,
        asyncio.get_running_loop(),
        detector,
        scenario_store,
    )
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
        if scenario_store is not None:
            scenario_store.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


backend_dir = Path(__file__).resolve().parent.parent
build_client_dir = backend_dir.parent / "build" / "client"
app.mount("/assets", StaticFiles(directory=build_client_dir / "assets"), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = build_client_dir / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(build_client_dir / "index.html")

