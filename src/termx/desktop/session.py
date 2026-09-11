from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from termx.desktop.capture import CaptureError, grab_jpeg, list_displays
from termx.desktop.input import InputError, apply_event, clipboard_get, clipboard_set


class DesktopManager:
    def __init__(self) -> None:
        self.view_only = True
        self._lock = asyncio.Lock()
        self._pumps: set[asyncio.Task[None]] = set()
        self._stops: set[asyncio.Event] = set()
        self._fps = 0.0
        self._last_capture_ms = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "displays": list_displays(),
            "view_only": self.view_only,
            "fps": int(round(self._fps)),
            "last_capture_ms": self._last_capture_ms,
        }

    def _metrics_payload(self) -> dict[str, Any]:
        return {"type": "metrics", "fps": int(round(self._fps)), "last_capture_ms": self._last_capture_ms}

    async def attach(self, websocket: WebSocket) -> None:
        self.view_only = True
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "hello", **self.snapshot()}))
        stop = asyncio.Event()
        self._stops.add(stop)

        async def frames() -> None:
            last = time.monotonic()
            metric_at = last
            while not stop.is_set():
                try:
                    started = time.monotonic()
                    frame = await asyncio.to_thread(grab_jpeg)
                    now = time.monotonic()
                    dt = now - last
                    if dt > 0:
                        self._fps = 1.0 / dt
                    self._last_capture_ms = int((now - started) * 1000)
                    last = now
                    await websocket.send_bytes(frame)
                    if now - metric_at >= 1.0:
                        metric_at = now
                        await websocket.send_text(json.dumps(self._metrics_payload()))
                except CaptureError as exc:
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
                    except Exception:
                        break
                    await asyncio.sleep(1.5)
                except Exception:
                    break
                await asyncio.sleep(0.12)

        pump = asyncio.create_task(frames())
        self._pumps.add(pump)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                raw_text = message.get("text")
                if not raw_text:
                    continue
                try:
                    payload = json.loads(raw_text)
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")
                if kind == "control":
                    self.view_only = bool(payload.get("view_only", True))
                    await websocket.send_text(json.dumps({"type": "control", "view_only": self.view_only}))
                    continue
                if kind == "metrics":
                    await websocket.send_text(json.dumps(self._metrics_payload()))
                    continue
                if kind == "clipboard":
                    action = str(payload.get("action") or "get")
                    if action == "get":
                        text = await asyncio.to_thread(clipboard_get)
                        await websocket.send_text(json.dumps({"type": "clipboard", "text": text}))
                    elif action == "set":
                        await asyncio.to_thread(clipboard_set, str(payload.get("text") or ""))
                    continue
                if self.view_only and kind in {"pointer", "key", "text"}:
                    await websocket.send_text(json.dumps({"type": "denied", "message": "view-only"}))
                    continue
                if kind in {"pointer", "key", "text", "release_all"}:
                    try:
                        await asyncio.to_thread(apply_event, payload)
                    except InputError as exc:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()
            self._stops.discard(stop)
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            self._pumps.discard(pump)
            try:
                await asyncio.to_thread(apply_event, {"type": "release_all"})
            except Exception:
                pass

    async def close(self) -> None:
        for event in list(self._stops):
            event.set()
        pumps = list(self._pumps)
        for task in pumps:
            task.cancel()
        if pumps:
            await asyncio.gather(*pumps, return_exceptions=True)
        self._pumps.clear()
        self._stops.clear()
        try:
            await asyncio.to_thread(apply_event, {"type": "release_all"})
        except Exception:
            pass
