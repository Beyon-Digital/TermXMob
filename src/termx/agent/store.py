from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import uuid
from pathlib import Path
from time import time
from typing import Any

from termx.config import config_dir

ACTIVE_STATUSES = frozenset({"planning", "awaiting_approval", "running", "paused", "cancelling"})
TASK_STATUSES = frozenset({*ACTIVE_STATUSES, "cancelled", "failed", "completed"})
TASK_FIELDS = frozenset(
    {
        "status",
        "plan",
        "result",
        "error",
        "previous_response_id",
        "runtime",
        "updated_at",
    }
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class AgentStore:
    """Thread-safe durable Agent ledger.

    SQLite owns ordered task state and small replay payloads. Large outputs and
    screenshots are content-addressed files so reconnect and export do not need
    to keep model observations in process memory.
    """

    def __init__(self, path: Path | None = None, artifact_dir: Path | None = None) -> None:
        self.path = path or (config_dir() / "agent.sqlite3")
        self.artifact_dir = artifact_dir or (config_dir() / "agent-artifacts")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.artifact_dir, stat.S_IRWXU)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        if os.name == "posix" and self.path.exists():
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def _migrate(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                secret_configured INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                cwd TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                limits TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'agent',
                plan TEXT,
                result TEXT,
                error TEXT,
                previous_response_id TEXT,
                runtime TEXT,
                next_sequence INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(task_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                resolved_at REAL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                mime TEXT NOT NULL,
                path TEXT NOT NULL,
                size INTEGER NOT NULL,
                digest TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_task_sequence ON events(task_id, sequence);
            CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated_at DESC);
            CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id, created_at);
            """
        )
        # Additive migration for databases created before the Chat mode column.
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(tasks)")}
        if "mode" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN mode TEXT NOT NULL DEFAULT 'agent'")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # Providers ---------------------------------------------------------

    def put_provider(
        self,
        provider_id: str,
        *,
        kind: str,
        name: str,
        base_url: str,
        model: str,
        capabilities: list[str],
        secret_configured: bool | None = None,
    ) -> dict[str, Any]:
        now = time()
        provider_id = provider_id.strip()
        if not provider_id:
            raise ValueError("provider id is required")
        with self._lock:
            current = self._db.execute(
                "SELECT secret_configured, created_at FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
            configured = bool(current["secret_configured"]) if current else False
            if secret_configured is not None:
                configured = secret_configured
            created = float(current["created_at"]) if current else now
            self._db.execute(
                """
                INSERT INTO providers
                    (id, kind, name, base_url, model, capabilities, secret_configured, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    name=excluded.name,
                    base_url=excluded.base_url,
                    model=excluded.model,
                    capabilities=excluded.capabilities,
                    secret_configured=excluded.secret_configured,
                    updated_at=excluded.updated_at
                """,
                (
                    provider_id,
                    kind,
                    name.strip() or provider_id,
                    base_url.rstrip("/"),
                    model.strip(),
                    _json(sorted(set(capabilities))),
                    int(configured),
                    created,
                    now,
                ),
            )
            self._db.commit()
        provider = self.get_provider(provider_id)
        if provider is None:  # pragma: no cover - defensive
            raise RuntimeError("provider was not saved")
        return provider

    def list_providers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM providers ORDER BY updated_at DESC").fetchall()
        return [self._provider(row) for row in rows]

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
        return self._provider(row) if row else None

    def set_provider_secret_state(self, provider_id: str, configured: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE providers SET secret_configured = ?, updated_at = ? WHERE id = ?",
                (int(configured), time(), provider_id),
            )
            self._db.commit()

    def delete_provider(self, provider_id: str) -> bool:
        with self._lock:
            used = self._db.execute(
                "SELECT 1 FROM tasks WHERE provider_id = ? LIMIT 1", (provider_id,)
            ).fetchone()
            if used:
                raise ValueError("provider is referenced by task history")
            cursor = self._db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            self._db.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _provider(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "base_url": row["base_url"],
            "model": row["model"],
            "capabilities": _load_json(row["capabilities"], []),
            "secret_configured": bool(row["secret_configured"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # Tasks and events --------------------------------------------------

    def create_task(
        self,
        *,
        prompt: str,
        cwd: str,
        provider_id: str,
        model: str,
        limits: dict[str, Any],
        mode: str = "agent",
    ) -> dict[str, Any]:
        now = time()
        task_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                """
                INSERT INTO tasks
                    (id, prompt, cwd, provider_id, model, status, limits, mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'planning', ?, ?, ?, ?)
                """,
                (task_id, prompt, cwd, provider_id, model, _json(limits), mode, now, now),
            )
            self._db.commit()
        task = self.get_task(task_id)
        if task is None:  # pragma: no cover
            raise RuntimeError("task was not created")
        return task

    def get_task(self, task_id: str, *, include_events: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = self._task(row)
        if include_events:
            task["events"] = self.events(task_id)
            task["approvals"] = self.approvals(task_id)
            task["artifacts"] = self.artifacts(task_id)
        return task

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._task(row) for row in rows]

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        invalid = set(changes) - TASK_FIELDS
        if invalid:
            raise ValueError(f"invalid task fields: {', '.join(sorted(invalid))}")
        if "status" in changes and changes["status"] not in TASK_STATUSES:
            raise ValueError("invalid task status")
        changes.setdefault("updated_at", time())
        encoded: dict[str, Any] = {}
        for key, value in changes.items():
            encoded[key] = _json(value) if key in {"plan", "runtime"} and value is not None else value
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?", (*encoded.values(), task_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            self._db.commit()
        task = self.get_task(task_id)
        if task is None:  # pragma: no cover
            raise KeyError(task_id)
        return task

    def append_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = uuid.uuid4().hex
        created = time()
        with self._lock:
            row = self._db.execute(
                "SELECT next_sequence FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            sequence = int(row["next_sequence"])
            self._db.execute(
                "INSERT INTO events (id, task_id, sequence, type, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, task_id, sequence, event_type, _json(payload), created),
            )
            self._db.execute(
                "UPDATE tasks SET next_sequence = ?, updated_at = ? WHERE id = ?",
                (sequence + 1, created, task_id),
            )
            self._db.commit()
        return {
            "id": event_id,
            "task_id": task_id,
            "sequence": sequence,
            "type": event_type,
            "payload": payload,
            "created_at": created,
        }

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE task_id = ? AND sequence > ? ORDER BY sequence",
                (task_id, max(0, after)),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "sequence": row["sequence"],
                "type": row["type"],
                "payload": _load_json(row["payload"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "prompt": row["prompt"],
            "cwd": row["cwd"],
            "provider_id": row["provider_id"],
            "model": row["model"],
            "status": row["status"],
            "limits": _load_json(row["limits"], {}),
            "mode": (row["mode"] if "mode" in row.keys() else "agent") or "agent",
            "plan": _load_json(row["plan"], None),
            "result": row["result"],
            "error": row["error"],
            "previous_response_id": row["previous_response_id"],
            "runtime": _load_json(row["runtime"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # Approvals ---------------------------------------------------------

    def create_approval(self, task_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        approval_id = uuid.uuid4().hex
        created = time()
        with self._lock:
            self._db.execute(
                "INSERT INTO approvals (id, task_id, kind, status, payload, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                (approval_id, task_id, kind, _json(payload), created),
            )
            self._db.commit()
        return {
            "id": approval_id,
            "task_id": task_id,
            "kind": kind,
            "status": "pending",
            "payload": payload,
            "created_at": created,
            "resolved_at": None,
        }

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._approval(row) if row else None

    def approvals(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at", (task_id,)
            ).fetchall()
        return [self._approval(row) for row in rows]

    def resolve_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"approved", "denied"}:
            raise ValueError("decision must be approved or denied")
        resolved = time()
        with self._lock:
            cursor = self._db.execute(
                "UPDATE approvals SET status = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
                (decision, resolved, approval_id),
            )
            if cursor.rowcount == 0:
                row = self._db.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
                if row is None:
                    raise KeyError(approval_id)
                raise ValueError("approval is already resolved")
            self._db.commit()
        approval = self.get_approval(approval_id)
        if approval is None:  # pragma: no cover
            raise KeyError(approval_id)
        return approval

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "status": row["status"],
            "payload": _load_json(row["payload"], {}),
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    # Artifacts and retention ------------------------------------------

    def save_artifact(self, task_id: str, kind: str, mime: str, data: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        suffix = ".jpg" if mime == "image/jpeg" else ".png" if mime == "image/png" else ".bin"
        destination = self.artifact_dir / f"{digest}{suffix}"
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
            if os.name == "posix":
                os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        artifact_id = uuid.uuid4().hex
        created = time()
        with self._lock:
            self._db.execute(
                "INSERT INTO artifacts (id, task_id, kind, mime, path, size, digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, task_id, kind, mime, str(destination), len(data), digest, created),
            )
            self._db.commit()
        return {
            "id": artifact_id,
            "task_id": task_id,
            "kind": kind,
            "mime": mime,
            "size": len(data),
            "digest": digest,
            "created_at": created,
        }

    def get_artifact(self, task_id: str, artifact_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM artifacts WHERE id = ? AND task_id = ?", (artifact_id, task_id)
            ).fetchone()
        return self._artifact(row, include_path=True) if row else None

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task_id,)
            ).fetchall()
        return [self._artifact(row) for row in rows]

    @staticmethod
    def _artifact(row: sqlite3.Row, *, include_path: bool = False) -> dict[str, Any]:
        item = {
            "id": row["id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "mime": row["mime"],
            "size": row["size"],
            "digest": row["digest"],
            "created_at": row["created_at"],
        }
        if include_path:
            item["path"] = row["path"]
        return item

    def export_task(self, task_id: str) -> dict[str, Any] | None:
        return self.get_task(task_id, include_events=True)

    def delete_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        if task["status"] in ACTIVE_STATUSES:
            raise ValueError("active tasks cannot be deleted")
        with self._lock:
            paths = [
                Path(row["path"])
                for row in self._db.execute(
                    "SELECT path FROM artifacts WHERE task_id = ?", (task_id,)
                ).fetchall()
            ]
            self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._db.commit()
            referenced = {
                row["path"] for row in self._db.execute("SELECT DISTINCT path FROM artifacts").fetchall()
            }
        for path in paths:
            if str(path) not in referenced:
                try:
                    path.unlink()
                except OSError:
                    pass
        return True

    def retention_policy(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, value FROM settings WHERE key IN ('retention_days', 'max_bytes')"
            ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        try:
            days = max(1, min(3650, int(values.get("retention_days", 30))))
        except (TypeError, ValueError):
            days = 30
        try:
            size = max(1_000_000, min(100_000_000_000, int(values.get("max_bytes", 500_000_000))))
        except (TypeError, ValueError):
            size = 500_000_000
        return {"retention_days": days, "max_bytes": size}

    def set_retention_policy(self, *, retention_days: int, max_bytes: int) -> dict[str, int]:
        policy = {
            "retention_days": max(1, min(3650, int(retention_days))),
            "max_bytes": max(1_000_000, min(100_000_000_000, int(max_bytes))),
        }
        with self._lock:
            self._db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, str(value)) for key, value in policy.items()],
            )
            self._db.commit()
        return policy

    def storage_status(
        self,
        *,
        retention_days: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        policy = self.retention_policy()
        retention_days = retention_days if retention_days is not None else policy["retention_days"]
        max_bytes = max_bytes if max_bytes is not None else policy["max_bytes"]
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(size), 0) AS total FROM artifacts").fetchone()
            tasks = self._db.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()
        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        artifact_bytes = int(row["total"] if row else 0)
        return {
            "bytes": artifact_bytes + database_bytes,
            "artifact_bytes": artifact_bytes,
            "database_bytes": database_bytes,
            "task_count": int(tasks["count"] if tasks else 0),
            "retention_days": retention_days,
            "max_bytes": max_bytes,
        }

    def prune(self, *, retention_days: int | None = None, max_bytes: int | None = None) -> list[str]:
        policy = self.retention_policy()
        retention_days = retention_days if retention_days is not None else policy["retention_days"]
        max_bytes = max_bytes if max_bytes is not None else policy["max_bytes"]
        cutoff = time() - max(1, retention_days) * 86400
        removed: list[str] = []
        for task in reversed(self.list_tasks(limit=500)):
            if task["status"] in ACTIVE_STATUSES:
                continue
            over_age = float(task["updated_at"]) < cutoff
            over_size = self.storage_status(max_bytes=max_bytes)["bytes"] > max_bytes
            if not over_age and not over_size:
                continue
            if self.delete_task(task["id"]):
                removed.append(task["id"])
        return removed
