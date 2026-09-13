import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, type AppStateStatus } from "react-native";

import { desktopSocketUrl, rtcIce, rtcOffer } from "@/lib/api";
import type { Connection, DisplayInfo } from "@/lib/types";

type DesktopOptions = {
  onError?: (message: string) => void;
  webrtc?: boolean;
};

function toDataUri(data: ArrayBuffer): string {
  const bytes = new Uint8Array(data);
  let binary = "";
  const chunk = 0x2000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return `data:image/jpeg;base64,${btoa(binary)}`;
}

function hasRtcPeerConnection(): boolean {
  return typeof (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection === "function";
}

function pumpStream(stream: MediaStream, onFrame: (uri: string) => void): () => void {
  if (typeof document === "undefined") return () => {};
  const video = document.createElement("video");
  video.autoplay = true;
  video.muted = true;
  video.setAttribute("playsinline", "true");
  video.srcObject = stream;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  let timer: ReturnType<typeof setInterval> | undefined;
  const tick = () => {
    if (!ctx || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0);
    onFrame(canvas.toDataURL("image/jpeg", 0.72));
  };
  void video.play().then(() => {
    timer = setInterval(tick, 120);
  }).catch(() => {});
  return () => {
    if (timer) clearInterval(timer);
    video.srcObject = null;
    for (const track of stream.getTracks()) track.stop();
  };
}

export function useDesktop(connection: Connection | null, active: boolean, options: DesktopOptions = {}) {
  const { webrtc = false } = options;
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef(options);
  handlersRef.current = options;
  const rtcActiveRef = useRef(false);
  const [frame, setFrame] = useState<string | null>(null);
  const [viewOnly, setViewOnly] = useState(true);
  const [displays, setDisplays] = useState<DisplayInfo[]>([]);
  const [selectedDisplayId, setSelectedDisplayId] = useState<string | undefined>();
  const [error, setError] = useState("");

  const send = useCallback((payload: object) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }, []);

  useEffect(() => {
    if (!connection || !active) return;
    let cancelled = false;
    rtcActiveRef.current = false;
    const ws = new WebSocket(desktopSocketUrl(connection));
    wsRef.current = ws;
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data) as {
            type?: string;
            view_only?: boolean;
            displays?: DisplayInfo[];
            selected_display?: string | null;
            id?: string | null;
            message?: string;
          };
          if ((msg.type === "hello" || msg.type === "display") && msg.displays) setDisplays(msg.displays);
          if ((msg.type === "hello" || msg.type === "display") && msg.selected_display) {
            setSelectedDisplayId(msg.selected_display);
          }
          if (msg.type === "control" && typeof msg.view_only === "boolean") setViewOnly(msg.view_only);
          if (msg.type === "error" || msg.type === "denied") {
            const text = msg.message || "Desktop error";
            setError(text);
            handlersRef.current.onError?.(text);
          }
        } catch {
          /* ignore */
        }
        return;
      }
      if (!rtcActiveRef.current && event.data instanceof ArrayBuffer) {
        setFrame(toDataUri(event.data));
        setError("");
      }
    };
    ws.onclose = () => {
      if (!cancelled) setError("Desktop disconnected");
    };
    const onApp = (state: AppStateStatus) => {
      if (state !== "active") send({ type: "release_all" });
    };
    const sub = AppState.addEventListener("change", onApp);

    let pc: RTCPeerConnection | null = null;
    let stopPump = () => {};
    if (webrtc && hasRtcPeerConnection()) {
      const Peer = (globalThis as unknown as { RTCPeerConnection: typeof RTCPeerConnection }).RTCPeerConnection;
      const peer = new Peer({ iceServers: [{ urls: "stun:stun.cloudflare.com:3478" }] });
      pc = peer;
      const pending: object[] = [];
      let sessionId = "";
      peer.addTransceiver("video", { direction: "recvonly" });
      peer.onicecandidate = (ev) => {
        if (!ev.candidate) return;
        const candidate = ev.candidate.toJSON();
        if (!sessionId) {
          pending.push(candidate);
          return;
        }
        void rtcIce(connection, { session_id: sessionId, candidate }).catch(() => {});
      };
      peer.ontrack = (ev) => {
        const stream = ev.streams[0] ?? new MediaStream(ev.track ? [ev.track] : []);
        stopPump();
        stopPump = pumpStream(stream, (uri) => {
          rtcActiveRef.current = true;
          setFrame(uri);
          setError("");
        });
      };
      void (async () => {
        try {
          const offer = await peer.createOffer();
          await peer.setLocalDescription(offer);
          if (cancelled) return;
          const result = await rtcOffer(connection, {
            session_id: sessionId || undefined,
            offer: { type: offer.type, sdp: offer.sdp },
          });
          if (cancelled) return;
          sessionId = result.session_id;
          await peer.setRemoteDescription(result.answer as RTCSessionDescriptionInit);
          for (const candidate of pending) {
            void rtcIce(connection, { session_id: sessionId, candidate }).catch(() => {});
          }
        } catch {
          rtcActiveRef.current = false;
          peer.close();
          if (pc === peer) pc = null;
        }
      })();
    }

    return () => {
      cancelled = true;
      sub.remove();
      send({ type: "release_all" });
      ws.close();
      if (wsRef.current === ws) wsRef.current = null;
      stopPump();
      pc?.close();
      rtcActiveRef.current = false;
    };
  }, [active, connection, send, webrtc]);

  const selectDisplay = useCallback(
    (id: string) => {
      setSelectedDisplayId(id);
      send({ type: "display", id });
    },
    [send],
  );

  const setControl = useCallback(
    (nextViewOnly: boolean) => {
      setViewOnly(nextViewOnly);
      send({ type: "control", view_only: nextViewOnly });
    },
    [send],
  );

  const pointer = useCallback(
    (action: string, x: number, y: number, button = 1, dy = 0) => {
      send({ type: "pointer", action, x, y, button, dy });
    },
    [send],
  );

  const key = useCallback(
    (action: string, code: string) => {
      send({ type: "key", action, key: code });
    },
    [send],
  );

  const text = useCallback(
    (data: string) => {
      send({ type: "text", data });
    },
    [send],
  );

  return {
    frame,
    viewOnly,
    setControl,
    displays,
    selectedDisplayId,
    error,
    pointer,
    key,
    text,
    send,
    selectDisplay,
  };
}
