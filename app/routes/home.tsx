import type { Route } from "./+types/home";
import { useState, useEffect } from "react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "CCTV Viewer" },
    { name: "description", content: "Simple and clean CCTV viewer." },
  ];
}

export default function Home() {
  const [isOn, setIsOn] = useState(false);
  const [loading, setLoading] = useState(true);

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
          <>
            <img 
              src="/api/video_feed" 
              alt="Live CCTV Feed" 
              className="w-full h-full object-contain"
            />
            <audio src="/api/audio_feed" autoPlay hidden />
          </>
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
