import { useCallback, useEffect, useRef, useState } from "react";

import type { ConnectionState } from "./types";

export function useWebRtcStream() {
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [cameraActive, setCameraActive] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const reconnectAttemptRef = useRef(0);
  const audioEnabledRef = useRef(audioEnabled);
  const cameraTogglePendingRef = useRef(false);

  useEffect(() => {
    audioEnabledRef.current = audioEnabled;
    if (!videoRef.current) {
      return;
    }
    videoRef.current.muted = !audioEnabled;
    videoRef.current.volume = audioEnabled ? 1 : 0;
  }, [audioEnabled]);

  // Sync initial camera state from server
  useEffect(() => {
    fetch("/api/status")
      .then((r) => r.json())
      .then((data: { camera_paused?: boolean }) => {
        if (typeof data.camera_paused === "boolean") {
          setCameraActive(!data.camera_paused);
        }
      })
      .catch(() => {
        // ignore — server might not be ready yet
      });
  }, []);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer: number | null = null;

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const scheduleReconnect = () => {
      clearReconnectTimer();
      const delayMs = Math.min(10_000, 2_000 * 2 ** reconnectAttemptRef.current);
      reconnectAttemptRef.current += 1;
      reconnectTimer = window.setTimeout(() => {
        if (!stopped) {
          void connect();
        }
      }, delayMs);
    };

    const connect = async () => {
      setConnectionState("connecting");
      setErrorMessage(null);

      if (peerRef.current) {
        peerRef.current.close();
        peerRef.current = null;
      }

      const peer = new RTCPeerConnection();
      peerRef.current = peer;
      peer.addTransceiver("video", { direction: "recvonly" });
      peer.addTransceiver("audio", { direction: "recvonly" });

      peer.ontrack = (event) => {
        if (!videoRef.current) return;
        const [stream] = event.streams;
        const [track] = stream.getVideoTracks();
        if (track) {
          track.contentHint = "motion";
        }
        videoRef.current.srcObject = stream;
        videoRef.current.muted = !audioEnabledRef.current;
        void videoRef.current.play();
      };

      peer.onconnectionstatechange = () => {
        if (peer.connectionState === "connected") {
          setConnectionState("connected");
          reconnectAttemptRef.current = 0;
          clearReconnectTimer();
          return;
        }
        if (peer.connectionState === "failed") {
          setConnectionState("failed");
          setErrorMessage("Koneksi stream gagal. Mencoba reconnect...");
          scheduleReconnect();
          return;
        }
        if (peer.connectionState === "disconnected" || peer.connectionState === "closed") {
          setConnectionState("disconnected");
          setErrorMessage("Koneksi stream terputus. Mencoba reconnect...");
          scheduleReconnect();
        }
      };

      try {
        const offer = await peer.createOffer();
        await peer.setLocalDescription(offer);
        const response = await fetch("/api/webrtc/offer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
        });
        if (!response.ok) {
          throw new Error(`Signaling failed with status ${response.status}`);
        }
        const answer: RTCSessionDescriptionInit = await response.json();
        await peer.setRemoteDescription(answer);
      } catch {
        setConnectionState("failed");
        setErrorMessage("Gagal membangun koneksi stream. Mencoba reconnect...");
        scheduleReconnect();
      }
    };

    void connect();

    return () => {
      stopped = true;
      clearReconnectTimer();
      if (peerRef.current) {
        peerRef.current.close();
        peerRef.current = null;
      }
    };
  }, []);

  const toggleAudio = async () => {
    const nextEnabled = !audioEnabledRef.current;
    setAudioEnabled(nextEnabled);
    if (videoRef.current) {
      await videoRef.current.play();
    }
  };

  const toggleCamera = useCallback(async () => {
    if (cameraTogglePendingRef.current) return;
    cameraTogglePendingRef.current = true;

    // Optimistic update — instan di UI, lalu sinkronisasi ke server
    const optimisticNext = !cameraActive;
    setCameraActive(optimisticNext);

    try {
      const res = await fetch("/api/camera/toggle", { method: "POST" });
      if (res.ok) {
        const data = (await res.json()) as { paused: boolean };
        // Reconcile dengan state server yang aktual
        setCameraActive(!data.paused);
      } else {
        // Rollback kalau server error
        setCameraActive(!optimisticNext);
      }
    } catch {
      // Rollback kalau network error
      setCameraActive(!optimisticNext);
    } finally {
      cameraTogglePendingRef.current = false;
    }
  }, [cameraActive]);

  return {
    audioEnabled,
    cameraActive,
    connectionState,
    errorMessage,
    toggleAudio,
    toggleCamera,
    videoRef,
  };
}
