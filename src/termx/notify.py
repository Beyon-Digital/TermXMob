from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

_LOCK = threading.Lock()


def desktop_enabled() -> bool:
    return os.environ.get("TERMX_DESKTOP") == "1"


def emit(event: dict[str, Any]) -> None:
    """Write one structured line for the desktop host; no-op outside desktop mode."""
    if not desktop_enabled():
        return
    line = json.dumps(event, ensure_ascii=True)
    with _LOCK:
        stream = sys.stdout
        if stream is None:
            return
        try:
            stream.write(line + "\n")
            stream.flush()
        except (OSError, ValueError):
            return


def notify(title: str, body: str = "", *, kind: str = "info", url: str | None = None) -> None:
    event: dict[str, Any] = {"termx": "notify", "title": title, "body": body, "kind": kind}
    if url:
        event["url"] = url
    emit(event)


def permission(which: str) -> None:
    """Ask the desktop shell to run the native permission prompt (macOS TCC)."""
    emit({"termx": "permission", "which": which})


def ready(port: int, urls: list[str], tunnel: str | None = None) -> None:
    emit({"termx": "ready", "port": port, "urls": list(urls), "tunnel": tunnel})
