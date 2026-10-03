from __future__ import annotations

import hashlib
import re
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

def configured_models(model: str) -> list[str]:
    found: list[str] = []
    for part in re.split(r"[\n,]", model or ""):
        item = part.strip()
        if item and item not in found:
            found.append(item)
    return found


ACTIVE_STATUSES = frozenset({
    "planning",
    "awaiting_approval",
    "running",
    "paused",
    "cancelling",
    "recovering",
    "recovery_confirmation_required",
})
TASK_STATUSES = frozenset({*ACTIVE_STATUSES, "cancelled", "failed", "completed"})

CHECKPOINT_SIDE_EFFECT_STATES = frozenset(
    {"none", "prepared", "running", "completed_uncommitted", "committed"}
)
CHECKPOINT_KINDS = frozenset({"execution", "context"})
TASK_FIELDS = frozenset(
    {
        "status",
        "plan",
        "result",
        "error",
        "previous_response_id",
        "runtime",
        "metrics",
        "engine",
        "engine_session_id",
        "engine_native_id",
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
                parent_id TEXT,
                mode TEXT NOT NULL DEFAULT 'agent',
                plan TEXT,
                result TEXT,
                error TEXT,
                previous_response_id TEXT,
                runtime TEXT,
                metrics TEXT,
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
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                history_cursor INTEGER NOT NULL DEFAULT 0,
                plan_step INTEGER NOT NULL DEFAULT 0,
                pending_call_id TEXT,
                side_effect_state TEXT NOT NULL DEFAULT 'none',
                payload TEXT NOT NULL DEFAULT '{}',
                result TEXT,
                resumable INTEGER NOT NULL DEFAULT 1,
                reason TEXT,
                provider_turn_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                project_id TEXT,
                cwd TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                draft INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'ask',
                custom_agent_id TEXT,
                provider_id TEXT,
                model TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                task_id TEXT,
                prompt TEXT NOT NULL DEFAULT '',
                mode TEXT,
                provider_id TEXT,
                model TEXT,
                created_at REAL NOT NULL,
                UNIQUE(conversation_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS conversation_context_refs (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL REFERENCES conversation_turns(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                ref TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                provider_id TEXT,
                model TEXT,
                tools TEXT NOT NULL DEFAULT '[]',
                limits TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_worktrees (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                mode TEXT NOT NULL DEFAULT 'worktree',
                base_repo TEXT NOT NULL,
                base_ref TEXT NOT NULL DEFAULT '',
                base_branch TEXT,
                worktree_path TEXT,
                branch TEXT,
                head_sha TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runbooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_id TEXT,
                steps TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runbook_runs (
                id TEXT PRIMARY KEY,
                runbook_id TEXT NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                cwd TEXT,
                current_step INTEGER NOT NULL DEFAULT -1,
                step_results TEXT NOT NULL DEFAULT '[]',
                steps TEXT,
                error TEXT,
                started_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS engine_sessions (
                binding_id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                engine TEXT NOT NULL,
                native_session_id TEXT NOT NULL,
                conversation_id TEXT,
                cwd TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_rules (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL DEFAULT 1,
                effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
                scope_type TEXT NOT NULL CHECK(scope_type IN ('task','project','custom_agent','host')),
                scope_id TEXT,
                action_type TEXT NOT NULL DEFAULT 'tool'
                    CHECK(action_type IN ('tool','capability','publication','computer')),
                tool TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                fingerprint_kind TEXT NOT NULL DEFAULT 'exact'
                    CHECK(fingerprint_kind IN ('exact','conservative')),
                matcher_json TEXT NOT NULL DEFAULT '{}',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                sandbox_profile TEXT,
                source_approval_id TEXT,
                task_id TEXT,
                project_id TEXT,
                display TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_used_at REAL,
                expires_at REAL,
                times_used INTEGER NOT NULL DEFAULT 0,
                revoked_at REAL
            );
            CREATE INDEX IF NOT EXISTS events_task_sequence ON events(task_id, sequence);
            CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated_at DESC);
            CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id, created_at);
            CREATE INDEX IF NOT EXISTS checkpoints_task ON checkpoints(task_id, kind, created_at);
            CREATE INDEX IF NOT EXISTS conversation_turns_conversation
                ON conversation_turns(conversation_id, sequence);
            CREATE INDEX IF NOT EXISTS conversation_refs_turn
                ON conversation_context_refs(turn_id);
            CREATE INDEX IF NOT EXISTS conversations_updated
                ON conversations(archived, pinned DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS policy_rules_fingerprint
                ON policy_rules(fingerprint, action_type);
            CREATE INDEX IF NOT EXISTS policy_rules_scope
                ON policy_rules(scope_type, scope_id);
            CREATE INDEX IF NOT EXISTS engine_sessions_task
                ON engine_sessions(task_id);
            CREATE INDEX IF NOT EXISTS engine_sessions_conversation
                ON engine_sessions(conversation_id);
            """
        )
        # Additive migration for databases created before the Chat mode column.
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(tasks)")}
        if "mode" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN mode TEXT NOT NULL DEFAULT 'agent'")
        if "metrics" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN metrics TEXT")
        if "parent_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")
        if "custom_agent_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN custom_agent_id TEXT")
        if "engine" not in columns:
            self._db.execute(
                "ALTER TABLE tasks ADD COLUMN engine TEXT NOT NULL DEFAULT 'internal'"
            )
        if "engine_session_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN engine_session_id TEXT")
        if "engine_native_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN engine_native_id TEXT")
        ca_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(custom_agents)")
        }
        if ca_columns and "approval_mode" not in ca_columns:
            self._db.execute(
                "ALTER TABLE custom_agents ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'standard'"
            )
        if ca_columns and "sandbox_profile" not in ca_columns:
            self._db.execute(
                "ALTER TABLE custom_agents ADD COLUMN sandbox_profile TEXT NOT NULL DEFAULT 'agent'"
            )
        wt_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(task_worktrees)")
        }
        if wt_columns and "base_branch" not in wt_columns:
            self._db.execute("ALTER TABLE task_worktrees ADD COLUMN base_branch TEXT")
        rr_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(runbook_runs)")
        }
        if rr_columns and "steps" not in rr_columns:
            self._db.execute("ALTER TABLE runbook_runs ADD COLUMN steps TEXT")
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
            "models": configured_models(row["model"]),
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
        parent_id: str | None = None,
        custom_agent_id: str | None = None,
        engine: str = "internal",
        status: str = "planning",
    ) -> dict[str, Any]:
        now = time()
        task_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                """
                INSERT INTO tasks
                    (id, prompt, cwd, provider_id, model, status, limits, mode, parent_id, custom_agent_id, engine, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, prompt, cwd, provider_id, model, status, _json(limits), mode, parent_id, custom_agent_id, engine, now, now),
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

    def children(self, parent_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at", (parent_id,)
            ).fetchall()
        return [self._task(row) for row in rows]

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
            encoded[key] = _json(value) if key in {"plan", "runtime", "metrics"} and value is not None else value
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

    # Engine sessions ---------------------------------------------------

    def save_engine_session(
        self,
        binding_id: str,
        *,
        task_id: str | None,
        engine: str,
        native_session_id: str,
        conversation_id: str | None = None,
        cwd: str = "",
        status: str = "active",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO engine_sessions
                    (binding_id, task_id, engine, native_session_id, conversation_id,
                     cwd, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(binding_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    native_session_id=excluded.native_session_id,
                    conversation_id=excluded.conversation_id,
                    status=excluded.status,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (binding_id, task_id, engine, native_session_id, conversation_id,
                 cwd, status, _json(payload or {}), now, now),
            )
            self._db.commit()
        row = self.get_engine_session(binding_id)
        assert row is not None
        return row

    def get_engine_session(self, binding_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM engine_sessions WHERE binding_id = ?", (binding_id,)
            ).fetchone()
        return self._engine_session(row) if row else None

    def engine_session_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM engine_sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._engine_session(row) if row else None

    def engine_session_for_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT * FROM engine_sessions
                WHERE conversation_id = ? AND status IN ('active','idle')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return self._engine_session(row) if row else None

    def update_engine_session(
        self, binding_id: str, *, task_id: str | None = None,
        status: str | None = None, payload: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        changes: dict[str, Any] = {"updated_at": time()}
        if task_id is not None:
            changes["task_id"] = task_id
        if status is not None:
            changes["status"] = status
        if payload is not None:
            changes["payload"] = _json(payload)
        if conversation_id is not None:
            changes["conversation_id"] = conversation_id
        assignments = ", ".join(f"{k} = ?" for k in changes)
        with self._lock:
            self._db.execute(
                f"UPDATE engine_sessions SET {assignments} WHERE binding_id = ?",
                (*changes.values(), binding_id),
            )
            self._db.commit()
        return self.get_engine_session(binding_id)

    def list_engine_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM engine_sessions"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY updated_at DESC", args).fetchall()
        return [self._engine_session(row) for row in rows]

    @staticmethod
    def _engine_session(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "binding_id": row["binding_id"],
            "task_id": row["task_id"],
            "engine": row["engine"],
            "native_session_id": row["native_session_id"],
            "conversation_id": row["conversation_id"],
            "cwd": row["cwd"],
            "status": row["status"],
            "payload": _load_json(row["payload"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # Checkpoints ---------------------------------------------------------

    def create_checkpoint(
        self,
        task_id: str,
        *,
        kind: str,
        history_cursor: int = 0,
        plan_step: int = 0,
        pending_call_id: str | None = None,
        side_effect_state: str = "none",
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        resumable: bool = True,
        reason: str | None = None,
        provider_turn_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in CHECKPOINT_KINDS:
            raise ValueError(f"invalid checkpoint kind: {kind}")
        if side_effect_state not in CHECKPOINT_SIDE_EFFECT_STATES:
            raise ValueError(f"invalid side_effect_state: {side_effect_state}")
        checkpoint_id = uuid.uuid4().hex
        created = time()
        with self._lock:
            self._db.execute(
                "INSERT INTO checkpoints (id, task_id, kind, history_cursor, plan_step,"
                " pending_call_id, side_effect_state, payload, result, resumable,"
                " reason, provider_turn_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    task_id,
                    kind,
                    int(history_cursor),
                    int(plan_step),
                    pending_call_id,
                    side_effect_state,
                    _json(payload or {}),
                    _json(result) if result is not None else None,
                    1 if resumable else 0,
                    reason,
                    provider_turn_id,
                    created,
                ),
            )
            self._db.commit()
        checkpoint = self.get_checkpoint(checkpoint_id)
        if checkpoint is None:  # pragma: no cover
            raise KeyError(checkpoint_id)
        return checkpoint

    def update_checkpoint(self, checkpoint_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "history_cursor",
            "plan_step",
            "pending_call_id",
            "side_effect_state",
            "payload",
            "result",
            "resumable",
            "reason",
            "provider_turn_id",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"invalid checkpoint fields: {', '.join(sorted(invalid))}")
        state = changes.get("side_effect_state")
        if state is not None and state not in CHECKPOINT_SIDE_EFFECT_STATES:
            raise ValueError(f"invalid side_effect_state: {state}")
        encoded: dict[str, Any] = {}
        for key, value in changes.items():
            if key in {"payload", "result"}:
                encoded[key] = _json(value) if value is not None else None
            elif key == "resumable":
                encoded[key] = 1 if value else 0
            else:
                encoded[key] = value
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE checkpoints SET {assignments} WHERE id = ?",
                (*encoded.values(), checkpoint_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(checkpoint_id)
            self._db.commit()
        checkpoint = self.get_checkpoint(checkpoint_id)
        if checkpoint is None:  # pragma: no cover
            raise KeyError(checkpoint_id)
        return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
            ).fetchone()
        return self._checkpoint(row) if row is not None else None

    def checkpoints(self, task_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM checkpoints WHERE task_id = ?"
        args: list[Any] = [task_id]
        if kind is not None:
            query += " AND kind = ?"
            args.append(kind)
        query += " ORDER BY created_at, rowid"
        with self._lock:
            rows = self._db.execute(query, args).fetchall()
        return [self._checkpoint(row) for row in rows]

    def latest_checkpoint(self, task_id: str, kind: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM checkpoints WHERE task_id = ?"
        args: list[Any] = [task_id]
        if kind is not None:
            query += " AND kind = ?"
            args.append(kind)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._lock:
            row = self._db.execute(query, args).fetchone()
        return self._checkpoint(row) if row is not None else None

    @staticmethod
    def _checkpoint(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "history_cursor": row["history_cursor"],
            "plan_step": row["plan_step"],
            "pending_call_id": row["pending_call_id"],
            "side_effect_state": row["side_effect_state"],
            "payload": _load_json(row["payload"], {}),
            "result": _load_json(row["result"], None),
            "resumable": bool(row["resumable"]),
            "reason": row["reason"],
            "provider_turn_id": row["provider_turn_id"],
            "created_at": row["created_at"],
        }

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
            "parent_id": (row["parent_id"] if "parent_id" in row.keys() else None) or None,
            "custom_agent_id": (
                row["custom_agent_id"] if "custom_agent_id" in row.keys() else None
            )
            or None,
            "plan": _load_json(row["plan"], None),
            "result": row["result"],
            "error": row["error"],
            "previous_response_id": row["previous_response_id"],
            "engine": (row["engine"] if "engine" in row.keys() else "internal") or "internal",
            "engine_session_id": (
                row["engine_session_id"] if "engine_session_id" in row.keys() else None
            ),
            "engine_native_id": (
                row["engine_native_id"] if "engine_native_id" in row.keys() else None
            ),
            "runtime": _load_json(row["runtime"], {}),
            "metrics": _load_json(row["metrics"], {}) if "metrics" in row.keys() else {},
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
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }.get(mime, ".bin")
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

    @staticmethod
    def _conversation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "project_id": row["project_id"],
            "cwd": row["cwd"],
            "pinned": bool(row["pinned"]),
            "archived": bool(row["archived"]),
            "draft": bool(row["draft"]),
            "mode": row["mode"],
            "custom_agent_id": row["custom_agent_id"],
            "provider_id": row["provider_id"],
            "model": row["model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _turn(row: sqlite3.Row, ref_rows: list[sqlite3.Row]) -> dict[str, Any]:
        context_refs = []
        attachment_refs = []
        for ref in ref_rows:
            try:
                meta = json.loads(ref["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            item = {"id": ref["id"], "ref": ref["ref"], "meta": meta}
            (attachment_refs if ref["kind"] == "attachment" else context_refs).append(item)
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "sequence": row["sequence"],
            "task_id": row["task_id"],
            "prompt": row["prompt"],
            "mode": row["mode"],
            "provider_id": row["provider_id"],
            "model": row["model"],
            "context_refs": context_refs,
            "attachment_refs": attachment_refs,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _custom_agent(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tools = json.loads(row["tools"] or "[]")
        except json.JSONDecodeError:
            tools = []
        try:
            limits = json.loads(row["limits"] or "{}")
        except json.JSONDecodeError:
            limits = {}
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "instructions": row["instructions"],
            "provider_id": row["provider_id"],
            "model": row["model"],
            "tools": tools,
            "limits": limits,
            "approval_mode": (
                row["approval_mode"] if "approval_mode" in row.keys() else None
            )
            or "standard",
            "sandbox_profile": (
                row["sandbox_profile"] if "sandbox_profile" in row.keys() else None
            )
            or "agent",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

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

    # Conversations -------------------------------------------------------

    def create_conversation(
        self,
        *,
        title: str = "",
        project_id: str | None = None,
        cwd: str | None = None,
        mode: str = "ask",
        custom_agent_id: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        pinned: bool = False,
        archived: bool = False,
        draft: bool = False,
    ) -> dict[str, Any]:
        now = time()
        conversation_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._db.execute(
                """
                INSERT INTO conversations
                    (id, title, project_id, cwd, pinned, archived, draft, mode,
                     custom_agent_id, provider_id, model, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    title,
                    project_id,
                    cwd,
                    int(pinned),
                    int(archived),
                    int(draft),
                    mode,
                    custom_agent_id,
                    provider_id,
                    model,
                    now,
                    now,
                ),
            )
            self._db.commit()
        return self.get_conversation(conversation_id)  # type: ignore[return-value]

    def list_conversations(
        self, *, archived: bool | None = False, limit: int = 200
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM conversations"
        params: list[Any] = []
        if archived is not None:
            query += " WHERE archived = ?"
            params.append(int(archived))
        query += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [self._conversation(row) for row in rows]

    def get_conversation(
        self, conversation_id: str, *, include_turns: bool = False
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return None
            conversation = self._conversation(row)
            if include_turns:
                turn_rows = self._db.execute(
                    "SELECT * FROM conversation_turns WHERE conversation_id = ? ORDER BY sequence",
                    (conversation_id,),
                ).fetchall()
                turns = []
                for turn_row in turn_rows:
                    ref_rows = self._db.execute(
                        "SELECT * FROM conversation_context_refs WHERE turn_id = ? ORDER BY created_at",
                        (turn_row["id"],),
                    ).fetchall()
                    turns.append(self._turn(turn_row, ref_rows))
                conversation["turns"] = turns
        return conversation

    def update_conversation(
        self, conversation_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        allowed = {
            "title",
            "project_id",
            "cwd",
            "mode",
            "custom_agent_id",
            "provider_id",
            "model",
            "pinned",
            "archived",
            "draft",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_conversation(conversation_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = [
            int(value) if key in {"pinned", "archived", "draft"} else value
            for key, value in updates.items()
        ]
        params.append(time())
        params.append(conversation_id)
        with self._lock:
            self._db.execute(
                f"UPDATE conversations SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )
            self._db.commit()
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            self._db.commit()
        return cursor.rowcount > 0

    def add_conversation_turn(
        self,
        conversation_id: str,
        *,
        prompt: str,
        task_id: str | None = None,
        mode: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        context_refs: list[dict[str, Any]] | None = None,
        attachment_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.get_conversation(conversation_id) is None:
            raise KeyError(conversation_id)
        now = time()
        turn_id = uuid.uuid4().hex[:16]
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM conversation_turns WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            sequence = int(row["next"] if row else 1)
            self._db.execute(
                """
                INSERT INTO conversation_turns
                    (id, conversation_id, sequence, task_id, prompt, mode,
                     provider_id, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    conversation_id,
                    sequence,
                    task_id,
                    prompt,
                    mode,
                    provider_id,
                    model,
                    now,
                ),
            )
            for kind, refs in (("context", context_refs or []), ("attachment", attachment_refs or [])):
                for ref in refs:
                    ref_id = uuid.uuid4().hex[:16]
                    target = ref.get("ref") or ref.get("path") or ref.get("id")
                    if not target:
                        continue
                    # Clients send either a flat ref ({ref, cwd, start}) or a
                    # structured one ({ref, meta: {cwd, start}}) — fold an
                    # incoming meta object into the stored metadata instead of
                    # nesting it under a literal "meta" key.
                    meta = {key: value for key, value in ref.items() if key not in {"ref", "path", "id", "kind", "meta"}}
                    inner = ref.get("meta")
                    if isinstance(inner, dict):
                        meta = {**inner, **meta}
                    self._db.execute(
                        """
                        INSERT INTO conversation_context_refs
                            (id, turn_id, kind, ref, meta, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (ref_id, turn_id, kind, str(target), json.dumps(meta), now),
                    )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            self._db.commit()
        conversation = self.get_conversation(conversation_id, include_turns=True)
        return next(turn for turn in conversation["turns"] if turn["id"] == turn_id)  # type: ignore[index]

    # Custom agents -------------------------------------------------------

    def create_custom_agent(
        self,
        *,
        name: str,
        description: str = "",
        instructions: str = "",
        provider_id: str | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        limits: dict[str, Any] | None = None,
        approval_mode: str = "standard",
        sandbox_profile: str = "agent",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("custom agent name is required")
        if approval_mode not in {"standard", "remember", "autonomous"}:
            raise ValueError("approval_mode must be standard, remember or autonomous")
        if sandbox_profile not in {"host", "workspace", "agent"}:
            raise ValueError("sandbox_profile must be host, workspace or agent")
        now = time()
        agent_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._db.execute(
                """
                INSERT INTO custom_agents
                    (id, name, description, instructions, provider_id, model,
                     tools, limits, approval_mode, sandbox_profile,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    name,
                    description,
                    instructions,
                    provider_id,
                    model,
                    json.dumps(list(tools or [])),
                    json.dumps(dict(limits or {})),
                    approval_mode,
                    sandbox_profile,
                    now,
                    now,
                ),
            )
            self._db.commit()
        return self.get_custom_agent(agent_id)  # type: ignore[return-value]

    def list_custom_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM custom_agents ORDER BY updated_at DESC"
            ).fetchall()
        return [self._custom_agent(row) for row in rows]

    def get_custom_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM custom_agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return self._custom_agent(row) if row else None

    def update_custom_agent(self, agent_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "name",
            "description",
            "instructions",
            "provider_id",
            "model",
            "tools",
            "limits",
            "approval_mode",
            "sandbox_profile",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_custom_agent(agent_id)
        if "approval_mode" in updates and updates["approval_mode"] not in {
            "standard",
            "remember",
            "autonomous",
        }:
            raise ValueError("approval_mode must be standard, remember or autonomous")
        if "sandbox_profile" in updates and updates["sandbox_profile"] not in {
            "host",
            "workspace",
            "agent",
        }:
            raise ValueError("sandbox_profile must be host, workspace or agent")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = [
            json.dumps(value) if key in {"tools", "limits"} else value
            for key, value in updates.items()
        ]
        params.append(time())
        params.append(agent_id)
        with self._lock:
            self._db.execute(
                f"UPDATE custom_agents SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )
            self._db.commit()
        return self.get_custom_agent(agent_id)

    def delete_custom_agent(self, agent_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM custom_agents WHERE id = ?", (agent_id,)
            )
            self._db.commit()
        return cursor.rowcount > 0

    # Policy rules ------------------------------------------------------

    def create_policy_rule(
        self,
        *,
        effect: str,
        scope_type: str,
        scope_id: str | None,
        action_type: str,
        tool: str,
        fingerprint: str,
        fingerprint_kind: str = "exact",
        matcher: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        sandbox_profile: str | None = None,
        source_approval_id: str | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        display: str = "",
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        if effect not in {"allow", "deny"}:
            raise ValueError("effect must be allow or deny")
        if scope_type not in {"task", "project", "custom_agent", "host"}:
            raise ValueError("invalid scope_type")
        if action_type not in {"tool", "capability", "publication", "computer"}:
            raise ValueError("invalid action_type")
        if fingerprint_kind not in {"exact", "conservative"}:
            raise ValueError("invalid fingerprint_kind")
        if scope_type != "host" and not scope_id:
            raise ValueError("non-host rules require scope_id")
        if action_type == "capability":
            # A capability rule must name capabilities — and can never name the
            # ungrantable set, which no sandbox backend may provide.
            from termx.agent.policies.defaults import UNGRANTABLE_CAPABILITIES

            if not capabilities:
                raise ValueError("capability rules require a non-empty capabilities list")
            bad = [c for c in capabilities if c in UNGRANTABLE_CAPABILITIES]
            if bad:
                raise ValueError(f"capabilities are ungrantable: {', '.join(bad)}")
        rule_id = uuid.uuid4().hex[:16]
        now = time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO policy_rules
                    (id, effect, scope_type, scope_id, action_type, tool,
                     fingerprint, fingerprint_kind, matcher_json, capabilities_json,
                     sandbox_profile, source_approval_id, task_id, project_id,
                     display, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    effect,
                    scope_type,
                    scope_id,
                    action_type,
                    tool,
                    fingerprint,
                    fingerprint_kind,
                    _json(dict(matcher or {})),
                    _json(sorted(set(capabilities or []))),
                    sandbox_profile,
                    source_approval_id,
                    task_id,
                    project_id,
                    display,
                    now,
                    now,
                    expires_at,
                ),
            )
            self._db.commit()
        rule = self.get_policy_rule(rule_id)
        if rule is None:  # pragma: no cover
            raise RuntimeError("policy rule was not created")
        return rule

    def get_policy_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM policy_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._policy_rule(row) if row else None

    def list_policy_rules(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        effect: str | None = None,
        include_revoked: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type is not None:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if effect is not None:
            clauses.append("effect = ?")
            params.append(effect)
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM policy_rules {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, max(1, min(limit, 1000))),
            ).fetchall()
        return [self._policy_rule(row) for row in rows]

    def matching_policy_rules(
        self,
        *,
        fingerprint: str,
        action_type: str = "tool",
        scopes: list[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Active (non-revoked, non-expired) rules whose fingerprint matches and
        whose (scope_type, scope_id) is one of ``scopes`` — or whose scope is
        ``host`` (global). Task-scope rules only reach this point for their own
        task, so they die with the task."""
        params: list[Any] = [fingerprint, action_type, time()]
        scope_clause = "scope_type = 'host'"
        if scopes:
            scope_clause = "(scope_type = 'host' OR ({}) )".format(
                " OR ".join("(scope_type = ? AND scope_id = ?)" for _ in scopes)
            )
            for scope_type, scope_id in scopes:
                params.extend([scope_type, scope_id])
        with self._lock:
            rows = self._db.execute(
                f"""
                SELECT * FROM policy_rules
                WHERE fingerprint = ? AND action_type = ? AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?) AND {scope_clause}
                """,
                params,
            ).fetchall()
        return [self._policy_rule(row) for row in rows]

    def capability_rules(
        self, *, scopes: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        """Active capability rules in the given scopes (fingerprint-agnostic —
        capability rules match on their capability list, not command shape)."""
        params: list[Any] = [time()]
        scope_clause = "(scope_type = 'host' OR ({}))".format(
            " OR ".join("(scope_type = ? AND scope_id = ?)" for _ in scopes)
        )
        for scope_type, scope_id in scopes:
            params.extend([scope_type, scope_id])
        with self._lock:
            rows = self._db.execute(
                f"""
                SELECT * FROM policy_rules
                WHERE action_type = 'capability' AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?) AND {scope_clause}
                """,
                params,
            ).fetchall()
        return [self._policy_rule(row) for row in rows]

    def update_policy_rule(self, rule_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"effect", "expires_at", "matcher", "capabilities", "display"}
        encoded: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"invalid policy field: {key}")
            if key == "effect" and value not in {"allow", "deny"}:
                raise ValueError("effect must be allow or deny")
            if key == "capabilities":
                encoded["capabilities_json"] = _json(sorted(set(value or [])))
                continue
            encoded[key] = _json(value) if key == "matcher" else value
        if "matcher" in encoded:
            encoded["matcher_json"] = encoded.pop("matcher")
        encoded["updated_at"] = time()
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE policy_rules SET {assignments} WHERE id = ? AND revoked_at IS NULL",
                (*encoded.values(), rule_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)
            self._db.commit()
        rule = self.get_policy_rule(rule_id)
        if rule is None:  # pragma: no cover
            raise KeyError(rule_id)
        return rule

    def expire_task_policy_rules(self, task_id: str) -> int:
        """Expire every task-scoped rule for a task — called at terminal state
        so scoped trust can never outlive its task."""
        now = time()
        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE policy_rules SET expires_at = ?, updated_at = ?
                WHERE scope_type = 'task' AND scope_id = ?
                  AND revoked_at IS NULL AND expires_at IS NULL
                """,
                (now, now, task_id),
            )
            self._db.commit()
        return cursor.rowcount

    def touch_policy_rule(self, rule_id: str) -> None:
        with self._lock:
            self._db.execute(
                """
                UPDATE policy_rules
                SET last_used_at = ?, times_used = times_used + 1
                WHERE id = ?
                """,
                (time(), rule_id),
            )
            self._db.commit()

    def revoke_policy_rule(self, rule_id: str) -> dict[str, Any]:
        now = time()
        with self._lock:
            cursor = self._db.execute(
                "UPDATE policy_rules SET revoked_at = ?, updated_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, now, rule_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)
            self._db.commit()
        rule = self.get_policy_rule(rule_id)
        if rule is None:  # pragma: no cover
            raise KeyError(rule_id)
        return rule

    @staticmethod
    def _policy_rule(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "effect": row["effect"],
            "scope_type": row["scope_type"],
            "scope_id": row["scope_id"],
            "action_type": row["action_type"],
            "tool": row["tool"],
            "fingerprint": row["fingerprint"],
            "fingerprint_kind": row["fingerprint_kind"],
            "matcher": _load_json(row["matcher_json"], {}),
            "capabilities": _load_json(row["capabilities_json"], []),
            "sandbox_profile": row["sandbox_profile"],
            "source_approval_id": row["source_approval_id"],
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "display": row["display"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
            "expires_at": row["expires_at"],
            "times_used": row["times_used"],
            "revoked_at": row["revoked_at"],
        }

    # Runbooks --------------------------------------------------------------

    def create_runbook(
        self,
        *,
        name: str,
        steps: list[dict[str, Any]],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        runbook_id = uuid.uuid4().hex[:12]
        now = time()
        with self._lock:
            self._db.execute(
                "INSERT INTO runbooks (id, name, project_id, steps, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (runbook_id, name, project_id, _json(steps), now, now),
            )
            self._db.commit()
        return self.get_runbook(runbook_id)  # type: ignore[return-value]

    def list_runbooks(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if project_id:
                rows = self._db.execute(
                    "SELECT * FROM runbooks WHERE project_id = ? OR project_id IS NULL ORDER BY updated_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM runbooks ORDER BY updated_at DESC"
                ).fetchall()
        return [self._runbook(row) for row in rows]

    def get_runbook(self, runbook_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runbooks WHERE id = ?", (runbook_id,)
            ).fetchone()
        return self._runbook(row) if row else None

    def update_runbook(self, runbook_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "project_id", "steps"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_runbook(runbook_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [
            _json(value) if key == "steps" else value for key, value in updates.items()
        ]
        with self._lock:
            self._db.execute(
                f"UPDATE runbooks SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, time(), runbook_id),
            )
            self._db.commit()
        return self.get_runbook(runbook_id)

    def delete_runbook(self, runbook_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM runbooks WHERE id = ?", (runbook_id,)
            )
            self._db.commit()
        return cursor.rowcount > 0

    def create_runbook_run(
        self,
        runbook_id: str,
        *,
        cwd: str | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO runbook_runs (id, runbook_id, status, cwd, steps, started_at) VALUES (?, ?, 'running', ?, ?, ?)",
                (
                    run_id,
                    runbook_id,
                    cwd,
                    json.dumps(steps) if steps is not None else None,
                    time(),
                ),
            )
            self._db.commit()
        return self.get_runbook_run(run_id)  # type: ignore[return-value]

    def get_runbook_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runbook_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._runbook_run(row) if row else None

    def list_runbook_runs(
        self, runbook_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._lock:
            if runbook_id:
                rows = self._db.execute(
                    "SELECT * FROM runbook_runs WHERE runbook_id = ? ORDER BY started_at DESC LIMIT ?",
                    (runbook_id, max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM runbook_runs ORDER BY started_at DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
        return [self._runbook_run(row) for row in rows]

    def running_runbook_runs(self) -> list[dict[str, Any]]:
        """Every run still marked 'running' — uncapped; used by the runner's
        restart sweep so old interrupted runs are never left executor-less."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runbook_runs WHERE status = 'running' ORDER BY started_at"
            ).fetchall()
        return [self._runbook_run(row) for row in rows]

    def update_runbook_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"status", "current_step", "step_results", "error", "finished_at", "cwd"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_runbook_run(run_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [
            _json(value) if key == "step_results" else value
            for key, value in updates.items()
        ]
        with self._lock:
            self._db.execute(
                f"UPDATE runbook_runs SET {assignments} WHERE id = ?",
                (*values, run_id),
            )
            self._db.commit()
        return self.get_runbook_run(run_id)

    @staticmethod
    def _runbook(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "project_id": row["project_id"],
            "steps": json.loads(row["steps"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _runbook_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "runbook_id": row["runbook_id"],
            "status": row["status"],
            "cwd": row["cwd"],
            "current_step": row["current_step"],
            "step_results": json.loads(row["step_results"] or "[]"),
            "steps": json.loads(row["steps"]) if row["steps"] else None,
            "error": row["error"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    # Task worktrees -------------------------------------------------------

    def save_task_worktree(
        self,
        task_id: str,
        *,
        mode: str,
        base_repo: str,
        base_ref: str = "",
        base_branch: str | None = None,
        worktree_path: str | None = None,
        branch: str | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        now = time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO task_worktrees
                    (task_id, mode, base_repo, base_ref, base_branch,
                     worktree_path, branch, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, mode, base_repo, base_ref, base_branch, worktree_path,
                 branch, status, now, now),
            )
            self._db.commit()
        return self.task_worktree(task_id)  # type: ignore[return-value]

    def task_worktree(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM task_worktrees WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._worktree(row) if row else None

    def update_task_worktree(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"worktree_path", "branch", "head_sha", "status", "base_branch"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.task_worktree(task_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = list(updates.values()) + [time(), task_id]
        with self._lock:
            self._db.execute(
                f"UPDATE task_worktrees SET {assignments}, updated_at = ? WHERE task_id = ?",
                params,
            )
            self._db.commit()
        return self.task_worktree(task_id)

    @staticmethod
    def _worktree(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "mode": row["mode"],
            "base_repo": row["base_repo"],
            "base_ref": row["base_ref"],
            "base_branch": row["base_branch"],
            "worktree_path": row["worktree_path"],
            "branch": row["branch"],
            "head_sha": row["head_sha"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
