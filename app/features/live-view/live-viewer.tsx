import { useWebRtcStream } from "./use-webrtc-stream";

export function LiveViewer() {
  const { audioEnabled, connectionState, errorMessage, toggleAudio, videoRef } = useWebRtcStream();

  const statusBadgeClass =
    connectionState === "connected"
      ? "bg-emerald-500/20 text-emerald-300 border-emerald-400/40"
      : "bg-yellow-500/20 text-yellow-200 border-yellow-300/40";

  const statusLabel = connectionState === "connected" ? "LIVE (WebRTC)" : "CONNECTING";

  return (
    <main className="relative w-screen h-screen bg-black overflow-hidden font-sans">
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

