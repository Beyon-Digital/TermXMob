from __future__ import annotations

import asyncio
import sys
from typing import Any

from termx.desktop.capture import grab_jpeg, list_displays, pointer_target
from termx.desktop.input import apply_event, clipboard_get, clipboard_set

MAX_ACTIONS = 12


async def _guarded_os(revision, operation, *args):
    """Check in the OS worker, including transient private intervals.

    An effect already sent to the OS cannot be undone. A changed epoch refuses
    subsequent events and reports uncertainty rather than replaying input.
    Key/button release cleanup deliberately uses the unguarded path.
    """
    from termx.desktop.recording import capture_privacy_revision
    def perform():
        if capture_privacy_revision() != revision:
            raise PermissionError('Private capture changed before computer input')
        result = operation(*args)
        try:
            changed = capture_privacy_revision() != revision
        except PermissionError:
            changed = True
        if changed:
            raise PermissionError('Private capture changed during OS input; an event may have been sent. Verify before retrying.')
        return result
    return await asyncio.to_thread(perform)



class ComputerController:
    def __init__(self, display_id: str | None = None) -> None:
        self.display_id = display_id

    async def screenshot(self) -> bytes:
        from termx.desktop.recording import capture_privacy_revision
        revision = capture_privacy_revision()
        frame = await asyncio.to_thread(grab_jpeg, self.display_id)
        if capture_privacy_revision() != revision:
            raise PermissionError('Private capture changed while computer observation was in flight')
        return frame

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
        if len(actions) > MAX_ACTIONS:
            raise ValueError(f"computer action batch exceeds {MAX_ACTIONS} actions")
        try:
            from termx.desktop.recording import capture_privacy_revision
            privacy_revision = capture_privacy_revision()
            for action in actions:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                await self._action(action, cancel=cancel, privacy_revision=privacy_revision)
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            return await self.screenshot()
        except BaseException:
            await self.release_all()
            raise

    async def release_all(self) -> None:
        await asyncio.to_thread(apply_event, {"type": "release_all"})

    def _display(self) -> tuple[str | None, float, float]:
        displays = list_displays()
        selected = next(
            (item for item in displays if str(item.get("id")) == str(self.display_id)),
            next((item for item in displays if item.get("main")), displays[0] if displays else {}),
        )
        display_id = str(selected.get("id")) if selected.get("id") is not None else None
        return display_id, float(selected.get("width") or 1), float(selected.get("height") or 1)

    async def _action(
        self,
        action: dict[str, Any],
        *,
        cancel: asyncio.Event | None = None,
        privacy_revision: int | None = None,
    ) -> None:
        from termx.desktop.recording import capture_privacy_revision
        if privacy_revision is None: privacy_revision = capture_privacy_revision()
        if capture_privacy_revision() != privacy_revision:
            raise PermissionError("Private capture changed before computer input")
        kind = str(action.get("type") or "")
        if kind in {"screenshot", ""}:
            return
        if kind == "wait":
            delay = min(5.0, max(0.0, float(action.get("seconds") or 1)))
            if cancel is None:
                await asyncio.sleep(delay)
                return
            try:
                await asyncio.wait_for(cancel.wait(), timeout=delay)
            except asyncio.TimeoutError:
                return
            raise asyncio.CancelledError
        display_id, width, height = self._display()
        target = pointer_target(display_id) if display_id else None
        if kind in {"click", "double_click", "move"}:
            event = {
                "type": "pointer",
                "action": "move" if kind == "move" else "click",
                "x": float(action.get("x") or 0) / max(width, 1),
                "y": float(action.get("y") or 0) / max(height, 1),
                "button": _button(action.get("button")),
            }
            await _guarded_os(privacy_revision, apply_event, event, target)
            if kind == "double_click":
                await asyncio.sleep(0.08)
                await _guarded_os(privacy_revision, apply_event, event, target)
            return
        if kind == "drag":
            path = action.get("path") or []
            if not isinstance(path, list) or len(path) < 2:
                raise ValueError("drag requires at least two points")
            first = path[0]
            await _guarded_os(privacy_revision,
                apply_event,
                {
                    "type": "pointer",
                    "action": "down",
                    "x": float(first.get("x") or 0) / max(width, 1),
                    "y": float(first.get("y") or 0) / max(height, 1),
                    "button": 1,
                },
                target,
            )
            for point in path[1:]:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                await _guarded_os(privacy_revision,
                    apply_event,
                    {
                        "type": "pointer",
                        "action": "move",
                        "x": float(point.get("x") or 0) / max(width, 1),
                        "y": float(point.get("y") or 0) / max(height, 1),
                        "button": 1,
                        "down": True,
                    },
                    target,
                )
            last = path[-1]
            await _guarded_os(privacy_revision,
                apply_event,
                {
                    "type": "pointer",
                    "action": "up",
                    "x": float(last.get("x") or 0) / max(width, 1),
                    "y": float(last.get("y") or 0) / max(height, 1),
                    "button": 1,
                },
                target,
            )
            return
        if kind == "scroll":
            await _guarded_os(privacy_revision,
                apply_event,
                {
                    "type": "pointer",
                    "action": "wheel",
                    "x": float(action.get("x") or 0) / max(width, 1),
                    "y": float(action.get("y") or 0) / max(height, 1),
                    "dx": float(action.get("scroll_x") or action.get("dx") or 0),
                    "dy": float(action.get("scroll_y") or action.get("dy") or 0),
                },
                target,
            )
            return
        if kind == "type":
            for character in str(action.get("text") or ""):
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                await _guarded_os(privacy_revision, apply_event, {"type": "text", "data": character})
            return
        if kind == "paste_text":
            text = str(action.get("text") or "")
            if not text:
                return
            if not await self._paste_text(text, privacy_revision=privacy_revision):
                # Clipboard/native paste unavailable on this backend — the
                # per-character path remains the safe fallback.
                for character in text:
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError
                    await _guarded_os(privacy_revision, apply_event, {"type": "text", "data": character})
            return
        if kind in {"mouse_down", "mouse_up"}:
            await _guarded_os(privacy_revision,
                apply_event,
                {
                    "type": "pointer",
                    "action": "down" if kind == "mouse_down" else "up",
                    "x": float(action.get("x") or 0) / max(width, 1),
                    "y": float(action.get("y") or 0) / max(height, 1),
                    "button": _button(action.get("button")),
                },
                target,
            )
            return
        if kind in {"key_down", "key_up"}:
            keys = action.get("keys") or action.get("key") or []
            if isinstance(keys, str):
                keys = [keys]
            normalized = [_key_name(key) for key in keys if str(key).strip()]
            modifiers = [key for key in normalized if key in _MODIFIERS]
            regular = [key for key in normalized if key not in _MODIFIERS]
            for key in regular or modifiers:
                await _guarded_os(privacy_revision,
                    apply_event,
                    {
                        "type": "key",
                        "key": key,
                        "action": "down" if kind == "key_down" else "up",
                        "modifiers": modifiers,
                    },
                )
            return
        if kind == "release_all":
            await self.release_all()
            return
        if kind == "set_display":
            display_id = str(action.get("display_id") or "").strip()
            if not display_id:
                raise ValueError("set_display requires a display_id")
            self.display_id = display_id
            return
        if kind == "keypress":
            keys = action.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            normalized = [_key_name(key) for key in keys if str(key).strip()]
            modifiers = [key for key in normalized if key in _MODIFIERS]
            regular = [key for key in normalized if key not in _MODIFIERS]
            if not regular:
                regular = modifiers
                modifiers = []
            for key in regular:
                await _guarded_os(privacy_revision,
                    apply_event,
                    {
                        "type": "key",
                        "key": key,
                        "action": "tap",
                        "modifiers": modifiers,
                    },
                )
            return
        raise ValueError(f"unsupported computer action: {kind}")

    async def _paste_text(self, text: str, *, privacy_revision: int | None = None) -> bool:
        """Paste via the native clipboard bridge; False when unsupported.

        Verified by reading the clipboard back — clipboard_set is a no-op on
        hosts with no clipboard command, so a mismatch means fall back to
        per-character typing.
        """
        if privacy_revision is None:
            from termx.desktop.recording import capture_privacy_revision
            privacy_revision = capture_privacy_revision()
        try:
            await _guarded_os(privacy_revision, clipboard_set, text)
            if await _guarded_os(privacy_revision, clipboard_get) != text:
                return False
            modifier = "meta" if sys.platform == "darwin" else "control"
            await _guarded_os(privacy_revision,
                apply_event,
                {"type": "key", "key": "v", "action": "tap", "modifiers": [modifier]},
            )
            return True
        except PermissionError:
            raise
        except Exception:
            return False


def _button(value: Any) -> int:
    return {"left": 1, "middle": 2, "right": 3}.get(str(value or "left").lower(), 1)


_MODIFIERS = {"shift", "control", "alt", "meta"}
_KEY_ALIASES = {
    "cmd": "meta",
    "command": "meta",
    "ctrl": "control",
    "option": "alt",
    "return": "enter",
    "esc": "escape",
}


def _key_name(value: Any) -> str:
    key = str(value).strip().lower()
    return _KEY_ALIASES.get(key, key)
