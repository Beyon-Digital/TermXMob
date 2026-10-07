from __future__ import annotations

import asyncio
import json
import os
import secrets
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
        self._views: dict[str, dict[str, Any]] = {}
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

    async def _ws_rtc(self, websocket: WebSocket, payload: dict[str, Any], *, principal_id=None, managed_session_id=None, authorize=None, viewer_id="legacy", owned_rtc=None) -> None:
        """WebRTC signalling over the session socket instead of HTTP."""
        rtc = self.rtc
        client_id = str(payload.get("session_id") or "desktop")
        if len(client_id)>128:
            await websocket.send_text(json.dumps({"type":"rtc","ok":False,"error":"RTC identifier exceeds 128 characters"}))
            return
        import hashlib
        session_id = hashlib.sha256((viewer_id + "\0" + client_id).encode()).hexdigest()
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
                if owned_rtc is not None: owned_rtc.add(session_id)
                result = await asyncio.to_thread(
                    rtc.handle_offer,
                    session_id,
                    {**(payload.get("offer") or {}), "_termx_fps": self._target_fps},
                    principal_id=principal_id, managed_session_id=managed_session_id, authorize=authorize,
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rtc",
                            "action": "answer",
                            "ok": True,
                            "session_id": client_id,
                            "answer": result.get("answer"),
                        }
                    )
                )
            elif action == "ice":
                await asyncio.to_thread(rtc.add_ice, session_id, payload.get("candidate") or {}, principal_id=principal_id, managed_session_id=managed_session_id)
            elif action == "close":
                await asyncio.to_thread(rtc.close_owned, session_id, principal_id=principal_id, managed_session_id=managed_session_id)
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
            "viewer_count": len(self._active),
            "fps": int(round(self._fps)),
            "target_fps": self._target_fps,
            "last_capture_ms": self._last_capture_ms,
        }

    def _metrics_payload(self) -> dict[str, Any]:
        return {"type": "metrics", "fps": int(round(self._fps)), "last_capture_ms": self._last_capture_ms}

    def viewers(self, principal_id: str) -> list[dict[str, Any]]:
        return [{'id': row['id'], 'state': 'paused' if row['paused'] else 'watching' if row['view_only'] else 'control'}
                for row in self._views.copy().values() if row['principal_id'] == principal_id and not row['stop'].is_set()]

    async def stop_viewer(self, identifier: str, principal_id: str) -> dict[str, bool]:
        row = self._views.get(identifier)
        if not row or not principal_id or row['principal_id'] != principal_id:
            raise KeyError('Desktop capture not found')
        row['stop'].set()
        row['resume'].set()
        await row['websocket'].close(code=1000)
        return {'ok': True}

    async def attach(self, websocket: WebSocket, authorize_control=None, *, principal_id=None, session_id=None, authorize_view=None) -> None:
        await self._cancel_idle_close()
        permissions = permission_snapshot()
        if permissions.get("screen_recording") == "denied":
            _prompt_once("screen_recording")
        await websocket.accept()
        view_only = True  # Every new connection begins watching, even if another controls.
        await websocket.send_text(json.dumps({"type": "hello", **self.snapshot(), "view_only": view_only}))
        stop = asyncio.Event()
        resume = asyncio.Event()
        resume.set()
        token = object()
        self._stops.add(stop)
        self._active.add(token)
        identifier = secrets.token_urlsafe(16)
        viewer = {'id': identifier, 'principal_id': principal_id, 'session_id': session_id,
                  'view_only': view_only, 'paused': False, 'stop': stop, 'resume': resume, 'websocket': websocket}
        self._views[identifier] = viewer
        applied_input = False
        owned_rtc = set()

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
                    from termx.desktop.recording import capture_privacy_revision
                    def allowed_frame():
                        if (session_id is not None and authorize_view is None) or (authorize_view is not None and not authorize_view()):
                            raise PermissionError("Desktop view authority expired")
                        return capture_privacy_revision()
                    privacy = await asyncio.to_thread(allowed_frame)
                    frame = await asyncio.to_thread(grab_jpeg, self.display_id)
                    if await asyncio.to_thread(allowed_frame) != privacy:
                        raise PermissionError("Desktop privacy changed during capture")
                    now = time.monotonic()
                    dt = now - last
                    if dt > 0:
                        self._fps = 1.0 / dt
                    self._last_capture_ms = int((now - started) * 1000)
                    last = now
                    if stop.is_set() or not resume.is_set():
                        # Paused or disconnected while this frame was in flight;
                        # don't deliver a frame after "paused" was acknowledged.
                        continue
                    if await asyncio.to_thread(allowed_frame) != privacy:
                        raise PermissionError("Desktop privacy changed before publication")
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
                protected = kind in {'pointer', 'key', 'text', 'release_all', 'clipboard',
                                     'display_create', 'display_delete', 'rtc'} or (
                    kind == 'control' and not bool(payload.get('view_only', True)))
                if protected and authorize_control is not None and not authorize_control():
                    view_only = True
                    viewer['view_only'] = True
                    await websocket.send_text(json.dumps({'type': 'denied', 'view_only': True, 'message': 'desktop-control permission required'}))
                    continue
                if kind == "control":
                    requested = bool(payload.get("view_only", True))
                    if not requested and self._input_denied():
                        view_only = True
                        viewer['view_only'] = True
                        _prompt_once('accessibility')
                        await websocket.send_text(json.dumps({'type': 'denied', 'view_only': True,
                            'message': 'Accessibility permission is required to control this Mac. Grant it to Termx in System Settings → Privacy & Security → Accessibility.'}))
                        continue
                    view_only = requested
                    viewer['view_only'] = view_only
                    self.view_only = view_only
                    if self.store is not None:
                        try:
                            self.store.set_view_only(self.view_only)
                        except Exception:
                            pass
                    await websocket.send_text(json.dumps({"type": "control", "view_only": view_only}))
                    continue
                if kind == "pause":
                    if token in self._active:
                        self._active.discard(token)
                        resume.clear()
                        viewer['paused'] = True
                        await websocket.send_text(json.dumps({"type": "paused"}))
                        if not self._active:
                            await self._schedule_idle_close(pause_grace())
                    continue
                if kind == "resume":
                    if token not in self._active:
                        self._active.add(token)
                        resume.set()
                        viewer['paused'] = False
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
                    await self._ws_rtc(websocket, payload, principal_id=principal_id, managed_session_id=session_id, authorize=authorize_view, viewer_id=identifier, owned_rtc=owned_rtc)
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
                if view_only and kind in {"pointer", "key", "text"}:
                    await websocket.send_text(json.dumps({"type": "denied", "message": "view-only"}))
                    continue
                if kind in {"pointer", "key", "text"} and self._input_denied():
                    view_only = True
                    self._views[identifier]['view_only'] = True
                    _prompt_once("accessibility")
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "denied",
                                "view_only": True,
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
                        if kind != "release_all":
                            applied_input = True
                    except InputError as exc:
                        view_only = True
                        self._views[identifier]['view_only'] = True
                        await websocket.send_text(json.dumps({"type": "denied", "view_only": True, "message": str(exc)}))
        except WebSocketDisconnect:
            pass
        finally:
            self._views.pop(identifier, None)
            if self.rtc is not None:
                for rtc_id in owned_rtc:
                    await asyncio.to_thread(self.rtc.close_owned, rtc_id, principal_id=principal_id, managed_session_id=session_id)
            stop.set()
            resume.set()
            self._stops.discard(stop)
            self._active.discard(token)
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            self._pumps.discard(pump)
            if applied_input:
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
