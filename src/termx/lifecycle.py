from __future__ import annotations

import asyncio
from typing import Any


async def shutdown_state(state: Any, timeout: float = 3.0) -> None:
    """Stop tunnels, desktop pumps, RTC, virtual displays, then kill PTY groups."""
    tunnels = getattr(state, "tunnels", None)
    if tunnels is not None:
        await _invoke(getattr(tunnels, "stop_all", None), timeout=timeout)
    desktop = getattr(state, "desktop", None)
    if desktop is not None:
        await _invoke(getattr(desktop, "close", None), timeout=timeout)
    try:
        from termx.desktop.capture import close_capture

        close_capture()
    except Exception:
        pass
    rtc = getattr(state, "rtc", None)
    if rtc is not None:
        await _invoke(getattr(rtc, "close_all", None), timeout=timeout)
    try:
        from termx.desktop.virtual import destroy_all_virtual_displays

        destroy_all_virtual_displays()
    except Exception:
        pass
    sessions = getattr(state, "sessions", None)
    if sessions is not None:
        await _invoke(getattr(sessions, "kill_all", None), timeout, timeout=timeout)


async def _invoke(fn: Any, *args: Any, timeout: float) -> None:
    if not callable(fn):
        return
    try:
        result = fn(*args)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=timeout)
    except Exception:
        pass
