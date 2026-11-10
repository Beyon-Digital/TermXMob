"""Readiness waits that keep working with many open files.

select.select() cannot watch descriptors numbered 1024 or higher and raises
for them, so a busy host process would see terminals and helper pipes end
silently. selectors.DefaultSelector uses epoll or kqueue instead.
"""
from __future__ import annotations

import selectors
from typing import Any


def wait_readable(source: Any, timeout: float) -> bool:
    """True when `source` (descriptor or file object) has data or has reached end of file.

    Raises OSError or ValueError for a closed or invalid descriptor, as
    select.select did, so callers keep their existing error handling.
    """
    with selectors.DefaultSelector() as selector:
        selector.register(source, selectors.EVENT_READ)
        return bool(selector.select(timeout))
