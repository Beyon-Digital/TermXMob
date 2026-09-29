"""Activity Center — normalized, Termx-owned view of what's running on the host.

Aggregates agent tasks, PTY sessions, project previews, tunnels, port
forwards, and desktop displays/viewers into one record shape:

    {id, kind, project_id, title, state, started_at, updated_at, actions[]}

Deliberately scoped to Termx-owned surfaces — arbitrary host processes are
never exposed here (PROD-005 owns scoped port/process discovery).
"""

from __future__ import annotations

import os
import time
from typing import Any

ACTIVE_TASK_STATES = {
    "planning",
    "awaiting_plan",
    "running",
    "awaiting_approval",
    "cancelling",
    "recovering",
    "recovery_confirmation_required",
}


def _project_for(projects: list[dict[str, Any]], path: str | None) -> str | None:
    """Longest registered-project root that prefixes `path`, else None."""
    if not path:
        return None
    norm = os.path.normpath(path)
    best: dict[str, Any] | None = None
    for project in projects:
        root = project.get("path")
        if not root:
            continue
        root = os.path.normpath(str(root))
        if norm == root or norm.startswith(root + os.sep):
            if best is None or len(root) > len(str(best["path"])):
                best = project
    return str(best["id"]) if best else None


def _record(
    *,
    id: str,
    kind: str,
    project_id: str | None,
    title: str,
    state: str,
    started_at: float | None,
    updated_at: float | None,
    actions: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "project_id": project_id,
        "title": title,
        "state": state,
        "started_at": started_at,
        "updated_at": updated_at,
        "actions": actions,
    }
    if extra:
        record.update(extra)
    return record


def activity_snapshot(state: Any) -> dict[str, Any]:
    """Termx-owned activity normalized for the Activity Center."""
    try:
        projects = state.projects.projects()
    except Exception:
        projects = []
    items: list[dict[str, Any]] = []

    for task in state.agent_store.list_tasks(limit=200):
        active = task["status"] in ACTIVE_TASK_STATES
        actions = ["open"]
        if active:
            actions += ["stop", "cancel"]
        title = (task.get("prompt") or "").strip().splitlines()[0][:120] or "Agent task"
        items.append(
            _record(
                id=f"agent:{task['id']}",
                kind="agent",
                project_id=_project_for(projects, task.get("cwd")),
                title=title,
                state=task["status"],
                started_at=task.get("created_at"),
                updated_at=task.get("updated_at"),
                actions=actions,
                extra={"mode": task.get("mode"), "provider_id": task.get("provider_id")},
            )
        )

    for session in state.sessions.list():
        snap = session.snapshot()
        last_output = getattr(session, "_last_output_at", 0.0) or None
        running = not snap.get("exited")
        items.append(
            _record(
                id=f"terminal:{snap['id']}",
                kind="terminal",
                project_id=_project_for(projects, snap.get("live_cwd") or snap.get("cwd")),
                title=str(snap.get("title") or "Terminal"),
                state="running" if running else "exited",
                started_at=snap.get("created_at"),
                updated_at=last_output or snap.get("created_at"),
                actions=["open", "stop"] if running else ["open", "close"],
                extra={"shell": snap.get("shell"), "exit_code": snap.get("exit_code")},
            )
        )

    for project in projects:
        try:
            previews = state.projects.previews(project["id"])
        except Exception:
            continue
        for preview in previews:
            items.append(
                _record(
                    id=f"preview:{preview['id']}",
                    kind="preview",
                    project_id=str(project["id"]),
                    title=str(preview.get("name") or preview.get("url") or "Preview"),
                    state="active",
                    started_at=project.get("updated_at"),
                    updated_at=project.get("updated_at"),
                    actions=["open", "delete"],
                    extra={"url": preview.get("url")},
                )
            )

    try:
        tunnel = state.tunnels.status_public()
    except Exception:
        tunnel = {"state": "stopped"}
    if tunnel.get("state") and tunnel.get("state") != "stopped":
        items.append(
            _record(
                id="tunnel:active",
                kind="tunnel",
                project_id=None,
                title=str(tunnel.get("provider") or "Tunnel"),
                state=str(tunnel.get("state")),
                started_at=tunnel.get("started_at"),
                updated_at=tunnel.get("started_at"),
                actions=["open", "stop", "restart"],
                extra={"url": tunnel.get("url"), "detail": tunnel.get("detail")},
            )
        )

    try:
        rules = {r.id: r for r in state.store.list_rules()}
        statuses = state.forwards.statuses()
    except Exception:
        rules, statuses = {}, []
    for status in statuses:
        if status.get("state") in (None, "stopped"):
            continue
        rule = rules.get(status["id"])
        title = (
            rule.name
            if rule is not None and rule.name
            else f":{rule.listen_port} → {rule.target_host}:{rule.target_port}"
            if rule is not None
            else str(status["id"])
        )
        items.append(
            _record(
                id=f"forward:{status['id']}",
                kind="forward",
                project_id=None,
                title=title,
                state=str(status.get("state")),
                started_at=status.get("started_at"),
                updated_at=status.get("started_at"),
                actions=["stop"],
                extra={
                    "listen_port": rule.listen_port if rule else None,
                    "target": f"{rule.target_host}:{rule.target_port}" if rule else None,
                },
            )
        )

    try:
        desktop = state.desktop.snapshot()
    except Exception:
        desktop = {"displays": [], "view_only": True}
    for display in desktop.get("displays", []):
        if not display.get("virtual"):
            continue
        items.append(
            _record(
                id=f"display:{display['id']}",
                kind="desktop",
                project_id=None,
                title=str(display.get("name") or display["id"]),
                state="active" if display.get("owner") else "idle",
                started_at=display.get("created_at"),
                updated_at=display.get("expires_at"),
                actions=["open", "stop"],
                extra={"owner": display.get("owner"), "width": display.get("width"), "height": display.get("height")},
            )
        )
    viewers = int(desktop.get("viewer_count") or 0)
    if viewers:
        items.append(
            _record(
                id="desktop:viewers",
                kind="desktop",
                project_id=None,
                title=f"{viewers} desktop viewer{'s' if viewers != 1 else ''}",
                state="viewing" if desktop.get("view_only") else "controlling",
                started_at=None,
                updated_at=None,
                actions=["open"],
                extra={"view_only": desktop.get("view_only")},
            )
        )

    return {"activity": items, "count": len(items), "generated_at": time.time()}
