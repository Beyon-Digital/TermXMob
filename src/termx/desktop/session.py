from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from termx import notify
from termx.config import ConfigStore
from termx.desktop.capture import (
    CaptureError,
    close_capture,
    grab_jpeg,
    list_displays,
    pointer_target,
)
from termx.desktop.input import InputError, apply_event, clipboard_get, clipboard_set
from termx.desktop.permissions import permission_snapshot
from termx.desktop.virtual import VirtualDisplayError, create_virtual_display, destroy_virtual_display

IDLE_GRACE_DEFAULT = 5.0


def idle_grace() -> float:
    """Seconds to keep the capture helper alive after the last client leaves."""
    try:
        return max(0.0, float(os.environ.get("TERMX_CAPTURE_IDLE_GRACE", IDLE_GRACE_DEFAULT)))
    except ValueError:
        return IDLE_GRACE_DEFAULT


def pause_grace() -> float:
    """Seconds to keep capture alive after every viewer paused (hidden tab/window)."""
    try:
        return max(0.0, float(os.environ.get("TERMX_CAPTURE_PAUSE_GRACE", 1.0)))
    except ValueError:
        return 1.0


_PROMPTED: set[str] = set()


def _prompt_once(which: str) -> None:
    """Ask the desktop shell for the native TCC prompt, at most once per process."""
    if which in _PROMPTED:
        return
    _PROMPTED.add(which)
    notify.permission(which)


class DesktopManager:
    def __init__(self, store: ConfigStore | None = None, rtc: Any | None = None) -> None:
        self.store = store
        # WebRTC signalling runs over the session socket so the real-time path
        # never needs an HTTP round trip.
        self.rtc = rtc
        self.view_only = store.get().desktop.view_only_default if store is not None else True
        self.display_id: str | None = None
        self._lock = asyncio.Lock()
        self._pumps: set[asyncio.Task[None]] = set()
        self._stops: set[asyncio.Event] = set()
        self._active: set[object] = set()
        self._idle_task: asyncio.Task[None] | None = None
        self._fps = 0.0
        self._last_capture_ms = 0
        self._target_fps = 12

    async def _cancel_idle_close(self) -> None:
        task = self._idle_task
        self._idle_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _close_when_idle(self, grace: float) -> None:
        """Release capture once no viewer is actively watching."""
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:
            return
        if self._active:
            return
        try:
            await asyncio.to_thread(close_capture)
        except Exception:
            pass

    async def _schedule_idle_close(self, grace: float) -> None:
        await self._cancel_idle_close()
        self._idle_task = asyncio.create_task(self._close_when_idle(grace))

    async def _ws_create_display(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        """Create a virtual display on behalf of a socket client."""
        width = int(payload.get("width") or 1170)
        height = int(payload.get("height") or 2532)
        try:
            created = await asyncio.to_thread(
                create_virtual_display,
                width,
                height,
                float(payload.get("dpr") or 2.0),
                int(payload.get("refresh_hz") or 60),
                owner=payload.get("owner"),
            )
        except VirtualDisplayError as exc:
            await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
            return
        self.display_id = str(created["id"])
        await websocket.send_text(
            json.dumps({"type": "displays", "created": created, "id": self.display_id, **self.snapshot()})
        )

    async def _ws_rtc(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        """WebRTC signalling over the session socket instead of HTTP."""
        rtc = self.rtc
        session_id = str(payload.get("session_id") or "desktop")
        if rtc is None or not rtc.available():
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "rtc",
                        "action": payload.get("action") or "offer",
                        "ok": False,
                        "error": "WebRTC is unavailable on this host; using WebSocket frames",
                    }
                )
            )
            return
        action = str(payload.get("action") or "offer")
        try:
            if action == "offer":
                result = await asyncio.to_thread(
                    rtc.handle_offer,
                    session_id,
                    {**(payload.get("offer") or {}), "_termx_fps": self._target_fps},
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rtc",
                            "action": "answer",
                            "ok": True,
                            "session_id": result.get("session_id", session_id),
                            "answer": result.get("answer"),
                        }
                    )
                )
            elif action == "ice":
                await asyncio.to_thread(rtc.add_ice, session_id, payload.get("candidate") or {})
            elif action == "close":
                await asyncio.to_thread(rtc.close, session_id)
        except Exception as exc:  # surface negotiation failures to the client
            await websocket.send_text(
                json.dumps({"type": "rtc", "action": action, "ok": False, "error": str(exc)})
            )

    def snapshot(self) -> dict[str, Any]:
        displays = list_displays()
        selected = self.display_id or next(
            (str(item["id"]) for item in displays if item.get("main")),
            str(displays[0]["id"]) if displays else None,
        )
        return {
            "displays": displays,
            "selected_display": selected,
            "view_only": self.view_only,
            "permissions": permission_snapshot(),
            "fps": int(round(self._fps)),
            "target_fps": self._target_fps,
            "last_capture_ms": self._last_capture_ms,
        }

    def _metrics_payload(self) -> dict[str, Any]:
        return {"type": "metrics", "fps": int(round(self._fps)), "last_capture_ms": self._last_capture_ms}

    async def attach(self, websocket: WebSocket) -> None:
        await self._cancel_idle_close()
        permissions = permission_snapshot()
        if permissions.get("screen_recording") == "denied":
            _prompt_once("screen_recording")
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "hello", **self.snapshot()}))
        stop = asyncio.Event()
        resume = asyncio.Event()
        resume.set()
        token = object()
        self._stops.add(stop)
        self._active.add(token)

        async def frames() -> None:
            last = time.monotonic()
            metric_at = last
            reported: str | None = None
            while not stop.is_set():
                await resume.wait()
                if stop.is_set():
                    break
                try:
                    started = time.monotonic()
                    frame = await asyncio.to_thread(grab_jpeg, self.display_id)
                    now = time.monotonic()
                    dt = now - last
                    if dt > 0:
                        self._fps = 1.0 / dt
                    self._last_capture_ms = int((now - started) * 1000)
                    last = now
                    await websocket.send_bytes(frame)
                    if reported is not None:
                        reported = None
                        await websocket.send_text(json.dumps({"type": "cleared"}))
                    if now - metric_at >= 1.0:
                        metric_at = now
                        await websocket.send_text(json.dumps(self._metrics_payload()))
                except CaptureError as exc:
                    # Report each distinct failure once: repeating the same
                    # message every retry floods clients with stale banners.
                    message = str(exc)
                    if message != reported:
                        reported = message
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": message}))
                        except Exception:
                            break
                    await asyncio.sleep(1.5)
                except Exception:
                    break
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, (1.0 / self._target_fps) - elapsed))

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
                    if self.store is not None:
                        try:
                            self.store.set_view_only(self.view_only)
                        except Exception:
                            pass
                    await websocket.send_text(json.dumps({"type": "control", "view_only": self.view_only}))
                    continue
                if kind == "pause":
                    if token in self._active:
                        self._active.discard(token)
                        resume.clear()
                        await websocket.send_text(json.dumps({"type": "paused"}))
                        if not self._active:
                            await self._schedule_idle_close(pause_grace())
                    continue
                if kind == "resume":
                    if token not in self._active:
                        self._active.add(token)
                        resume.set()
                        await self._cancel_idle_close()
                        await websocket.send_text(json.dumps({"type": "resumed"}))
                    continue
                if kind == "display":
                    requested = payload.get("id")
                    self.display_id = str(requested) if requested else None
                    await websocket.send_text(
                        json.dumps({"type": "display", "id": self.display_id, **self.snapshot()})
                    )
                    continue
                if kind == "displays":
                    await websocket.send_text(
                        json.dumps({"type": "displays", **self.snapshot()})
                    )
                    continue
                if kind == "display_create":
                    await self._ws_create_display(websocket, payload)
                    continue
                if kind == "display_delete":
                    display_id = str(payload.get("id") or "")
                    previous_display = self.display_id
                    try:
                        if self.display_id == display_id:
                            # Stop the capture helper before removing its output,
                            # then select a surviving screen for the next frame.
                            await asyncio.to_thread(close_capture)
                            remaining = [item for item in list_displays() if str(item.get("id")) != display_id]
                            self.display_id = next(
                                (str(item["id"]) for item in remaining if item.get("main")),
                                str(remaining[0]["id"]) if remaining else None,
                            )
                        await asyncio.to_thread(destroy_virtual_display, display_id)
                    except VirtualDisplayError as exc:
                        self.display_id = previous_display
                        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
                        continue
                    await websocket.send_text(json.dumps({"type": "displays", **self.snapshot()}))
                    continue
                if kind == "stream":
                    try:
                        requested_fps = int(payload.get("fps") or 12)
                    except (TypeError, ValueError):
                        requested_fps = 12
                    self._target_fps = max(1, min(30, requested_fps))
                    if self.rtc is not None:
                        try:
                            await asyncio.to_thread(self.rtc.set_fps, "desktop", self._target_fps)
                        except Exception:
                            pass
                    await websocket.send_text(
                        json.dumps({"type": "stream", "fps": self._target_fps})
                    )
                    continue
                if kind == "rtc":
                    await self._ws_rtc(websocket, payload)
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
                if kind in {"pointer", "key", "text"} and self._input_denied():
                    _prompt_once("accessibility")
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "denied",
                                "message": (
                                    "Accessibility permission is required to control this Mac. "
                                    "Grant it to Termx in System Settings → Privacy & Security → Accessibility."
                                ),
                            }
                        )
                    )
                    continue
                if kind in {"pointer", "key", "text", "release_all"}:
                    try:
                        target = pointer_target(self.display_id) if kind == "pointer" else None
                        await asyncio.to_thread(apply_event, payload, target)
                    except InputError as exc:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()
            resume.set()
            self._stops.discard(stop)
            self._active.discard(token)
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            self._pumps.discard(pump)
            try:
                await asyncio.to_thread(apply_event, {"type": "release_all"})
            except Exception:
                pass
            if not self._active:
                # No viewer watching: stop capturing so the helper (and the macOS
                # screen-recording indicator) is not left running.
                await self._schedule_idle_close(idle_grace())

    def _input_denied(self) -> bool:
        if sys.platform != "darwin":
            return False
        return permission_snapshot().get("accessibility") == "denied"

    async def close(self) -> None:
        await self._cancel_idle_close()
        self._active.clear()
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
        try:
            await asyncio.to_thread(close_capture)
        except Exception:
            pass
