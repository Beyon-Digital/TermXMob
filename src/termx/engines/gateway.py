"""EngineGateway — binds native engine sessions to TermX tasks/events/approvals.

The gateway never re-implements an agent loop. A task row is the persistence
and streaming envelope for a native engine session: the adapter owns the
engine process, the gateway owns correlation (binding ↔ task ↔ conversation),
event persistence, and approval routing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import EngineAdapter
from .types import (
    EffectiveRunConfiguration,
    EngineDescriptor,
    EngineEvent,
    EngineSessionBinding,
)

EmitFn = Callable[[str, str, dict[str, Any]], dict[str, Any]]

_ENGINE_ACTIVE = {"active", "idle"}


class EngineGateway:
    def __init__(self, store: Any, emit: EmitFn) -> None:
        self._store = store
        self._emit = emit
        self._adapters: dict[str, EngineAdapter] = {}
        # binding_id -> live binding (in-memory; engine_sessions table persists)
        self._bindings: dict[str, EngineSessionBinding] = {}
        # binding_id -> task_id (the task currently owning the native session)
        self._binding_task: dict[str, str] = {}
        # binding_id -> accumulated assistant text for result recording
        self._result_buffers: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ registry

    def register(self, adapter: EngineAdapter) -> None:
        self._adapters[adapter.descriptor().id] = adapter

    def adapter(self, engine: str) -> EngineAdapter:
        try:
            return self._adapters[engine]
        except KeyError:
            raise KeyError(f"engine '{engine}' is not available") from None

    def engines(self) -> list[str]:
        return sorted(self._adapters)

    async def describe_all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self.engines():
            adapter = self._adapters[name]
            desc = adapter.descriptor().as_dict()
            desc["capabilities"] = adapter.capabilities().as_dict()
            out.append(desc)
        return out

    async def probe(self, engine: str) -> dict[str, Any]:
        adapter = self.adapter(engine)
        desc = await adapter.probe()
        payload = desc.as_dict()
        payload["capabilities"] = adapter.capabilities().as_dict()
        return payload

    async def models(self, engine: str) -> list[str]:
        adapter = self.adapter(engine)
        if not adapter.capabilities().models:
            desc = await adapter.probe()  # populates models lazily
            if desc.error:
                return []
        return adapter.capabilities().models

    # ------------------------------------------------------------ lifecycle

    async def create_task(
        self,
        *,
        prompt: str,
        cwd: str,
        engine: str,
        model: str | None = None,
        custom_agent: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        limits: dict[str, Any] | None = None,
        sandbox_profile: str = "agent",
        approval_mode: str = "standard",
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("task prompt is required")
        root = Path(cwd).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("project folder is not a directory")
        adapter = self.adapter(engine)

        instructions = ""
        if custom_agent:
            instructions = str(custom_agent.get("instructions") or "").strip()

        cfg = EffectiveRunConfiguration(
            engine=engine,
            model=model or (str(custom_agent["model"]) if custom_agent and custom_agent.get("model") else None),
            agent_id=custom_agent["id"] if custom_agent else None,
            instructions=instructions,
            cwd=str(root),
            sandbox_profile=sandbox_profile,
            approval_mode=approval_mode,
            agent_profile_revision=str((custom_agent or {}).get("file_revision") or ""),
        )

        # Reuse a live native session for this conversation when one exists —
        # the engine owns conversation continuity natively.
        binding: EngineSessionBinding | None = None
        existing = (
            self._store.engine_session_for_conversation(conversation_id)
            if conversation_id
            else None
        )
        if existing and existing["engine"] == engine:
            binding = self._bindings.get(existing["binding_id"])
            if binding is None:
                binding = EngineSessionBinding.new(
                    engine, existing["native_session_id"],
                    cwd=existing["cwd"], conversation_id=conversation_id,
                    status="idle",
                )
                try:
                    binding = await adapter.attach(binding)
                except Exception:
                    self._store.update_engine_session(
                        existing["binding_id"], status="lost")
                    binding = None
            if binding is not None:
                binding.conversation_id = conversation_id
                self._bindings[binding.binding_id] = binding

        task = self._store.create_task(
            prompt=prompt,
            cwd=str(root),
            provider_id=f"engine:{engine}",
            model=cfg.model or "",
            limits=limits or {},
            custom_agent_id=custom_agent["id"] if custom_agent else None,
            engine=engine,
            status="running",
        )
        task_id = task["id"]

        if binding is None:
            try:
                binding = await adapter.create_session(cfg)
            except Exception as exc:
                self._store.update_task(task_id, status="failed", error=str(exc))
                self._emit(task_id, "task.failed", {"message": str(exc)})
                raise ValueError(f"{engine} session failed to start: {exc}") from exc
            binding.conversation_id = conversation_id
            self._bindings[binding.binding_id] = binding
            self._store.save_engine_session(
                binding.binding_id,
                task_id=task_id,
                engine=engine,
                native_session_id=binding.native_session_id,
                conversation_id=conversation_id,
                cwd=binding.cwd,
                status=binding.status,
                payload=binding.as_dict(),
            )
        else:
            self._store.update_engine_session(
                binding.binding_id, task_id=task_id, status="active",
                payload=binding.as_dict())

        self._binding_task[binding.binding_id] = task_id
        self._store.update_task(
            task_id,
            engine_session_id=binding.binding_id,
            engine_native_id=binding.native_session_id,
        )
        self._emit(task_id, "task.created", {"task": task})
        self._emit(task_id, "engine.session.bound", {
            "engine": engine,
            "binding_id": binding.binding_id,
            "native_session_id": binding.native_session_id,
            "resumed": existing is not None,
        })
        try:
            text = prompt if not instructions else (
                f'You are the "{custom_agent["name"]}" agent. Follow these '
                f"instructions:\n{instructions}\n\nTask: {prompt}"
            )
            await adapter.send(binding, text)
        except Exception as exc:
            self._store.update_task(task_id, status="failed", error=str(exc))
            self._emit(task_id, "task.failed", {"message": str(exc)})
            raise ValueError(f"{engine} turn failed to start: {exc}") from exc
        return self._store.get_task(task_id, include_events=True) or task

    async def cancel(self, task_id: str) -> dict[str, Any] | None:
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        binding = self._binding_for_task(task_id)
        if binding is not None:
            self._store.update_task(task_id, status="cancelling")
            self._emit(task_id, "task.status", {"status": "cancelling"})
            await self.adapter(binding.engine).cancel(binding)
        else:
            self._store.update_task(
                task_id, status="cancelled", error="Cancelled by user")
            self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})
        return self._store.get_task(task_id)

    async def steer(self, task_id: str, message: str) -> dict[str, Any] | None:
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        binding = self._binding_for_task(task_id)
        if binding is None:
            raise ValueError("no live engine session for this task")
        ok = await self.adapter(binding.engine).steer(binding, message)
        if not ok:
            raise ValueError(f"{binding.engine} does not support steering right now")
        self._emit(task_id, "task.steered", {"message": message})
        return self._store.get_task(task_id)

    async def resolve_approval(
        self,
        task_id: str,
        approval_id: str,
        decision: str,
        *,
        remember: str | None = None,
        content: Any = None,
    ) -> dict[str, Any] | None:
        """Route a resolved approval to the engine's pending server request.

        Returns None when the task is not engine-backed (caller falls back to
        the internal manager path).
        """
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        approval = self._store.get_approval(approval_id)
        if approval is None or approval["task_id"] != task_id:
            raise KeyError(approval_id)
        payload = approval.get("payload") or {}
        token = payload.get("engine_request_id")
        binding = self._binding_for_task(task_id)
        resolved = self._store.resolve_approval(
            approval_id, "approved" if decision in {"approved", "approve"} else decision)
        self._emit(task_id, "approval.resolved", {"approval": resolved})
        if binding is not None and token:
            wire_decision = {
                "approved": "approve",
                "approve": "approve",
                "denied": "deny",
                "deny": "deny",
                "cancel": "cancel",
            }.get(decision, "deny")
            try:
                await self.adapter(binding.engine).respond_approval(
                    binding, token, wire_decision,
                    remember=remember in {"session", "always", "project"},
                    content=content,
                )
            except KeyError:
                pass  # request already expired engine-side; row stays resolved
        return resolved

    # ----------------------------------------------------------- event flow

    def _binding_for_task(self, task_id: str) -> EngineSessionBinding | None:
        record = self._store.engine_session_for_task(task_id)
        if record is None:
            return None
        return self._bindings.get(record["binding_id"])

    def on_engine_event(self, binding_id: str, event: EngineEvent) -> None:
        """Adapter event sink — persist, update state, fan out to subscribers."""
        task_id = self._binding_task.get(binding_id)
        if task_id is None:
            # session created before task row link or orphaned — still record
            record = self._store.get_engine_session(binding_id)
            task_id = record["task_id"] if record else None
        if task_id is None:
            return
        payload = dict(event.payload)
        if event.native:
            payload["native"] = event.native
        self._emit(task_id, event.type, payload)

        if event.type == "engine.message.delta":
            delta = event.payload.get("delta") or event.payload.get("text") or ""
            self._result_buffers.setdefault(binding_id, []).append(str(delta))
        elif event.type == "engine.item.completed":
            item = event.payload.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text"):
                buf = self._result_buffers.setdefault(binding_id, [])
                if not buf or not "".join(buf).strip():
                    buf.append(str(item["text"]))
        elif event.type == "engine.turn.completed":
            self._finish_turn(binding_id, task_id, event.payload)
        elif event.type == "engine.session.lost":
            self._store.update_engine_session(binding_id, status="lost")
            self._store.update_task(
                task_id, status="failed",
                error="Engine process exited; session may be resumable",
            )

    def _finish_turn(self, binding_id: str, task_id: str,
                     payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "completed")
        result = "".join(self._result_buffers.pop(binding_id, [])) or None
        if status == "completed":
            self._store.update_task(task_id, status="completed", result=result)
            self._emit(task_id, "task.completed", {"result": result})
        elif status == "interrupted":
            self._store.update_task(
                task_id, status="cancelled", error="Interrupted", result=result)
            self._emit(task_id, "task.cancelled", {"message": "Interrupted"})
        else:
            err = payload.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            self._store.update_task(
                task_id, status="failed", error=message or "engine turn failed",
                result=result)
            self._emit(task_id, "task.failed",
                       {"message": message or "engine turn failed"})
        record = self._store.get_engine_session(binding_id)
        if record:
            self._store.update_engine_session(binding_id, status="idle")

    async def approval_sink(self, binding_id: str, token: str, method: str,
                            params: dict[str, Any], kind: str) -> str:
        """Create the TermX approval row backing a native server→client request."""
        task_id = self._binding_task.get(binding_id)
        if task_id is None:
            raise ValueError("no task bound to engine session")
        approval = self._store.create_approval(
            task_id,
            kind,
            {
                "engine_request_id": token,
                "engine_method": method,
                "title": _approval_title(method, params),
                "request": params,
                "consequence": "Sent to the engine process when resolved",
            },
        )
        self._emit(task_id, "approval.requested", {"approval": approval})
        return str(approval["id"])

    # ------------------------------------------------------------ lifecycle

    def recover(self) -> int:
        """On host start: live bindings are gone — mark them resumable-lost."""
        marked = 0
        for record in self._store.list_engine_sessions():
            if record["status"] in _ENGINE_ACTIVE:
                self._store.update_engine_session(
                    record["binding_id"], status="lost")
                marked += 1
        return marked

    async def shutdown(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.shutdown()
            except Exception:
                pass


def _approval_title(method: str, params: dict[str, Any]) -> str:
    if method == "item/commandExecution/requestApproval":
        cmd = params.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return f"Approve command: {str(cmd or '(command)')[:120]}"
    if method == "item/fileChange/requestApproval":
        return "Approve file changes"
    if method == "item/permissions/requestApproval":
        return "Approve requested permissions"
    if method == "mcpServer/elicitation/request":
        return f"MCP server request: {params.get('serverName', '')}"
    return "Engine approval requested"
