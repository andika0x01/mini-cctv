import { useWebRtcStream } from "./use-webrtc-stream";

export function LiveViewer() {
  const { audioEnabled, cameraActive, connectionState, errorMessage, toggleAudio, toggleCamera, videoRef } =
    useWebRtcStream();

  const statusBadgeClass =
    connectionState === "connected"
      ? "bg-emerald-500/20 text-emerald-300 border-emerald-400/40"
      : "bg-yellow-500/20 text-yellow-200 border-yellow-300/40";

  const statusLabel = connectionState === "connected" ? "LIVE (WebRTC)" : "CONNECTING";

  return (
    <main className="relative w-screen h-screen bg-black overflow-hidden font-sans">
      {/* Top-left: status + audio */}
      <div className="absolute top-5 left-5 z-10 flex items-center gap-3">
        <div className={`px-3 py-1.5 rounded-full border text-xs font-semibold tracking-wide ${statusBadgeClass}`}>
          {statusLabel}
        </div>
        <button
          type="button"
          onClick={() => {
            void toggleAudio();
          }}
          className="px-3 py-1.5 rounded-full border border-slate-300/40 bg-slate-700/40 text-xs font-semibold tracking-wide text-slate-100"
        >
          {audioEnabled ? "AUDIO ON" : "AUDIO OFF"}
        </button>
      </div>

      {/* Top-right: camera toggle */}
      <div className="absolute top-5 right-5 z-10">
        <button
          type="button"
          id="camera-toggle-btn"
          onClick={() => {
            void toggleCamera();
          }}
          className={`
            flex items-center gap-2 px-4 py-2 rounded-full border text-xs font-bold tracking-widest
            transition-all duration-150
            ${
              cameraActive
                ? "bg-red-500/20 border-red-400/50 text-red-200 hover:bg-red-500/35 hover:border-red-400/80"
                : "bg-emerald-500/20 border-emerald-400/50 text-emerald-200 hover:bg-emerald-500/35 hover:border-emerald-400/80"
            }
          `}
        >
          {/* indicator dot */}
          <span
            className={`w-2 h-2 rounded-full ${cameraActive ? "bg-red-400" : "bg-slate-500"}`}
          />
          {cameraActive ? "CAMERA ON" : "CAMERA OFF"}
        </button>
      </div>

      {errorMessage ? (
        <div className="absolute top-16 left-5 z-10 rounded-md border border-red-400/40 bg-red-500/20 px-3 py-2 text-xs text-red-100">
          {errorMessage}
        </div>
      ) : null}

      <div className="w-full h-full flex items-center justify-center">
        <video
          ref={videoRef}
          playsInline
          autoPlay
          muted={!audioEnabled}
          className="w-full h-full object-contain"
          onLoadedMetadata={() => {
            if (videoRef.current) {
              void videoRef.current.play();
            }
          }}
        />
      </div>
    </main>
  );
}
