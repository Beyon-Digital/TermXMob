from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from termx.config import config_dir

_DROP = frozenset({"data", "command_body", "pty", "output"})
_lock = threading.Lock()


def _audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def log_event(kind: str, **fields: Any) -> None:
    event: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind}
    event.update((key, value) for key, value in fields.items() if key not in _DROP)
    line = json.dumps(event, ensure_ascii=True) + "\n"
    path = _audit_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        if not existed:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def read_events(limit: int = 100) -> list[dict]:
    path = _audit_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    if limit < 0:
        return events
    return events[-limit:]
