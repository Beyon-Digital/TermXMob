"""Task-bound workflow commands shared by Internal and native coding engines.

Identity, project, conversation and cancellation come exclusively from host
records. Model arguments cannot widen this binding. Mutable native callbacks
enter the same exact-action review broker used by native coding tools.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException

from termx.agent.policy import redact
from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import expand_tools
from termx.agent.tools.workflows import DESCRIPTIONS, READ_TOOLS, SCHEMAS, WORKFLOW_TOOLS


@dataclass(frozen=True)
class WorkflowToolBinding:
    actor: object
    task_id: str
    session_id: str
    managed_session_id: str
    project_id: str | None
    read_only: bool


def _public(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _public(part) for key, part in value.items()}
    if isinstance(value, list):
        return [_public(part) for part in value]
    return value


def _bounded_result(result):
    result = _public(result)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) <= 256 * 1024:
        return result
    # Keep the durable identity and actual state if a run contains many child
    # outputs. A transcript-sized tool result must never wedge native JSONL.
    row = result.get("run") or result.get("workflow") or {}
    summary = {key: row[key] for key in ("id", "workflow_id", "workflow_revision", "revision", "name", "status", "project_id", "created_at", "updated_at", "finished_at") if key in row}
    if "calls" in row:
        summary["calls"] = [{key: call[key] for key in ("id", "sequence", "operation", "name", "status", "session_id", "task_id") if key in call}
                            for call in row["calls"][:256]]
    return {"ok": result.get("ok", True), "truncated": True,
            "run" if "run" in result else "workflow": summary,
            "message": "The full result exceeds the tool output limit. Inspect this durable run in the Workflows workspace."}


def _arguments(name, arguments):
    if name not in SCHEMAS or not isinstance(arguments, dict):
        raise ValueError("Unknown workflow tool or invalid arguments")
    schema = SCHEMAS[name]
    if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
        raise ValueError("Unexpected or missing workflow arguments")
    # A provider's JSON schema is guidance; enforce it again at the host edge.
    encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 128 * 1024:
        raise ValueError("Workflow tool arguments exceed the size limit")
    for key, value in arguments.items():
        kind = schema["properties"][key]["type"]
        if ((kind == "string" and not isinstance(value, str))
                or (kind == "integer" and type(value) is not int)
                or (kind == "boolean" and type(value) is not bool)
                or (kind == "object" and not isinstance(value, dict))):
            raise ValueError("Invalid workflow argument type")
        rule = schema["properties"][key]
        if kind == "string" and not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 128 * 1024):
            raise ValueError("Workflow argument length is invalid")
        if kind == "integer" and not rule.get("minimum", 0) <= value <= rule.get("maximum", 2 ** 31 - 1):
            raise ValueError("Workflow argument bound is invalid")
    return dict(arguments)


class WorkflowToolService:
    def __init__(self, workspace):
        self.workspace = workspace

    def is_workflow_child(self, task_id):
        """Resolve enrollment before the first child turn can execute."""
        ancestor, seen = task_id, set()
        while ancestor:
            if ancestor in seen or len(seen) >= 64:
                raise PermissionError("Task ancestry is invalid")
            seen.add(ancestor)
            metadata = self.workspace.store.get("task", ancestor) or {}
            if metadata.get("workflow_run_id") or (metadata.get("conversation_id")
                    and self.workspace.store.get("workflow_child_session", metadata["conversation_id"])):
                return True
            ancestor = (self.workspace.agents.get_task(ancestor) or {}).get("parent_id")
        return False

    def validate_delegation(self, task_id):
        if self.is_workflow_child(task_id):
            raise PermissionError("The workflow script coordinates its agents; workflow children cannot delegate again")

    def binding(self, task_id, name, *, cancel=None):
        ws = self.workspace
        meta = ws.store.get("task", task_id)
        task = ws.agents.get_task(task_id)
        browser = getattr(ws.state, "browser", None)
        authority = browser.records.get("agent-task-authority", task_id) if browser else None
        if not meta or not task or not authority or meta["owner"] != authority.get("principal_id"):
            raise PermissionError("A live managed coding task is required")
        enrolled = ws.state.identity.session_by_id(authority["session_id"])
        actor = enrolled.principal if enrolled else None
        if (not actor or actor.id != meta["owner"] or actor.policy_version != authority["policy_version"]
                or not browser.session_valid(actor.id, authority["session_id"], authority["policy_version"])):
            raise PermissionError("The managed task authority expired")
        if authority.get("conversation_id") != meta.get("conversation_id"):
            raise PermissionError("Task conversation authority changed")
        if task.get("status") not in {"queued", "running", "awaiting_approval"}:
            raise PermissionError("The originating task is no longer active")
        pending = getattr(ws.state.agent, "_cancel", {}).get(task_id)
        if (cancel and cancel.is_set()) or (pending and pending.is_set()):
            raise PermissionError("The originating task was cancelled")
        if self.is_workflow_child(task_id):
            raise PermissionError("Workflow children cannot recursively invoke workflow tools")
        scope = "agent-view" if name in READ_TOOLS else "agent-run"
        ws.record(actor, "task", task_id, scope=scope)
        session = ws.record(actor, "conversation", meta["conversation_id"], scope=scope)
        if session.get("runner_id") or session.get("workflow") == "browser" or task.get("workflow") == "browser":
            raise PermissionError("Workflow tools require a local coding conversation")
        if (meta.get("project_id") or None) != (session.get("project_id") or None):
            raise PermissionError("Task project authority changed")
        read_only = task.get("mode") in {"ask", "plan"} or session.get("mode") in {"ask", "plan"}
        if read_only and name not in READ_TOOLS:
            raise PermissionError("This conversation is read-only")
        preset = ws.agents.task_agent(task)
        if preset:
            config = preset.get("file") or {}
            allowed = preset.get("tools") if isinstance(preset.get("tools"), list) else config.get("tools") or []
            denied = config.get("deny_tools") or []
            mode = config.get("tools_mode", preset.get("tools_mode", "explicit"))
            if name in denied or (mode == "explicit" and name not in expand_tools(allowed)):
                raise PermissionError("Workflow tool is outside the task preset")
        ws.validate_task_execution(task_id)
        return WorkflowToolBinding(actor, task_id, session["id"], authority["session_id"], session.get("project_id"), read_only)

    def schemas(self, task_id, *, read_only=False, workflow=None, allowed=None, denied=None, tools_mode=None):
        if not task_id or workflow == "browser":
            return []
        result = []
        for name in WORKFLOW_TOOLS:
            if read_only and name not in READ_TOOLS:
                continue
            if name in (denied or ()) or (allowed and tools_mode == "explicit" and name not in expand_tools(allowed)):
                continue
            try:
                self.binding(task_id, name)
            except (KeyError, ValueError, PermissionError, HTTPException):
                continue
            result.append({"name": name, "description": DESCRIPTIONS[name], "parameters": SCHEMAS[name]})
        return result

    def _same_project(self, binding, row, *, allow_global=False):
        if not (allow_global and not row.get("project_id")) and (row.get("project_id") or None) != (binding.project_id or None):
            raise PermissionError("Workflow is outside the task's project")
        return row

    async def command(self, task_id, call, *, cancel=None):
        try:
            values = _arguments(call.name, call.arguments)
            binding = self.binding(task_id, call.name, cancel=cancel)
            return _bounded_result(await self._command(binding, call, values, cancel=cancel))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Native/SDK transports otherwise stringify arbitrary exception
            # data, including credentials from remote hosts or database errors.
            from termx.workspace.workflows.service import WorkflowError
            error = {"code": getattr(exc, "code", "workflow_refused"),
                     "message": redact(str(exc))[:1000] if isinstance(exc, WorkflowError)
                     else "Workflow request was refused or is stale. Check the task, project, revision and arguments."}
            return {"ok": False, "refused": True, "error": error}

    async def _command(self, binding, call, values, *, cancel):
        service = self.workspace.workflows
        actor = binding.actor
        name = call.name
        request_id = values.pop("request_id", None) or "tool_" + hashlib.sha256((binding.task_id + ":" + call.call_id).encode()).hexdigest()
        if name == "list_workflows":
            rows = service.list(actor, project_id=binding.project_id, archived=values.get("archived", False))
            rows = [row for row in rows if not row.get("project_id") or row.get("project_id") == binding.project_id]
            offset, limit = values.get("offset", 0), values.get("limit", 100)
            keys = ("id", "revision", "name", "description", "project_id", "language", "limits", "archived", "created_at", "updated_at")
            return {"ok": True, "workflows": [{key: row[key] for key in keys if key in row} for row in rows[offset:offset + limit]],
                    "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None}
        if name == "get_workflow":
            return {"ok": True, "workflow": self._same_project(binding, service.get(actor, values["workflow_id"]), allow_global=True)}
        if name == "save_workflow":
            identifier = values.pop("workflow_id", None)
            revision = values.pop("revision", None)
            if identifier:
                self._same_project(binding, service.get(actor, identifier))
                row = service.update(actor, identifier, revision=revision, changes=values)
            else:
                if revision is not None:
                    raise ValueError("A new workflow has no saved revision")
                row = service.create(actor, request_id=request_id, project_id=binding.project_id, **values)
            return {"ok": True, "workflow": row, "workflow_id": row["id"], "revision": row["revision"]}
        if name == "validate_workflow":
            return {"ok": True, "validation": await service.validate(actor, **values)}
        if name == "run_workflow":
            project_id = binding.project_id
            if values.get("workflow_id"):
                definition = self._same_project(binding, service.get(actor, values["workflow_id"]), allow_global=True)
                # Global definitions inherit this exact parent context. The
                # service derives its effective project from the bound session.
                if not definition.get("project_id"):
                    project_id = None
            # Revalidate immediately at dispatch; a cancelled task or revoked
            # managed session cannot launch work after an approval wait.
            self.binding(binding.task_id, name, cancel=cancel)
            row = await service.start(actor, request_id=request_id, project_id=project_id,
                parent_session_id=binding.session_id, actor_task_id=binding.task_id,
                managed_session_id=binding.managed_session_id, **values)
            return {"ok": True, "run": row, "run_id": row["id"], "status": row["status"],
                    "message": "Run receipt created. Inspect get_workflow_run for completion and child approvals."}
        row = self._same_project(binding, service.get_run(actor, values["run_id"]))
        if name == "get_workflow_run":
            return {"ok": True, "run": row}
        if name == "cancel_workflow_run":
            return {"ok": True, "run": await service.cancel(actor, values["run_id"])}
        raise ValueError("Unknown workflow tool")

    async def native_execute(self, engine, binding, name, arguments, identifier):
        """Native callback: policy denial and exact review precede mutations."""
        try:
            task_id = binding.extensions_snapshot.get("managed_task_id")
            if name not in binding.extensions_snapshot.get("workflow_tool_names", ()):
                raise PermissionError("Workflow tool was not provisioned for this session")
            if binding.extensions_snapshot.get("review_read_only") and name not in READ_TOOLS:
                raise PermissionError("Native execution profile is read-only")
            values = _arguments(name, arguments)
            scope = self.binding(task_id, name)
            if binding.conversation_id and binding.conversation_id != scope.session_id:
                raise PermissionError("Native session belongs to another conversation")
            if engine.browser_service is not self.workspace.state.browser:
                raise PermissionError("Native review broker is unavailable")
            from termx.agent.tools import default_registry
            manager = self.workspace.state.agent
            context = manager._tool_context(task_id, self.workspace.agents.get_task(task_id))
            call = ProviderCall("function", str(identifier), name, values)
            decision = default_registry().get(name).decide(call, context)
            if decision.auto_resolved == "deny" or (not decision.allowed and not decision.approval_required):
                raise PermissionError("Workflow tool was denied by policy")
            async def operation():
                # Human review may wait while a remembered deny, preset or
                # read-only mode changes. Re-decide at the execution edge.
                self.binding(task_id, name, cancel=context.cancel)
                current_context = manager._tool_context(task_id, self.workspace.agents.get_task(task_id))
                current = default_registry().get(name).decide(call, current_context)
                if current.auto_resolved == "deny" or (not current.allowed and not current.approval_required):
                    raise PermissionError("Workflow policy changed during approval")
                return await self.command(task_id, call, cancel=current_context.cancel)
            if name in {"save_workflow", "run_workflow"}:
                from termx.engines.action_review import execute
                return await execute(engine, binding, name, values, str(identifier), operation)
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"ok": False, "refused": True, "error": {"code": "workflow_refused",
                "message": "Workflow action was refused or its native task authority changed."}}
