import type { Route } from "./+types/home";
import { useState, useEffect, useRef } from "react";
import Hls from "hls.js";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "CCTV Viewer" },
    { name: "description", content: "Simple and clean CCTV viewer." },
  ];
}

export default function Home() {
  const [isOn, setIsOn] = useState(false);
  const [loading, setLoading] = useState(true);
  
  // Custom Controls State
  const [seekMin, setSeekMin] = useState(0);
  const [seekMax, setSeekMax] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isLiveLocked, setIsLiveLocked] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [isPlaying, setIsPlaying] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  useEffect(() => {
    fetch("/api/status")
      .then(res => res.json())
      .then(data => {
        setIsOn(data.is_on);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to fetch status", err);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    let isCancelled = false;

    const initVideo = async () => {
      if (!isOn || !videoRef.current) return;
      const video = videoRef.current;
      const src = "/data/live.m3u8";

      // Wait until ffmpeg creates the live.m3u8 file
      while (!isCancelled) {
        try {
          const res = await fetch(src, { method: "HEAD", cache: "no-store" });
          if (res.ok) break;
        } catch (e) {}
        await new Promise(r => setTimeout(r, 500));
      }
      
      if (isCancelled) return;

      if (Hls.isSupported()) {
        const hls = new Hls({
          liveSyncDurationCount: 1, 
          liveMaxLatencyDurationCount: 2,
          maxLiveSyncPlaybackRate: 2,
          enableWorker: true,
          lowLatencyMode: true,
        });
        hlsRef.current = hls;
        
        hls.loadSource(src);
        hls.attachMedia(video);
        
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().catch(console.error);
        });
        
        hls.on(Hls.Events.ERROR, function (event, data) {
          if (data.fatal) {
            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                console.error("fatal network error encountered, try to recover");
                hls.startLoad();
                break;
              case Hls.ErrorTypes.MEDIA_ERROR:
                console.error("fatal media error encountered, try to recover");
                hls.recoverMediaError();
                break;
              default:
                hls.destroy();
                break;
            }
          }
        });
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = src;
        video.addEventListener("loadedmetadata", () => {
          video.play().catch(console.error);
        });
      }
    };

    initVideo();

    return () => {
      isCancelled = true;
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [isOn]);

  const toggleCamera = async () => {
    const newState = !isOn;
    setIsOn(newState); 
    try {
      const res = await fetch("/api/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_on: newState })
      });
      const data = await res.json();
      // Ensure state matches backend reality
      setIsOn(data.is_on);
    } catch (err) {
      console.error("Failed to toggle camera", err);
      setIsOn(!newState); 
    }
  };

  if (loading) {
    return (
      <div className="w-screen h-screen bg-black text-white flex items-center justify-center font-sans">
        <p className="text-xl">Loading...</p>
      </div>
    );
  }

  return (
    <div className="relative w-screen h-screen bg-black overflow-hidden font-sans">
      
      {/* Floating Controls */}
      <div className="absolute top-6 left-6 z-10 flex items-center gap-3">
        {isOn && (
          <button 
            onClick={() => {
              const newLockedState = !isLiveLocked;
              setIsLiveLocked(newLockedState);
              if (newLockedState && videoRef.current && videoRef.current.seekable.length > 0) {
                videoRef.current.currentTime = videoRef.current.seekable.end(0);
                videoRef.current.play();
              }
            }}
            className={`px-5 py-2.5 rounded-full font-bold text-sm tracking-wide shadow-lg transition-all border cursor-pointer backdrop-blur-md flex items-center gap-2 ${
              isLiveLocked 
                ? 'border-red-500/50 bg-red-600/20 text-red-400' 
                : 'border-white/10 bg-white/10 text-gray-300 hover:text-white'
            }`}
          >
            {isLiveLocked ? (
              <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(239,68,68,0.8)]"></div>
            ) : (
              <svg className="w-3.5 h-3.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
            )}
            {isLiveLocked ? 'LIVE LOCKED' : 'UNLOCK'}
          </button>
        )}
        <button 
          onClick={toggleCamera}
          className={`px-5 py-2.5 rounded-full font-semibold text-sm shadow-lg transition-colors border cursor-pointer backdrop-blur-md ${
            isOn 
              ? 'border-white/10 bg-black/60 text-white hover:bg-black/80' 
              : 'border-white/10 bg-white/10 text-white hover:bg-white/20'
          }`}
        >
          {isOn ? 'Turn Off' : 'Turn On'}
        </button>
      </div>

      {/* Video Feed */}
      <div className="w-full h-full flex items-center justify-center">
        {isOn ? (
          <div className="relative w-full h-full flex flex-col group">
            <video 
              ref={videoRef}
              playsInline
              autoPlay
              muted={false}
              className="w-full h-full object-contain"
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onTimeUpdate={(e) => {
                if (!isDragging) {
                  setCurrentTime(e.currentTarget.currentTime);
                }
                if (e.currentTarget.seekable.length > 0) {
                  setSeekMin(e.currentTarget.seekable.start(0));
                  setSeekMax(e.currentTarget.seekable.end(0));
                }
              }}
              onClick={() => {
                if (videoRef.current) {
                  if (videoRef.current.paused) videoRef.current.play();
                  else videoRef.current.pause();
                }
              }}
            />
            {/* Custom Mobile-Friendly Controls */}
            <div className={`absolute bottom-0 left-0 right-0 px-6 pb-8 pt-24 bg-gradient-to-t from-black via-black/80 to-transparent flex flex-col gap-6 transition-all duration-500 ease-out ${isLiveLocked ? 'opacity-0 translate-y-8 pointer-events-none' : 'opacity-100 translate-y-0'}`}>
              <div className="w-full relative group">
                <input 
                  type="range" 
                  min={seekMin} 
                  max={seekMax} 
                  step={1}
                  value={currentTime} 
                  onPointerDown={() => setIsDragging(true)}
                  onPointerUp={(e) => {
                    setIsDragging(false);
                    if (videoRef.current && !isLiveLocked) {
                      videoRef.current.currentTime = Number(e.currentTarget.value);
                    }
                  }}
                  onChange={(e) => {
                    if (isDragging) {
                      setCurrentTime(Number(e.target.value));
                    }
                  }}
                  disabled={isLiveLocked}
                  className="w-full h-2 bg-gray-600/80 rounded-full appearance-none cursor-pointer accent-white hover:h-3 transition-all outline-none"
                />
              </div>
              <div className="flex justify-center items-center gap-4 text-sm font-semibold text-white">
                <button disabled={isLiveLocked} onClick={() => {
                  if (videoRef.current) {
                    let t = videoRef.current.currentTime - 60;
                    if (videoRef.current.seekable.length > 0) t = Math.max(videoRef.current.seekable.start(0), t);
                    videoRef.current.currentTime = t;
                  }
                }} className="w-12 h-12 flex items-center justify-center bg-white/10 hover:bg-white/20 border border-white/10 backdrop-blur-md rounded-full active:scale-95 transition-all">-1m</button>
                
                <button disabled={isLiveLocked} onClick={() => {
                  if (videoRef.current) {
                    let t = videoRef.current.currentTime - 10;
                    if (videoRef.current.seekable.length > 0) t = Math.max(videoRef.current.seekable.start(0), t);
                    videoRef.current.currentTime = t;
                  }
                }} className="w-12 h-12 flex items-center justify-center bg-white/10 hover:bg-white/20 border border-white/10 backdrop-blur-md rounded-full active:scale-95 transition-all">-10s</button>
                
                <button onClick={() => {
                   if (videoRef.current) {
                     if (videoRef.current.paused) videoRef.current.play();
                     else videoRef.current.pause();
                   }
                }} className="w-14 h-14 flex items-center justify-center bg-white text-black hover:bg-gray-200 shadow-xl rounded-full active:scale-95 transition-all mx-2">
                  {isPlaying ? (
                    <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM7 8a1 1 0 012 0v4a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" /></svg>
                  ) : (
                    <svg className="w-6 h-6 ml-1" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clipRule="evenodd" /></svg>
                  )}
                </button>
                
                <button disabled={isLiveLocked} onClick={() => {
                  if (videoRef.current) {
                    let t = videoRef.current.currentTime + 10;
                    if (videoRef.current.seekable.length > 0) t = Math.min(videoRef.current.seekable.end(0), t);
                    videoRef.current.currentTime = t;
                  }
                }} className="w-12 h-12 flex items-center justify-center bg-white/10 hover:bg-white/20 border border-white/10 backdrop-blur-md rounded-full active:scale-95 transition-all">+10s</button>
                
                <button disabled={isLiveLocked} onClick={() => {
                  if (videoRef.current) {
                    let t = videoRef.current.currentTime + 60;
                    if (videoRef.current.seekable.length > 0) t = Math.min(videoRef.current.seekable.end(0), t);
                    videoRef.current.currentTime = t;
                  }
                }} className="w-12 h-12 flex items-center justify-center bg-white/10 hover:bg-white/20 border border-white/10 backdrop-blur-md rounded-full active:scale-95 transition-all">+1m</button>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center">
            <svg className="w-16 h-16 mb-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            <p className="text-xl font-light tracking-wide text-gray-500">Camera is turned off</p>
          </div>
        )}
      </div>

    </div>
  );
}
