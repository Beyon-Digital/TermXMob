import { useCallback, useEffect, useRef } from "react";
import { AppState, type AppStateStatus } from "react-native";

import { ptySocketUrl } from "@/lib/api";
import type { Connection } from "@/lib/types";

type Handlers = {
  onOutput: (data: string | Uint8Array) => void;
  onExit?: () => void;
  onReconnect?: () => void;
};

export function usePty(connection: Connection | null, sessionId: string | null, handlers: Handlers) {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const sendBuf = useRef<string[]>([]);

  const flush = (ws: WebSocket) => {
    while (sendBuf.current.length && ws.readyState === WebSocket.OPEN) {
      ws.send(sendBuf.current.shift() as string);
    }
  };

  const queue = useCallback((payload: object) => {
    const raw = JSON.stringify(payload);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(raw);
      return;
    }
    sendBuf.current.push(raw);
  }, []);

  useEffect(() => {
    if (!connection || !sessionId) return;
    let cancelled = false;
    let ping: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let socket: WebSocket | null = null;

    const open = () => {
      if (cancelled) return;
      if (socket) {
        socket.onclose = null;
        socket.onmessage = null;
        socket.onerror = null;
        try {
          socket.close();
        } catch {
          /* ignore */
        }
      }
      const ws = new WebSocket(ptySocketUrl(connection, sessionId));
      socket = ws;
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        attempt = 0;
        handlersRef.current.onReconnect?.();
        flush(ws);
        queue({ type: "ping" });
      };
      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data) as { type?: string };
            if (msg.type === "exit") handlersRef.current.onExit?.();
          } catch {
            handlersRef.current.onOutput(event.data);
          }
          return;
        }
        if (event.data instanceof ArrayBuffer) {
          handlersRef.current.onOutput(new Uint8Array(event.data));
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        if (wsRef.current === ws) wsRef.current = null;
        const delay = Math.min(4000, 250 * 2 ** attempt);
        attempt += 1;
        retry = setTimeout(open, delay);
      };
    };

    open();
    ping = setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 15000);

    const resume = () => {
      const ws = wsRef.current;
      if (!ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
        open();
      }
    };
    const onApp = (state: AppStateStatus) => {
      if (state === "active") resume();
    };
    const sub = AppState.addEventListener("change", onApp);
    const onVis = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") resume();
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVis);
    }

    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
      if (ping) clearInterval(ping);
      sub.remove();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVis);
      }
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      if (wsRef.current === socket) wsRef.current = null;
    };
  }, [connection, queue, sessionId]);

  const sendRaw = useCallback(
    (data: string) => {
      if (!data) return;
      queue({ type: "input", data });
    },
    [queue],
  );

  const sendResize = useCallback(
    (cols: number, rows: number) => {
      queue({ type: "resize", cols, rows });
    },
    [queue],
  );

  const sendSignal = useCallback(
    (name: string) => {
      queue({ type: "signal", name });
    },
    [queue],
  );

  return { sendRaw, sendResize, sendSignal };
}
