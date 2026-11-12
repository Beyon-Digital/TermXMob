"""Durable JavaScript orchestration over ordinary workspace agent tasks."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
from time import monotonic, time

from fastapi import HTTPException

from termx.agent.limits import resolve_limits as agent_limits
from termx.agent.policy import redact
from termx.workspace.store import Conflict
from .schema import DEFAULT_LIMITS, resolve_limits, validate_args, validate_definition, validate_input_schema, validate_source
from .store import CALL, DEFINITION, RUN, TERMINAL, WorkflowStore, digest

_LIVE = frozenset({"queued", "running", "awaiting_approval", "cancelling"})
_TASK_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_PUBLIC_CALL = {"id", "sequence", "operation", "name", "status", "session_id", "task_id", "output", "error", "started_at", "finished_at"}
_PUBLIC_RUN = {"id", "workflow_id", "workflow_revision", "name", "status", "args", "project_id", "parent_session_id", "result", "error", "limits", "created_at", "updated_at", "finished_at", "events", "events_truncated"}

_BUDGET_CAPABILITIES = {
    "budget_scope": "manual_run_opt_in",
    "source_task_aggregate_budget": "not_shared",
    "concurrent_workflows_per_source_task": None,
    "native_child_tool_steps": "unmeasured",
}


def _denied(exc):
    return not isinstance(exc, HTTPException) or exc.status_code in {403, 404}


class WorkflowError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _error(exc, code="workflow_failed"):
    return {"code": getattr(exc, "code", code), "message": redact(str(exc) or type(exc).__name__)[:2000]}


def _public_value(value, limit=64000):
    """Redact JSON strings without turning non-text JSON into source code."""
    def clean(item, depth=0):
        if depth > 32:
            return "[depth limit]"
        if isinstance(item, str):
            return redact(item)
        if isinstance(item, dict):
            return {str(key): clean(part, depth + 1) for key, part in item.items()}
        if isinstance(item, list):
            return [clean(part, depth + 1) for part in item]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return redact(str(item))
    result = clean(value)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > limit:
        return {"truncated": True, "text": encoded[:limit // 2]}
    return result


class WorkflowService:
    def __init__(self, workspace, runtime=None):
        self.workspace = workspace
        self.store = workspace.store
        self.ledger = WorkflowStore(self.store)
        if runtime is None:
            from .runtime import WorkflowRuntime
            runtime = WorkflowRuntime()
        self.runtime = runtime
        self._workers = {}
        self._cancels = {}
        self._principals = {}
        self._semaphores = {}
        self._closed = False
        self.ledger.interrupt()

    def _require(self, principal, scope, project_id=None):
        self.workspace.require(principal, scope, project_id)

    def _owned(self, principal, kind, identifier, scope="agent-view"):
        row = self.store.get(kind, identifier)
        if not row or row["owner"] != principal.id:
            raise KeyError(identifier)
        self._require(principal, scope, row.get("project_id"))
        return row

    def capabilities(self, principal=None):
        if principal is not None:
            self._require(principal, "agent-view")
        return {"runtime": self.runtime.capabilities(), "limits": dict(DEFAULT_LIMITS),
                "helpers": ["agent", "parallel", "pipeline", "phase", "log"],
                "agent_only": True, "automatic_replay": False,
                "nested_agent_delegation": "disabled",
                "agent_context": "authorized_project_or_owned_parent",
                **_BUDGET_CAPABILITIES}

    def list(self, principal, *, project_id=None, archived=False):
        self._require(principal, "agent-view", project_id)
        result = []
        for row in self.store.list(DEFINITION, principal.id):
            if project_id is not None and row.get("project_id") not in {None, project_id}:
                continue
            if bool(row.get("archived")) != bool(archived):
                continue
            try:
                self._owned(principal, DEFINITION, row["id"])
            except (KeyError, PermissionError, HTTPException) as exc:
                if not _denied(exc):
                    raise
                continue
            result.append({**row, "limits": resolve_limits()})
        return result

    def get(self, principal, identifier):
        row = self._owned(principal, DEFINITION, identifier)
        # Preserve saved legacy data while requiring explicit manual launch
        # input to opt into execution quotas.
        return {**row, "limits": resolve_limits()}

    @staticmethod
    def _request_id(value):
        if not isinstance(value, str) or not 8 <= len(value) <= 128:
            raise ValueError("request_id must contain 8..128 characters")
        return value

    def create(self, principal, *, request_id, **data):
        self._request_id(request_id)
        definition = validate_definition(data)
        self._require(principal, "agent-control", definition.get("project_id"))
        row, _created = self.ledger.reserve(DEFINITION, principal.id, request_id,
            digest(definition), definition, definition.get("project_id"))
        return {**row, "limits": resolve_limits()}

    def update(self, principal, identifier, *, revision, changes):
        row = self._owned(principal, DEFINITION, identifier, "agent-control")
        if type(revision) is not int or revision < 1:
            raise ValueError("revision must be a positive integer")
        patch = validate_definition(changes, partial=True)
        if "project_id" in patch and patch["project_id"] != row.get("project_id"):
            raise ValueError("Create another workflow to change its project boundary")
        updated = self.store.update(DEFINITION, identifier, {**row, **patch}, revision)
        return {**updated, "limits": resolve_limits()}

    async def validate(self, principal=None, *, source, input_schema=None, limits=None):
        if principal is not None:
            self._require(principal, "agent-view")
        source = validate_source(source)
        schema, bounded = validate_input_schema(input_schema), resolve_limits(limits)
        result = await self.runtime.validate(source, bounded)
        return {**result, "input_schema": schema, "limits": bounded}

    def _boundary(self, principal, project_id, parent_session_id, actor_task_id, managed_session_id):
        row = None
        if parent_session_id:
            row = self.workspace.session(principal, parent_session_id, turns=False)
            self.workspace.record(principal, "conversation", parent_session_id, scope="agent-run")
            if row.get("runner_id") or row.get("workflow"):
                raise PermissionError("Workflows require a local coding conversation")
            if row.get("mode") != "agent":
                raise PermissionError("Switch to Agent mode before starting an agent workflow")
            if project_id is not None and project_id != row.get("project_id"):
                raise PermissionError("Workflow and parent conversation must use the same project")
            project_id = row.get("project_id")
        if actor_task_id:
            meta = self.workspace.record(principal, "task", actor_task_id, scope="agent-run")
            task = self.workspace.agents.get_task(actor_task_id)
            if not task or task.get("mode") != "agent" or task["status"] not in {"running", "awaiting_approval"}:
                raise PermissionError("The originating agent turn cannot start workflows")
            if not row or meta.get("conversation_id") != parent_session_id:
                raise PermissionError("Workflow parent does not match its originating task")
            ancestor, seen = task, set()
            while ancestor:
                if ancestor["id"] in seen or len(seen) >= 64:
                    raise PermissionError("Agent ancestry is invalid")
                seen.add(ancestor["id"])
                enrolled = self.store.get("task", ancestor["id"]) or {}
                if enrolled.get("workflow_run_id") or self.store.get("workflow_child_session", enrolled.get("conversation_id") or ""):
                    raise PermissionError("Recursive workflow execution is unavailable")
                ancestor = self.workspace.agents.get_task(ancestor.get("parent_id")) if ancestor.get("parent_id") else None
            self.workspace.validate_task_execution(actor_task_id)
        if row is None:
            self._require(principal, "agent-run", project_id)
        if row:
            target = dict(self.workspace.execution_target(row))
            target["custom_agent_revision"] = row.get("custom_agent_revision")
        else:
            cwd = None
            if project_id:
                cwd = str(self.workspace.state.projects.project(project_id)["path"])
                self.workspace.require(principal, "agent-run", project_id, cwd)
            target = {"project_id": project_id, "cwd": cwd, "engine": "internal", "mode": "agent", "run_limits": agent_limits()}
        return {"target": target, "actor_task_id": actor_task_id,
                "managed_session_id": managed_session_id or getattr(principal, "authority_session_id", None),
                "policy_version": getattr(principal, "policy_version", None)}

    async def start(self, principal, *, request_id, workflow_id=None, revision=None, source=None,
                    name=None, args=None, project_id=None, parent_session_id=None, limits=None,
                    actor_task_id=None, managed_session_id=None):
        if self._closed:
            raise ValueError("Workflow service is closing")
        self._request_id(request_id)
        request_content = {"workflow_id": workflow_id, "revision": revision, "source": source,
            "name": name, "args": {} if args is None else args, "project_id": project_id,
            "parent_session_id": parent_session_id, "limits": limits,
            "actor_task_id": actor_task_id, "managed_session_id": managed_session_id}
        request_digest = digest(request_content)
        previous = self.ledger.existing(RUN, principal.id, request_id, request_digest)
        if previous:
            return self.get_run(principal, previous["id"])
        if bool(workflow_id) == (source is not None):
            raise ValueError("Choose exactly one saved workflow or inline source")
        if workflow_id:
            definition = self.get(principal, workflow_id)
            if definition.get("archived"):
                raise ValueError("Archived workflows cannot start new runs")
            if type(revision) is not int or definition["revision"] != revision:
                raise Conflict("Workflow changed; review its current revision before running")
            if project_id is not None and project_id != definition.get("project_id"):
                raise PermissionError("Workflow project boundary cannot be changed at launch")
            project_id = definition.get("project_id")
            if name is not None:
                raise ValueError("A saved workflow supplies its run name")
        else:
            if revision is not None:
                raise ValueError("Inline workflows have no saved revision")
            definition = validate_definition({"name": name or "Inline workflow", "source": source, "project_id": project_id})
        values = validate_args({} if args is None else args, definition["input_schema"])
        bounded = resolve_limits() if actor_task_id else resolve_limits(limits)
        binding = self._boundary(principal, project_id, parent_session_id, actor_task_id, managed_session_id)
        project_id = binding["target"].get("project_id")
        if not self.runtime.capabilities().get("available"):
            raise WorkflowError("runtime_unavailable", self.runtime.capabilities().get("reason") or "JavaScript workflow runtime is unavailable")
        snapshot = {key: definition[key] for key in ("name", "description", "language", "source", "input_schema", "limits")}
        intent = {"workflow_id": workflow_id, "workflow_revision": revision, "snapshot": snapshot,
                  "args": values, "project_id": project_id, "parent_session_id": parent_session_id,
                  "limits": bounded, "trigger": "agent" if actor_task_id else "manual", "binding": binding}
        row, created = self.ledger.reserve(RUN, principal.id, request_id, request_digest, {
            **intent, "name": definition["name"], "status": "queued", "call_ids": [], "events": [],
            "event_sequence": 0, "result": None, "error": None, "finished_at": None,
        }, project_id)
        if created:
            self._principals[row["id"]] = principal
            self._cancels[row["id"]] = asyncio.Event()
            self._semaphores[row["id"]] = asyncio.Semaphore(bounded["concurrency"]) if bounded["concurrency"] is not None else None
            task = asyncio.create_task(self._execute(row["id"]))
            self._workers[row["id"]] = task
            task.add_done_callback(lambda done, rid=row["id"]: self._done(rid, done))
        return self.get_run(principal, row["id"])

    def _done(self, run_id, worker):
        self._workers.pop(run_id, None)
        self._cancels.pop(run_id, None)
        self._semaphores.pop(run_id, None)
        self._principals.pop(run_id, None)
        if not worker.cancelled():
            worker.exception()

    def _actor(self, run):
        identity = getattr(self.workspace.state, "identity", None)
        principal = identity.principal_by_id(run["owner"]) if identity else self._principals.get(run["id"])
        if not principal:
            raise PermissionError("Workflow owner is no longer active")
        binding = run["binding"]
        if binding.get("policy_version") != getattr(principal, "policy_version", None):
            raise PermissionError("Workflow execution authority changed")
        if binding.get("managed_session_id"):
            session = identity.session_by_id(binding["managed_session_id"]) if identity else None
            if not session or session.principal.id != run["owner"] or getattr(session, "locked", False):
                raise PermissionError("Originating managed session was revoked or locked")
            principal = session.principal
        if binding.get("actor_task_id"):
            actor = self.workspace.agents.get_task(binding["actor_task_id"])
            if not actor or actor["status"] in {"cancelled", "cancelling", "failed"}:
                raise PermissionError("The originating agent turn was stopped")
        if run["status"] not in {"running", "awaiting_approval"}:
            raise PermissionError("Workflow no longer accepts agent effects")
        target = binding["target"]
        parent = run.get("parent_session_id")
        if parent:
            current = self.workspace.session(principal, parent, turns=False)
            self.workspace.record(principal, "conversation", parent, scope="agent-run")
            if self.workspace.execution_target(current) != target:
                raise PermissionError("Parent execution settings changed; start a new workflow")
        if target.get("custom_agent_id"):
            from termx.workspace.presets import resolve_preset
            preset = resolve_preset(self.workspace, principal, target["custom_agent_id"],
                engine=target.get("engine") or "internal", project_id=run.get("project_id"),
                runner_id=None, workflow=None)
            if not preset or preset["workspace_revision"] != target.get("custom_agent_revision"):
                raise PermissionError("Agent preset changed; review it before starting another workflow")
        self.workspace.require(principal, "agent-run", run.get("project_id"), target.get("cwd"),
                               resource_kind="conversation" if parent else None, resource_id=parent)
        return principal

    def validate_task_execution(self, task_id):
        seen, metadata = set(), None
        while task_id:
            if task_id in seen or len(seen) >= 64:
                raise PermissionError("Workflow child ancestry is invalid")
            seen.add(task_id)
            metadata = self.store.get("task", task_id)
            if metadata and metadata.get("workflow_run_id"):
                break
            child_binding = self.store.get("workflow_child_session", (metadata or {}).get("conversation_id") or "")
            if child_binding:
                metadata = {**metadata, "workflow_run_id": child_binding["run_id"]}
                break
            task_id = (self.workspace.agents.get_task(task_id) or {}).get("parent_id")
        if not metadata or not metadata.get("workflow_run_id"):
            return
        run = self.store.get(RUN, metadata["workflow_run_id"])
        if not run or run["owner"] != metadata["owner"]:
            raise PermissionError("Workflow child ownership is unavailable")
        self._actor(run)

    def validate_delegation(self, task_id):
        """Workflow JavaScript is its children's only managed orchestrator."""
        seen = set()
        while task_id:
            if task_id in seen or len(seen) >= 64:
                raise PermissionError("Workflow child ancestry is invalid")
            seen.add(task_id)
            metadata = self.store.get("task", task_id) or {}
            if metadata.get("workflow_run_id") or self.store.get("workflow_child_session", metadata.get("conversation_id") or ""):
                raise PermissionError("Workflow children cannot delegate more agents; use the workflow parallel or agent helpers")
            task_id = (self.workspace.agents.get_task(task_id) or {}).get("parent_id")

    def child_creation_context(self, principal, run_id, project_id, cwd):
        """Private creation context for reusing an owned parent scratch.

        The caller supplies a durable run ID, never a client-selected source
        resource. WorkspaceService still performs its ordinary create checks.
        """
        run = self._owned(principal, RUN, run_id, "agent-control")
        actor = self._actor(run)
        target = run["binding"]["target"]
        parent = run.get("parent_session_id")
        if project_id is not None or run.get("project_id") is not None or not parent or not cwd:
            raise PermissionError("Scratch delegation requires its owned parent conversation")
        actual = Path(cwd).resolve(strict=True)
        if (str(actual) != str(Path(target.get("cwd") or "").resolve(strict=True))
                or not actual.is_relative_to(self.workspace.scratch_root(principal.id))
                or not actual.is_dir()):
            raise PermissionError("Scratch delegation cannot change the parent execution folder")
        row = self.workspace.record(actor, "conversation", parent, scope="agent-control")
        self.workspace.record(actor, "conversation", parent, scope="agent-run")
        if row.get("project_id") is not None or Path(row["cwd"]).resolve(strict=True) != actual:
            raise PermissionError("Scratch parent execution boundary changed")
        return {"resource_kind": "conversation", "resource_id": parent}

    def delegated_boundary(self, principal, kind, identifier, cwd, scope):
        """Authorize a host-created scratch child through its owned ancestor.

        Historical reads use the caller's live authority; execution separately
        checks the active run and its original managed-session grant.
        """
        if kind == "task":
            metadata = self.store.get("task", identifier)
            if not metadata or metadata["owner"] != principal.id:
                return False
            identifier = metadata.get("conversation_id")
        elif kind != "conversation":
            return False
        if not identifier or not cwd:
            return False
        try:
            actual = Path(cwd).resolve(strict=True)
            if not actual.is_dir() or not actual.is_relative_to(self.workspace.scratch_root(principal.id)):
                return False
            seen, enrolled = set(), False
            while identifier:
                if identifier in seen or len(seen) >= 64:
                    return False
                seen.add(identifier)
                mapping = self.store.get("workflow_child_session", identifier)
                if not mapping:
                    if not enrolled:
                        return False
                    ancestor = self.workspace.record(principal, "conversation", identifier, scope=scope)
                    return ancestor.get("project_id") is None and Path(ancestor["cwd"]).resolve(strict=True) == actual
                if mapping["owner"] != principal.id:
                    return False
                run = self.store.get(RUN, mapping["run_id"])
                if not run or run["owner"] != principal.id or run.get("project_id") is not None:
                    return False
                target = run["binding"]["target"]
                if not target.get("cwd") or Path(target["cwd"]).resolve(strict=True) != actual:
                    return False
                child = self.store.get("conversation", identifier)
                if not child or child["owner"] != principal.id or child.get("project_id") is not None or Path(child["cwd"]).resolve(strict=True) != actual:
                    return False
                identifier, enrolled = run.get("parent_session_id"), True
            return False
        except (KeyError, PermissionError, OSError):
            return False
        except HTTPException as exc:
            if _denied(exc):
                return False
            raise

    def runs(self, principal, *, workflow_id=None, project_id=None):
        self._require(principal, "agent-view", project_id)
        if workflow_id:
            self.get(principal, workflow_id)
        result = []
        for row in self.store.list(RUN, principal.id, project_id):
            if workflow_id and row.get("workflow_id") != workflow_id:
                continue
            try:
                result.append(self.get_run(principal, row["id"]))
            except (PermissionError, KeyError, HTTPException) as exc:
                if not _denied(exc):
                    raise
                continue
            if len(result) >= 200:
                break
        return result

    def get_run(self, principal, identifier):
        row = self._owned(principal, RUN, identifier)
        result = {key: row.get(key) for key in _PUBLIC_RUN}
        calls = []
        for call in self.ledger.calls(row, visible=True):
            public = {key: call.get(key) for key in _PUBLIC_CALL}
            if call.get("session_id"):
                try:
                    self.workspace.record(principal, "conversation", call["session_id"])
                    if call.get("task_id"):
                        self.workspace.record(principal, "task", call["task_id"])
                except (PermissionError, KeyError, HTTPException) as exc:
                    if not _denied(exc):
                        raise
                    public.update(output=None, session_id=None, task_id=None, unavailable=True)
            calls.append(public)
        result["calls"] = calls
        result["call_count"] = row.get("call_count", len(calls))
        result["calls_truncated"] = result["call_count"] > len(calls)
        result["snapshot"] = row.get("snapshot")
        result["capability_limits"] = dict(_BUDGET_CAPABILITIES)
        try:
            action_scope = "agent-control" if row["status"] in _LIVE else "agent-run"
            if row.get("parent_session_id"):
                self.workspace.record(principal, "conversation", row["parent_session_id"], scope=action_scope)
            else:
                self._require(principal, action_scope, row.get("project_id"))
            definition = self.store.get(DEFINITION, row.get("workflow_id") or "")
            result["allowed_actions"] = ["cancel"] if row["status"] in _LIVE else ["retry"] if not definition or not definition.get("archived") else []
        except (PermissionError, HTTPException) as exc:
            if not _denied(exc):
                raise
            result["allowed_actions"] = []
        return result

    def _refresh_status(self, run_id):
        run = self.store.get(RUN, run_id)
        if not run or run["status"] not in {"running", "awaiting_approval"}:
            return
        waiting = any(call["status"] == "awaiting_approval" for call in self.ledger.calls(run))
        status = "awaiting_approval" if waiting else "running"
        if run["status"] != status:
            self.ledger.patch(RUN, run_id, status=status)
            self.ledger.event(run_id, "workflow.status", {"status": status})

    async def _execute(self, run_id):
        run = self.store.get(RUN, run_id)
        if run["status"] == "cancelling":
            self.ledger.patch(RUN, run_id, status="cancelled", finished_at=time())
            return
        self.ledger.patch(RUN, run_id, status="running", started_at=time())
        self.ledger.event(run_id, "workflow.started")
        cancel = self._cancels[run_id]
        try:
            self._actor(self.store.get(RUN, run_id))
            execution = self.runtime.run(run["snapshot"]["source"], run["args"],
                run["limits"], lambda cid, op, args: self._on_call(run_id, cid, op, args), cancel)
            outcome = await asyncio.wait_for(execution, timeout=run["limits"]["timeout_s"]) if run["limits"].get("timeout_s") is not None else await execution
            status = outcome.get("status")
            if status not in {"completed", "failed", "cancelled"}:
                raise WorkflowError("invalid_runtime_result", "Runtime ended without a terminal result")
            if cancel.is_set():
                status = "cancelled"
            elif status == "completed":
                self._actor(self.store.get(RUN, run_id))
            # A caught agent() error cannot leave orphaned effects running
            # after the JavaScript program returns a successful value.
            uncertain = await self._stop_children(run_id)
            if uncertain:
                status = "needs_attention"
            self.ledger.patch(RUN, run_id, status=status,
                result=_public_value(outcome.get("result")) if status == "completed" else None,
                error={"code": "unknown_child_outcome", "message": "A child did not acknowledge its terminal state; inspect it before starting new work."} if uncertain else _public_value(outcome.get("error")), finished_at=time())
        except BaseException as exc:
            cancel.set()
            uncertain = await self._stop_children(run_id)
            status = "needs_attention" if uncertain else "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            error = {"code": "timeout", "message": "The manually configured workflow timeout elapsed"} if isinstance(exc, TimeoutError) else _error(exc)
            self.ledger.patch(RUN, run_id, status=status, error=error, finished_at=time())
        finally:
            final = self.store.get(RUN, run_id)
            for call in self.ledger.calls(final):
                if call["status"] not in TERMINAL or (final["status"] == "needs_attention" and call.get("task_id")
                        and (self.workspace.agents.get_task(call["task_id"]) or {}).get("status") not in _TASK_TERMINAL):
                    task = self.workspace.agents.get_task(call.get("task_id")) if call.get("task_id") else None
                    status = (task or {}).get("status")
                    if status not in _TASK_TERMINAL:
                        status = "needs_attention" if call.get("task_id") else "cancelled" if final["status"] == "cancelled" else "failed"
                    self.ledger.patch(CALL, call["id"], status=status,
                        error=final.get("error") or {"code": "workflow_ended", "message": "Workflow ended before this call finished"}, finished_at=time())
            self.ledger.event(run_id, "workflow.finished", {"status": final["status"], "error": final.get("error")})

    async def _on_call(self, run_id, call_id, operation, arguments):
        run = self.store.get(RUN, run_id)
        self._actor(run)
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128 or operation not in {"agent", "phase", "log"} or not isinstance(arguments, dict):
            raise ValueError("Invalid workflow host request")
        if len(json.dumps(arguments, allow_nan=False).encode()) > 64000:
            raise ValueError("Workflow host request exceeds 64 KiB")
        if operation == "phase":
            if (arguments.keys() - {"phase_id", "name", "state", "status", "error"}
                    or arguments.get("state") not in {"start", "end"}
                    or not isinstance(arguments.get("name"), str)
                    or not isinstance(arguments.get("phase_id"), str)
                    or len(arguments["phase_id"]) > 128):
                raise ValueError("phase expects a bounded phase ID, name and start/end state")
            if arguments["state"] == "end":
                phase = next((item for item in self.ledger.calls(run)
                    if item["operation"] == "phase" and item.get("phase_id") == arguments["phase_id"]), None)
                if not phase:
                    raise ValueError("Phase did not start in this workflow")
                ending_digest = digest(arguments)
                if phase.get("end_digest"):
                    if phase["end_digest"] != ending_digest:
                        raise Conflict("Phase completion changed")
                    return {}
                status = "failed" if arguments.get("status") == "failed" else "completed"
                self.ledger.patch(CALL, phase["id"], status=status,
                    error=_public_value(arguments.get("error")), finished_at=time(), end_digest=ending_digest)
                self.ledger.event(run_id, "workflow.call.finished", {"call_id": phase["id"], "status": status})
                return {}
        name = str(arguments.get("name") or arguments.get("message") or operation)[:200]
        call, created = self.ledger.call(run_id, call_id, operation, arguments, name)
        if not created:
            if call["status"] == "completed":
                return call.get("output")
            raise Conflict("Previous workflow call is incomplete; it will not be replayed")
        self.ledger.patch(CALL, call["id"], status="running", started_at=time())
        self.ledger.event(run_id, "workflow.call.started", {"call_id": call["id"], "operation": operation, "name": name})
        try:
            if operation == "agent":
                output = await self._agent(run_id, call, arguments)
            elif operation == "log":
                if arguments.keys() - {"message", "data"} or not isinstance(arguments.get("message"), str):
                    raise ValueError("log expects message text and optional JSON data")
                output = {"message": arguments["message"][:4000], "data": arguments.get("data")}
            else:
                self.ledger.patch(CALL, call["id"], phase_id=arguments["phase_id"])
                return {}
            output = _public_value(output)
            self.ledger.patch(CALL, call["id"], status="completed", output=output, finished_at=time())
            self.ledger.event(run_id, "workflow.call.finished", {"call_id": call["id"], "status": "completed"})
            return output
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            self.ledger.patch(CALL, call["id"], status=status, error=_error(exc, "agent_failed"), finished_at=time())
            self.ledger.event(run_id, "workflow.call.finished", {"call_id": call["id"], "status": status})
            raise
        finally:
            self._refresh_status(run_id)

    async def _agent(self, run_id, call, arguments):
        if arguments.keys() - {"prompt", "name", "engine", "provider_id", "model"}:
            raise ValueError("agent accepts prompt, name, engine, provider_id and model only")
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 64000:
            raise ValueError("agent prompt must contain 1..64000 characters")
        run = self.store.get(RUN, run_id)
        agents = run.get("agent_count")
        if agents is None:
            agents = sum(item["operation"] == "agent" for item in self.ledger.calls(run))
        if run["limits"].get("max_agents") is not None and agents > run["limits"]["max_agents"]:
            raise WorkflowError("agent_limit", "Workflow agent limit exceeded")
        async with self._semaphores[run_id] or nullcontext():
            run = self.store.get(RUN, run_id)
            principal = self._actor(run)
            target = run["binding"]["target"]
            if not target.get("cwd"):
                raise ValueError("Choose a project or parent conversation before delegating agents")
            engine = arguments.get("engine") or target.get("engine") or "internal"
            provider = arguments.get("provider_id") or target.get("provider_id")
            model = arguments.get("model") or target.get("model")
            for key, value in (("engine", engine), ("provider_id", provider), ("model", model)):
                if value is not None and (not isinstance(value, str) or len(value) > 200):
                    raise ValueError(f"agent {key} must be a bounded identifier")
            if target.get("custom_agent_id") and (engine != target.get("engine") or provider != target.get("provider_id") or model != target.get("model")):
                raise PermissionError("An agent preset cannot be bypassed by workflow model overrides")
            if engine != "internal":
                gateway = getattr(self.workspace.state, "engines", None)
                adapter = gateway.adapter(engine) if gateway and hasattr(gateway, "adapter") else None
                notes = adapter.capabilities().notes if adapter else {}
                if notes.get("workflow_child_delegation") != "disabled":
                    raise WorkflowError("engine_unsupported", "This engine cannot verify disabled nested delegation for workflow children")
            # Legacy session task quotas never constrain workflow children.
            # Optional manual workflow bounds remain on this coordinator.
            limits = {key: None for key in agent_limits()}
            session = self.workspace.create_session(principal, title=call["name"], project_id=run.get("project_id"),
                cwd=target["cwd"], engine=engine, provider_id=provider, model=model, mode="agent",
                worktree_id=target.get("worktree_id"), custom_agent_id=target.get("custom_agent_id"),
                reasoning_config=target.get("reasoning_config") if engine == target.get("engine") and model == target.get("model") else None,
                run_limits=limits, **({"_workflow_parent_id": run_id} if run.get("project_id") is None else {}))
            self.ledger.patch(CALL, call["id"], session_id=session["id"])
            # Enrollment precedes WorkspaceService.send's on_created callback;
            # native/internal children are guarded before their first effect.
            self.store.create("workflow_child_session", principal.id,
                {"run_id": run_id, "call_id": call["id"]}, run.get("project_id"), session["id"])
            self._actor(self.store.get(RUN, run_id))
            task = await self.workspace.send(principal, session["id"], prompt=prompt,
                request_id="workflow:" + call["id"], limits=limits,
                managed_session_id=run["binding"].get("managed_session_id"),
                dispatch_guard=lambda _row: self._actor(self.store.get(RUN, run_id)))
            self.ledger.patch(CALL, call["id"], task_id=task["id"])
            metadata = self.store.get("task", task["id"])
            if metadata:
                self.store.update("task", task["id"], {**metadata, "workflow_run_id": run_id, "workflow_call_id": call["id"]}, metadata["revision"])
            while True:
                run = self.store.get(RUN, run_id)
                self._actor(run)
                task = self.workspace.agents.get_task(task["id"])
                if not task:
                    raise WorkflowError("child_unavailable", "Child task ledger is unavailable")
                status = task["status"]
                if status in _TASK_TERMINAL:
                    if status != "completed":
                        raise WorkflowError("agent_" + status, task.get("error") or "Child agent " + status)
                    return {"session_id": session["id"], "task_id": task["id"], "status": status,
                            "output": _public_value(task.get("result"))}
                if status in {"recovering", "recovery_confirmation_required", "paused"}:
                    raise WorkflowError("child_needs_attention", "Child agent requires recovery or a new instruction")
                visible = "awaiting_approval" if status == "awaiting_approval" else "running"
                current = self.store.get(CALL, call["id"])
                if current["status"] != visible:
                    self.ledger.patch(CALL, call["id"], status=visible)
                    self._refresh_status(run_id)
                    self.ledger.event(run_id, "workflow.call.status", {"call_id": call["id"], "status": visible})
                await asyncio.sleep(.1)

    async def _stop_children(self, run_id):
        run = self.store.get(RUN, run_id)
        pending, uncertain = [], False
        for call in self.ledger.calls(run):
            if (call.get("error") or {}).get("code") in {"child_needs_attention", "child_unavailable", "unknown_child_outcome"}:
                uncertain = True
            tid = call.get("task_id")
            if not tid and call.get("session_id"):
                # A send may have persisted its durable receipt before its
                # response was lost. Resolve that exact request, never resend.
                key = "workflow:" + call["id"]
                with self.store.lock:
                    receipt = self.store.db.execute("SELECT result FROM requests WHERE owner=? AND key=?", (run["owner"], key)).fetchone()
                tid = receipt["result"] if receipt else None
                if receipt and not tid:
                    uncertain = True
                if tid:
                    self.ledger.patch(CALL, call["id"], task_id=tid)
            if not tid:
                continue
            task = self.workspace.agents.get_task(tid)
            if task is None:
                uncertain = True
            if task and task["status"] not in _TASK_TERMINAL:
                try:
                    if task.get("engine", "internal") == "internal":
                        self.workspace.state.agent.cancel(tid)
                    else:
                        await asyncio.wait_for(self.workspace.state.engines.cancel(tid), timeout=2)
                except Exception:
                    pass
                pending.append(tid)
        deadline = monotonic() + 3
        while pending and monotonic() < deadline:
            pending = [tid for tid in pending if (self.workspace.agents.get_task(tid) or {}).get("status") not in _TASK_TERMINAL]
            if pending:
                await asyncio.sleep(.05)
        return bool(pending) or uncertain

    async def cancel(self, principal, identifier):
        run = self._owned(principal, RUN, identifier, "agent-control")
        if run["status"] in TERMINAL:
            return self.get_run(principal, identifier)
        self.ledger.patch(RUN, identifier, status="cancelling")
        event = self._cancels.get(identifier)
        if event:
            event.set()
        worker = self._workers.get(identifier)
        if worker:
            worker.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        if (self.store.get(RUN, identifier) or {}).get("status") == "cancelling":
            uncertain = await self._stop_children(identifier)
            self.ledger.patch(RUN, identifier, status="needs_attention" if uncertain else "cancelled", finished_at=time())
        return self.get_run(principal, identifier)

    async def close(self):
        self._closed = True
        for identifier in list(self._workers):
            worker = self._workers.get(identifier)
            if worker:
                self._cancels[identifier].set()
                worker.cancel()
        if self._workers:
            await asyncio.gather(*list(self._workers.values()), return_exceptions=True)
        for run in self.store.list(RUN):
            if run["status"] in _LIVE:
                uncertain = await self._stop_children(run["id"])
                self.ledger.patch(RUN, run["id"], status="needs_attention" if uncertain else "cancelled", finished_at=time())
