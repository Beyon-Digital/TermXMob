from __future__ import annotations

import asyncio
from typing import Any

from termx.desktop.capture import grab_jpeg, list_displays, pointer_target
from termx.desktop.input import apply_event

MAX_ACTIONS = 12


class ComputerController:
    def __init__(self, display_id: str | None = None) -> None:
        self.display_id = display_id

    async def screenshot(self) -> bytes:
        return await asyncio.to_thread(grab_jpeg, self.display_id)

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
        if len(actions) > MAX_ACTIONS:
            raise ValueError(f"computer action batch exceeds {MAX_ACTIONS} actions")
        try:
            for action in actions:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                await self._action(action, cancel=cancel)
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
    ) -> None:
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
            await asyncio.to_thread(apply_event, event, target)
            if kind == "double_click":
                await asyncio.sleep(0.08)
                await asyncio.to_thread(apply_event, event, target)
            return
        if kind == "drag":
            path = action.get("path") or []
            if not isinstance(path, list) or len(path) < 2:
                raise ValueError("drag requires at least two points")
            first = path[0]
            await asyncio.to_thread(
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
                await asyncio.to_thread(
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
            await asyncio.to_thread(
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
            await asyncio.to_thread(
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
                await asyncio.to_thread(apply_event, {"type": "text", "data": character})
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
                await asyncio.to_thread(
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
