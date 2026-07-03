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
      <div className="absolute top-4 right-4 z-10">
        <button 
          onClick={toggleCamera}
          className={`px-4 py-2 rounded-md font-medium transition-colors border cursor-pointer bg-black/50 backdrop-blur-sm ${
            isOn 
              ? 'border-gray-600 text-white hover:bg-black/80' 
              : 'border-gray-600 text-white hover:bg-white hover:text-black'
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
              onTimeUpdate={(e) => {
                setCurrentTime(e.currentTarget.currentTime);
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
            <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-black via-black/80 to-transparent flex flex-col gap-4">
              <input 
                type="range" 
                min={seekMin} 
                max={seekMax} 
                step={1}
                value={currentTime} 
                onChange={(e) => {
                  if (videoRef.current) {
                    videoRef.current.currentTime = Number(e.target.value);
                  }
                }}
                className="w-full h-2 bg-gray-600 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between items-center text-sm font-mono text-white">
                <button onClick={() => videoRef.current && (videoRef.current.currentTime -= 60)} className="px-3 py-2 bg-white/20 rounded active:bg-white/40">-1m</button>
                <button onClick={() => videoRef.current && (videoRef.current.currentTime -= 10)} className="px-3 py-2 bg-white/20 rounded active:bg-white/40">-10s</button>
                
                <button onClick={() => {
                   if (videoRef.current && videoRef.current.seekable.length > 0) {
                     videoRef.current.currentTime = videoRef.current.seekable.end(0);
                     videoRef.current.play();
                   }
                }} className="px-4 py-2 bg-red-600 rounded-full font-bold flex items-center gap-2 shadow-lg active:scale-95 transition-transform">
                  <div className="w-2.5 h-2.5 bg-white rounded-full animate-pulse"></div> Live
                </button>
                
                <button onClick={() => videoRef.current && (videoRef.current.currentTime += 10)} className="px-3 py-2 bg-white/20 rounded active:bg-white/40">+10s</button>
                <button onClick={() => videoRef.current && (videoRef.current.currentTime += 60)} className="px-3 py-2 bg-white/20 rounded active:bg-white/40">+1m</button>
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
