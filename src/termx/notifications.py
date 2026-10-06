"""Host-side notification center.

Real host-driven notifications: an in-memory ring buffer plus pub/sub for
GraphQL subscribers. ``AgentManager._emit`` fans task events out to the
registered bridge, which converts noteworthy events (task completion,
failures, approvals, assistant messages) into notifications clients can
list, mark read, and stream live.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from time import time
from typing import Any, Callable

MAX_ITEMS = 500

_NOTIFY_KIND = {
    "task.completed": ("success", "Agent task completed"),
    "task.failed": ("error", "Agent task failed"),
    "task.cancelled": ("info", "Agent task cancelled"),
    "approval.requested": ("warning", "Approval needed"),
}


class NotificationCenter:
    def __init__(self, max_items: int = MAX_ITEMS) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=max_items)
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def publish(
        self,
        title: str,
        body: str = "",
        *,
        kind: str = "info",
        url: str | None = None,
        level: str | None = None,
        source: str = "host",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "title": title,
            "body": body,
            "url": url,
            "level": level or kind,
            "source": source,
            "task_id": task_id,
            "read": False,
            "created_at": time(),
        }
        with self._lock:
            self._items.append(item)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._deliver, queue, item)
            except RuntimeError:
                self._subscribers.discard((loop, queue))
        return item

    @staticmethod
    def _deliver(queue: asyncio.Queue, item: dict[str, Any]) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def list(self, *, limit: int = 100, unread: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items)
        if unread:
            items = [item for item in items if not item["read"]]
        return items[-limit:][::-1]

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._items if not item["read"])

    def mark_read(self, ids: list[str]) -> int:
        wanted = set(ids)
        marked = 0
        with self._lock:
            for item in self._items:
                if item["id"] in wanted and not item["read"]:
                    item["read"] = True
                    marked += 1
        return marked

    def mark_all_read(self) -> int:
        marked = 0
        with self._lock:
            for item in self._items:
                if not item["read"]:
                    item["read"] = True
                    marked += 1
        return marked

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = {(loop, q) for loop, q in self._subscribers if q is not queue}

    # -- agent event bridge ------------------------------------------------

    def bridge_agent(self, manager: Any, store: Any) -> None:
        """Register a listener that turns task events into notifications."""
        listeners = getattr(manager, "_listeners", None)
        if listeners is None:
            return

        def on_event(task_id: str, event: dict[str, Any]) -> None:
            kind, title = _NOTIFY_KIND.get(event.get("type") or "", (None, None))
            if kind is not None:
                task = store.get_task(task_id) or {}
                prompt = str(task.get("prompt") or "").strip().splitlines()
                label = (prompt[0][:100] if prompt else "") or task_id
                body = ""
                payload = event.get("payload") or {}
                if kind == "error":
                    body = str(payload.get("error") or task.get("error") or "")[:400]
                elif event["type"] == "approval.requested":
                    body = str(payload.get("title") or "An action needs your approval")[:400]
                self.publish(
                    f"{title}: {label}",
                    body,
                    kind=kind,
                    source="agent",
                    task_id=task_id,
                )
                return
            if event.get("type") == "assistant.message":
                payload = event.get("payload") or {}
                text = str(payload.get("text") or "").strip()
                if text:
                    self.publish(
                        "Agent message",
                        text[:400],
                        kind="message",
                        source="agent",
                        task_id=task_id,
                    )

        listeners.add(on_event)
