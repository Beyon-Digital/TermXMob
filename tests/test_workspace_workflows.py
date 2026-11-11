"""Workflow coordination uses owned sessions and the existing send boundary.

The JavaScript interpreter is deliberately injected here; its real-process
verification lives in test_workflow_runtime and remains capability gated.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from termx.agent.limits import resolve_limits
from termx.identity import Principal, SessionIdentity
from termx.workspace.store import Conflict, WorkspaceStore
from termx.workspace.workflows.service import WorkflowService
from termx.workspace.workflows.store import CALL, DEFINITION, RUN


OWNER = Principal("owner", "Owner", ("agent-view", "agent-control", "agent-run"), 1)
OTHER = Principal("other", "Other", OWNER.scopes, 1)


class FakeRuntime:
    def __init__(self, program=None, available=True):
        self.program = program
        self.available = available
        self.executions = 0

    def capabilities(self):
        return {"available": self.available, "reason": None if self.available else "QuickJS is unavailable"}

    async def validate(self, source, limits=None):
        return {"valid": self.available, **({"unsupported": True, "error": {"code": "runtime_unavailable", "message": "QuickJS is unavailable"}} if not self.available else {})}

    async def run(self, source, args, limits, on_call, cancel_event):
        self.executions += 1
        self.last_limits = dict(limits)
        result = await self.program(args, on_call, cancel_event) if self.program else {"source": source, "args": args}
        return {"status": "completed", "result": result}


class FakeWorkspace:
    def __init__(self, tmp_path):
        self.store = WorkspaceStore(tmp_path / "workspace.sqlite3")
        self.owner = OWNER
        self.session_identity = SessionIdentity(OWNER, "managed", 99999999999)
        self.denied_projects = set()
        self.tasks = {}
        self.sent = []
        self.next_status = "completed"
        self.cancel_ack = True
        self.root = str(tmp_path)
        self.agents = SimpleNamespace(get_task=lambda tid: self.tasks.get(tid))
        self.state = SimpleNamespace(
            identity=SimpleNamespace(principal_by_id=lambda identifier: self.owner if self.owner and self.owner.id == identifier else None,
                                     session_by_id=lambda identifier: self.session_identity if identifier == "managed" else None),
            projects=SimpleNamespace(project=lambda identifier: {"id": identifier, "path": self.root}),
            agent=SimpleNamespace(cancel=self._cancel), engines=SimpleNamespace(cancel=self._native_cancel))

    def require(self, principal, scope, project_id=None, cwd=None, **kwargs):
        if scope not in principal.scopes or project_id in self.denied_projects:
            raise PermissionError("Permission denied")

    def record(self, principal, kind, identifier, *, scope="agent-view"):
        row = self.store.get(kind, identifier)
        if not row or row["owner"] != principal.id:
            raise KeyError(identifier)
        self.require(principal, scope, row.get("project_id"))
        return row

    def session(self, principal, identifier, *, turns=True):
        return self.record(principal, "conversation", identifier)

    @staticmethod
    def execution_target(row):
        return {key: row.get(key) for key in ("engine", "provider_id", "model", "mode", "workflow", "extension_ids", "runner_id", "runner_credential_ref", "project_id", "cwd", "worktree_id", "worktree_digest", "worktree_branch", "run_limits", "custom_agent_id", "custom_agent_revision", "reasoning_config")}

    def validate_task_execution(self, task_id):
        if getattr(self, "workflows", None):
            self.workflows.validate_task_execution(task_id)

    def scratch_root(self, owner):
        return Path(self.root) / "scratch" / owner

    def create_session(self, principal, **options):
        workflow_parent = options.pop("_workflow_parent_id", None)
        if workflow_parent:
            self.workflows.child_creation_context(principal, workflow_parent, options.get("project_id"), options.get("cwd"))
        self.require(principal, "agent-control", options.get("project_id"))
        return self.store.create("conversation", principal.id, {**options,
            "run_limits": resolve_limits(options.get("run_limits"))}, options.get("project_id"))

    async def send(self, principal, identifier, *, prompt, request_id, limits, managed_session_id=None, dispatch_guard=None):
        row = self.record(principal, "conversation", identifier, scope="agent-run")
        if dispatch_guard:
            dispatch_guard(row)
        tid = uuid4().hex
        task = {"id": tid, "status": self.next_status, "result": prompt, "engine": row["engine"], "mode": "agent", "limits": limits}
        self.tasks[tid] = task
        self.sent.append({"prompt": prompt, "request_id": request_id, "limits": limits, "task_id": tid,
                          "session_id": identifier, "managed_session_id": managed_session_id})
        self.store.create("task", principal.id, {"conversation_id": identifier}, row.get("project_id"), tid)
        with self.store.lock:
            self.store.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)", (principal.id, request_id, "fake", "dispatched", tid, 0))
            self.store.db.commit()
        return task

    def _cancel(self, task_id):
        if self.cancel_ack:
            self.tasks[task_id]["status"] = "cancelled"

    async def _native_cancel(self, task_id):
        self._cancel(task_id)


def setup_service(tmp_path, program=None, available=True):
    workspace = FakeWorkspace(tmp_path)
    runtime = FakeRuntime(program, available)
    service = WorkflowService(workspace, runtime)
    workspace.workflows = service
    return workspace, runtime, service


async def terminal(service, identifier, owner=OWNER):
    for _ in range(200):
        row = service.get_run(owner, identifier)
        if row["status"] in {"completed", "failed", "cancelled", "needs_attention"}:
            return row
        await asyncio.sleep(.01)
    raise AssertionError("workflow never became terminal")


def test_definition_owner_revision_archive_and_create_idempotency(tmp_path):
    workspace, _, service = setup_service(tmp_path)
    row = service.create(OWNER, request_id="create-001", name="Review", source="return args;", project_id="p")
    assert service.create(OWNER, request_id="create-001", name="Review", source="return args;", project_id="p")["id"] == row["id"]
    with pytest.raises(Conflict):
        service.create(OWNER, request_id="create-001", name="Changed", source="return args;", project_id="p")
    with pytest.raises(KeyError):
        service.get(OTHER, row["id"])
    changed = service.update(OWNER, row["id"], revision=1, changes={"archived": True})
    assert changed["revision"] == 2 and service.list(OWNER) == []
    assert service.list(OWNER, archived=True)[0]["id"] == row["id"]
    with pytest.raises(Conflict):
        service.update(OWNER, row["id"], revision=1, changes={"name": "Stale"})
    with pytest.raises(ValueError, match="project boundary"):
        service.update(OWNER, row["id"], revision=2, changes={"project_id": "other"})
    workspace.store.close()


def test_project_library_includes_reusable_global_definitions(tmp_path):
    workspace, _, service = setup_service(tmp_path)
    shared = service.create(OWNER, request_id="shared-001", name="Shared", source="return 1;")
    scoped = service.create(OWNER, request_id="scoped-001", name="Scoped", source="return 1;", project_id="p")
    service.create(OWNER, request_id="foreign-001", name="Other project", source="return 1;", project_id="elsewhere")
    assert {item["id"] for item in service.list(OWNER, project_id="p")} == {shared["id"], scoped["id"]}
    workspace.store.close()


def test_run_snapshot_idempotency_and_archive_preserves_history(tmp_path):
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path)
        definition = service.create(OWNER, request_id="create-001", name="Review", source="return args;")
        run = await service.start(OWNER, request_id="run-00001", workflow_id=definition["id"], revision=1, args={"n": 2})
        result = await terminal(service, run["id"])
        assert result["result"]["args"] == {"n": 2}
        service.update(OWNER, definition["id"], revision=1, changes={"source": "return 9;", "archived": True})
        duplicate = await service.start(OWNER, request_id="run-00001", workflow_id=definition["id"], revision=1, args={"n": 2})
        assert duplicate["id"] == run["id"] and runtime.executions == 1
        assert duplicate["snapshot"]["source"] == "return args;"
        with pytest.raises(Conflict):
            await service.start(OWNER, request_id="run-00001", workflow_id=definition["id"], revision=1, args={"n": 3})
        assert service.runs(OWNER, workflow_id=definition["id"])[0]["id"] == run["id"]
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_missing_runtime_validation_and_start_are_explicit(tmp_path):
    async def scenario():
        workspace, _, service = setup_service(tmp_path, available=False)
        definition = service.create(OWNER, request_id="create-001", name="Saved", source="return 1;")
        assert service.get(OWNER, definition["id"])["source"] == "return 1;"
        assert (await service.validate(OWNER, source="return 1;"))["unsupported"] is True
        with pytest.raises(ValueError, match="QuickJS"):
            await service.start(OWNER, request_id="run-00001", source="return 1;")
        assert service.runs(OWNER) == []
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_agent_uses_owned_send_receipt_and_keeps_real_approval_wait(tmp_path):
    async def program(args, host, cancel):
        return await host("1", "agent", {"name": "Check", "prompt": "Inspect the project"})
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        workspace.next_status = "awaiting_approval"
        run = await service.start(OWNER, request_id="run-00001", source="agent fixture", project_id="p")
        for _ in range(100):
            await asyncio.sleep(.01)
            if service.get_run(OWNER, run["id"])["status"] == "awaiting_approval":
                break
        waiting = service.get_run(OWNER, run["id"])
        assert waiting["status"] == "awaiting_approval"
        call = waiting["calls"][0]
        assert call["status"] == "awaiting_approval" and workspace.sent[0]["request_id"] == "workflow:" + call["id"]
        assert workspace.store.get("task", call["task_id"])["workflow_run_id"] == run["id"]
        workspace.tasks[call["task_id"]].update(status="completed", result="Verified")
        finished = await terminal(service, run["id"])
        assert finished["result"]["output"] == "Verified"
        assert finished["calls"][0]["status"] == "completed"
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_agent_rejects_untrusted_host_arguments_and_missing_boundary(tmp_path):
    async def scenario(arguments, project_id):
        async def program(args, host, cancel):
            return await host("1", "agent", arguments)
        workspace, _, service = setup_service(tmp_path / uuid4().hex, program)
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id=project_id)
        result = await terminal(service, run["id"])
        assert result["status"] == "failed" and workspace.sent == []
        await service.close()
        workspace.store.close()
    asyncio.run(scenario({"prompt": "hello", "cwd": "/"}, "p"))
    asyncio.run(scenario({"prompt": "hello"}, None))


def test_cancel_acknowledges_actual_child_and_denied_approval_does_not_resume(tmp_path):
    async def program(args, host, cancel):
        return await host("1", "agent", {"prompt": "Wait"})
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path, program)
        workspace.next_status = "awaiting_approval"
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id="p")
        while not workspace.sent:
            await asyncio.sleep(.01)
        cancelled = await service.cancel(OWNER, run["id"])
        assert cancelled["status"] == "cancelled"
        assert workspace.tasks[workspace.sent[0]["task_id"]]["status"] == "cancelled"
        assert runtime.executions == 1
        assert (await service.cancel(OWNER, run["id"]))["status"] == "cancelled"
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_cancel_before_worker_starts_becomes_terminal(tmp_path):
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path)
        run = await service.start(OWNER, request_id="run-00001", source="return 1;")
        cancelled = await service.cancel(OWNER, run["id"])
        assert cancelled["status"] == "cancelled" and runtime.executions == 0
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_owner_project_and_origin_session_revocation_stop_new_effects(tmp_path):
    async def program(args, host, cancel):
        await host("1", "log", {"message": "ready"})
        workspace.session_identity = None
        return await host("2", "agent", {"prompt": "Must not dispatch"})
    async def scenario():
        nonlocal workspace
        workspace, _, service = setup_service(tmp_path, program)
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id="p", managed_session_id="managed")
        with pytest.raises(KeyError):
            service.get_run(OTHER, run["id"])
        result = await terminal(service, run["id"])
        assert result["status"] == "failed" and workspace.sent == []
        workspace.denied_projects.add("p")
        with pytest.raises(PermissionError):
            service.get_run(OWNER, run["id"])
        await service.close()
        workspace.store.close()
    workspace = None
    asyncio.run(scenario())


def test_phase_duration_and_duplicate_host_calls_have_one_receipt(tmp_path):
    async def program(args, host, cancel):
        await host("1", "phase", {"phase_id": "p1", "name": "Review", "state": "start"})
        await host("2", "log", {"message": "hello"})
        await host("2", "log", {"message": "hello"})
        await host("3", "phase", {"phase_id": "p1", "name": "Review", "state": "end", "status": "completed"})
        return "done"
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        run = await service.start(OWNER, request_id="run-00001", source="fixture")
        result = await terminal(service, run["id"])
        assert result["status"] == "completed"
        assert len(result["calls"]) == 2 and [call["sequence"] for call in result["calls"]] == [1, 2]
        assert result["calls"][0]["finished_at"] >= result["calls"][0]["started_at"]
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_restart_marks_uncertain_runs_and_calls_without_replay(tmp_path):
    workspace, runtime, service = setup_service(tmp_path)
    run = workspace.store.create(RUN, OWNER.id, {"status": "running", "call_ids": [], "events": []})
    call = workspace.store.create(CALL, OWNER.id, {"run_id": run["id"], "status": "running"})
    service.ledger.patch(RUN, run["id"], call_ids=[call["id"]])
    WorkflowService(workspace, runtime)
    assert workspace.store.get(RUN, run["id"])["status"] == "needs_attention"
    assert workspace.store.get(CALL, call["id"])["status"] == "needs_attention"
    assert runtime.executions == 0
    workspace.store.close()


def test_real_workspace_send_retains_plan_and_tool_approval_pipeline(tmp_path):
    """Real manager, stores, shell effect and explicit human plan decision.

    Only inference and JavaScript evaluation are deterministic fixtures.
    """
    from test_agent import FakeAdapter, wait_for_status
    from test_workspace_orchestrator import setup, close

    async def program(args, host, cancel):
        return await host("1", "agent", {"name": "Verify", "prompt": "Inspect workflow boundary"})

    async def scenario():
        workspace, owner, manager, ledger, _primary = setup(tmp_path, FakeAdapter("printf workflow-proof"))
        service = WorkflowService(workspace, FakeRuntime(program))
        workspace.workflows = service
        parent = workspace.create_session(owner, project_id="fixture-project", cwd=str(tmp_path), mode="agent", provider_id="fake", model="fake-model")
        try:
            run = await service.start(owner, request_id="real-workflow-001", source="fixture", parent_session_id=parent["id"])
            for _ in range(200):
                current = service.get_run(owner, run["id"])
                if current["calls"] and current["calls"][0].get("task_id"):
                    break
                await asyncio.sleep(.01)
            child = current["calls"][0]
            task = ledger.get_task(child["task_id"])
            assert task["status"] == "awaiting_approval"
            approvals = ledger.approvals(task["id"])
            assert len(approvals) == 1 and approvals[0]["kind"] == "plan"
            assert not any(event["type"] == "process.started" for event in ledger.events(task["id"]))
            await manager.resolve_approval(task["id"], approvals[0]["id"], "approved")
            await wait_for_status(ledger, task["id"], "completed")
            finished = await terminal(service, run["id"], owner)
            assert finished["status"] == "completed" and finished["result"]["output"] == "Verified successfully."
            assert any(event["type"] == "process.output" and "workflow-proof" in event["payload"].get("chunk", "") for event in ledger.events(task["id"]))
            assert len(workspace.session(owner, child["session_id"])["turns"]) == 1
        finally:
            await service.close()
            await close(workspace, manager, ledger)
    asyncio.run(scenario())


def test_pre_send_binding_blocks_nested_delegation_and_checks_live_run(tmp_path):
    workspace, _, service = setup_service(tmp_path)
    run = workspace.store.create(RUN, OWNER.id, {"status": "running", "binding": {"target": {"cwd": workspace.root}, "policy_version": 1}})
    session = workspace.create_session(OWNER, project_id="p", cwd=workspace.root, mode="agent", engine="internal")
    workspace.store.create("workflow_child_session", OWNER.id, {"run_id": run["id"], "call_id": "call"}, "p", session["id"])
    # This is exactly the window inside send(): metadata exists, but the
    # workflow runner has not received the task and added its direct marker.
    workspace.tasks["child"] = {"id": "child", "status": "running", "mode": "agent"}
    workspace.store.create("task", OWNER.id, {"conversation_id": session["id"]}, "p", "child")
    with pytest.raises(PermissionError, match="cannot delegate"):
        service.validate_delegation("child")
    service.ledger.patch(RUN, run["id"], status="cancelled")
    with pytest.raises(PermissionError, match="no longer accepts"):
        service.validate_task_execution("child")
    workspace.store.close()


def test_missing_recorded_child_outcome_is_needs_attention(tmp_path):
    async def program(args, host, cancel):
        output = await host("1", "agent", {"prompt": "Complete then lose receipt"})
        workspace.tasks.pop(output["task_id"])
        return output
    async def scenario():
        nonlocal workspace
        workspace, _, service = setup_service(tmp_path, program)
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id="p")
        result = await terminal(service, run["id"])
        assert result["status"] == "needs_attention" and result["error"]["code"] == "unknown_child_outcome"
        assert result["calls"][0]["status"] == "needs_attention"
        await service.close()
        workspace.store.close()
    workspace = None
    asyncio.run(scenario())


def test_optional_manual_concurrency_does_not_impose_child_task_budgets(tmp_path):
    async def program(args, host, cancel):
        return await asyncio.gather(*(host(str(index), "agent", {"prompt": str(index)}) for index in range(4)))
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        workspace.next_status = "running"
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id="p",
                                  limits={"max_agents": 4, "concurrency": 2})
        await asyncio.sleep(.05)
        assert len(workspace.sent) == 2
        assert all(all(value is None for value in item["limits"].values()) for item in workspace.sent)
        for item in list(workspace.sent):
            workspace.tasks[item["task_id"]]["status"] = "completed"
        for _ in range(100):
            if len(workspace.sent) == 4:
                break
            await asyncio.sleep(.01)
        assert len(workspace.sent) == 4
        assert all(all(value is None for value in item["limits"].values()) for item in workspace.sent)
        for item in workspace.sent:
            workspace.tasks[item["task_id"]]["status"] = "completed"
        assert (await terminal(service, run["id"]))["status"] == "completed"
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_caught_child_recovery_still_requires_attention(tmp_path):
    async def program(args, host, cancel):
        try:
            await host("1", "agent", {"prompt": "Interrupted action"})
        except ValueError:
            return "caught"
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        workspace.next_status = "recovery_confirmation_required"
        run = await service.start(OWNER, request_id="run-00001", source="fixture", project_id="p")
        result = await terminal(service, run["id"])
        assert result["status"] == "needs_attention"
        assert result["error"]["code"] == "unknown_child_outcome"
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_owned_scratch_parent_is_reused_only_through_trusted_run_context(tmp_path):
    async def program(args, host, cancel):
        return await host("1", "agent", {"prompt": "Inspect the same scratch"})
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        owned = workspace.scratch_root(OWNER.id) / "parent"
        owned.mkdir(parents=True)
        sibling = workspace.scratch_root(OWNER.id) / "sibling"
        sibling.mkdir()
        parent = workspace.create_session(OWNER, cwd=str(owned), project_id=None, mode="agent", engine="internal")
        run = await service.start(OWNER, request_id="run-00001", source="fixture", parent_session_id=parent["id"])
        result = await terminal(service, run["id"])
        assert result["status"] == "completed"
        child = result["calls"][0]
        assert workspace.session(OWNER, child["session_id"])["cwd"] == str(owned)
        assert service.delegated_boundary(OWNER, "conversation", child["session_id"], str(owned), "agent-view")
        assert service.delegated_boundary(OWNER, "task", child["task_id"], str(owned), "agent-view")
        assert not service.delegated_boundary(OWNER, "conversation", child["session_id"], str(sibling), "agent-view")
        assert not service.delegated_boundary(OTHER, "conversation", child["session_id"], str(owned), "agent-view")
        assert not service.delegated_boundary(OWNER, "conversation", parent["id"], str(owned), "agent-view")
        with pytest.raises(PermissionError, match="no longer accepts"):
            service.child_creation_context(OWNER, run["id"], None, str(owned))
        workspace.store.delete("conversation", parent["id"])
        assert not service.delegated_boundary(OWNER, "conversation", child["session_id"], str(owned), "agent-view")
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_private_scratch_context_rejects_changed_boundary_before_creation(tmp_path):
    async def scenario():
        workspace, _, service = setup_service(tmp_path)
        owned = workspace.scratch_root(OWNER.id) / "parent"
        owned.mkdir(parents=True)
        sibling = owned.parent / "sibling"
        sibling.mkdir()
        parent = workspace.create_session(OWNER, cwd=str(owned), project_id=None, mode="agent", engine="internal")
        binding = service._boundary(OWNER, None, parent["id"], None, None)
        run = workspace.store.create(RUN, OWNER.id, {"status": "running", "parent_session_id": parent["id"], "binding": binding})
        assert service.child_creation_context(OWNER, run["id"], None, str(owned)) == {"resource_kind": "conversation", "resource_id": parent["id"]}
        with pytest.raises(PermissionError, match="cannot change"):
            service.child_creation_context(OWNER, run["id"], None, str(sibling))
        with pytest.raises(PermissionError, match="owned parent"):
            service.child_creation_context(OWNER, run["id"], "fake-project", str(owned))
        with pytest.raises(KeyError):
            service.child_creation_context(OTHER, run["id"], None, str(owned))
        workspace.store.delete(RUN, run["id"])
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_real_trusted_operator_scratch_uses_owned_parent_without_host_scope(tmp_path):
    from test_agent import FakeAdapter, wait_for_status
    from test_workspace_orchestrator import setup, close
    from termx.authorization import AuthorizationService, ROLES

    async def program(args, host, cancel):
        return await host("1", "agent", {"prompt": "Verify owned scratch"})

    async def scenario():
        workspace, _owner, manager, ledger, _primary = setup(tmp_path, FakeAdapter("printf scoped-workflow"))
        authorization = AuthorizationService(workspace.state.identity)
        workspace.state.authorization = authorization
        actor = workspace.state.identity.create_local_user("workflow-operator", "strong workflow password", list(ROLES["operator"]))
        authorization.set_role(actor.id, "operator", trusted_execution=True)
        actor = workspace.state.identity.principal_by_id(actor.id)
        assert "host-admin" not in actor.scopes
        parent = workspace.create_session(actor, mode="agent", provider_id="fake", model="fake-model")
        service = WorkflowService(workspace, FakeRuntime(program))
        workspace.workflows = service
        try:
            run = await service.start(actor, request_id="scratch-real-001", source="fixture", parent_session_id=parent["id"])
            for _ in range(200):
                current = service.get_run(actor, run["id"])
                if current["calls"] and current["calls"][0].get("task_id"):
                    break
                await asyncio.sleep(.01)
            child = current["calls"][0]
            assert child["task_id"] is not None, current
            child_session = workspace.session(actor, child["session_id"])
            assert child_session["scratch"] is True and child_session["cwd"] == parent["cwd"]
            assert Path(child_session["cwd"]).is_relative_to(workspace.scratch_root(actor.id))
            approval = ledger.approvals(child["task_id"])[0]
            assert approval["status"] == "pending" and approval["kind"] == "plan"
            await manager.resolve_approval(child["task_id"], approval["id"], "approved")
            await wait_for_status(ledger, child["task_id"], "completed")
            finished = await terminal(service, run["id"], actor)
            assert finished["status"] == "completed" and finished["allowed_actions"] == ["retry"]
            # The helper cannot turn a sibling path into authority, even for
            # an otherwise trusted operator and an owned parent run.
            outside = tmp_path / "unrelated"
            outside.mkdir()
            assert not service.delegated_boundary(actor, "conversation", child["session_id"], str(outside), "agent-view")
            authorization.set_role(actor.id, "operator", trusted_execution=False)
            with pytest.raises(HTTPException):
                workspace.record(actor, "conversation", child["session_id"], scope="agent-run")
        finally:
            await service.close()
            await close(workspace, manager, ledger)
    from fastapi import HTTPException
    asyncio.run(scenario())


def test_multiple_agent_workflows_are_unlimited_and_idempotent(tmp_path):
    async def program(args, host, cancel):
        await cancel.wait()
        return "stopped"
    async def scenario():
        workspace, _, service = setup_service(tmp_path, program)
        parent = workspace.create_session(OWNER, project_id="p", cwd=workspace.root, mode="agent", engine="internal")
        workspace.tasks["source"] = {"id": "source", "mode": "agent", "status": "running"}
        workspace.store.create("task", OWNER.id, {"conversation_id": parent["id"]}, "p", "source")
        arguments = {"source": "fixture", "project_id": "p", "parent_session_id": parent["id"], "actor_task_id": "source"}
        outcomes = await asyncio.gather(service.start(OWNER, request_id="actor-run-001", **arguments),
                                        service.start(OWNER, request_id="actor-run-002", **arguments), return_exceptions=True)
        accepted = [item for item in outcomes if isinstance(item, dict)]
        assert len(accepted) == 2
        duplicate = await service.start(OWNER, request_id="actor-run-001", **arguments)
        assert duplicate["id"] == accepted[0]["id"]
        assert service.capabilities()["source_task_aggregate_budget"] == "not_shared"
        assert duplicate["capability_limits"]["budget_scope"] == "manual_run_opt_in"
        await service.cancel(OWNER, duplicate["id"])
        await service.cancel(OWNER, accepted[1]["id"])
        next_run = await service.start(OWNER, request_id="actor-run-003", **arguments)
        assert next_run["id"] != duplicate["id"]
        await service.cancel(OWNER, next_run["id"])
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_same_actor_runs_remain_idempotent_across_database_connections(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from termx.workspace.workflows.store import WorkflowStore
    store = WorkspaceStore(tmp_path / "admission.sqlite3")
    other = WorkspaceStore(tmp_path / "admission.sqlite3")
    body = {"status": "queued", "binding": {"actor_task_id": "source"}}
    def reserve(ledger, request):
        try:
            return ledger.reserve(RUN, OWNER.id, request, request, body)
        except Conflict as exc:
            return exc
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda pair: reserve(*pair),
                [(WorkflowStore(store), "one"), (WorkflowStore(other), "two")]))
        assert sum(isinstance(item, tuple) for item in results) == 2
        assert len(store.list(RUN)) == 2
        existing = store.list(RUN)[0]
        store.update(RUN, existing["id"], {**existing, "status": "needs_attention"})
        third, created = WorkflowStore(other).reserve(RUN, OWNER.id, "three", "three", body)
        assert created and third["id"] not in {item[0]["id"] for item in results}
        again, created = WorkflowStore(other).reserve(RUN, OWNER.id, "one", "one", body)
        assert not created and again["id"] in {item[0]["id"] for item in results}
    finally:
        other.close()
        store.close()


def test_legacy_saved_limits_are_preserved_but_never_implicitly_applied(tmp_path):
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path)
        saved = workspace.store.create(DEFINITION, OWNER.id, {"name": "Legacy", "description": "", "language": "javascript",
            "source": "return 1;", "input_schema": {"type": "object"}, "limits": {"max_agents": 1, "concurrency": 1, "timeout_s": 1}, "archived": False})
        assert service.get(OWNER, saved["id"])["limits"] == {"max_agents": None, "concurrency": None, "timeout_s": None}
        unlimited = await service.start(OWNER, request_id="legacy-default", workflow_id=saved["id"], revision=saved["revision"])
        assert (await terminal(service, unlimited["id"]))["status"] == "completed"
        assert all(value is None for value in runtime.last_limits.values())
        manual = await service.start(OWNER, request_id="manual-opt-in", workflow_id=saved["id"], revision=saved["revision"], limits={"max_agents": 1000})
        assert (await terminal(service, manual["id"]))["limits"] == {"max_agents": 1000, "concurrency": None, "timeout_s": None}
        assert workspace.store.get(DEFINITION, saved["id"])["limits"] == {"max_agents": 1, "concurrency": 1, "timeout_s": 1}
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_agent_trigger_ignores_supplied_and_inherited_limits(tmp_path):
    async def program(args, host, cancel):
        return await asyncio.gather(*(host(str(index), "agent", {"prompt": str(index)}) for index in range(20)))
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path, program)
        parent = workspace.create_session(OWNER, project_id="p", cwd=workspace.root, mode="agent", engine="internal")
        legacy = {"max_steps": 1, "max_seconds": 1, "shell_timeout_s": 1, "max_parallel_subagents": 1, "max_subagents_total": 1}
        with workspace.store.lock:
            body = json.loads(workspace.store.db.execute("SELECT body FROM records WHERE kind='conversation' AND id=?", (parent["id"],)).fetchone()[0])
            workspace.store.db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?", (json.dumps({**body, "run_limits": legacy}), parent["id"]))
            workspace.store.db.commit()
        workspace.tasks["source"] = {"id": "source", "mode": "agent", "status": "running"}
        workspace.store.create("task", OWNER.id, {"conversation_id": parent["id"]}, "p", "source")
        run = await service.start(OWNER, request_id="automatic-001", source="fixture", project_id="p", parent_session_id=parent["id"],
            actor_task_id="source", limits={"max_agents": 1, "concurrency": 1, "timeout_s": 1})
        result = await terminal(service, run["id"])
        assert result["status"] == "completed" and len(workspace.sent) == 20
        assert all(value is None for value in runtime.last_limits.values())
        assert all(all(value is None for value in item["limits"].values()) for item in workspace.sent)
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())


def test_long_run_keeps_receipts_with_bounded_visible_history(tmp_path):
    workspace, _, service = setup_service(tmp_path)
    run = workspace.store.create(RUN, OWNER.id, {"status": "running", "call_ids": [], "events": [], "snapshot": {}})
    first = None
    for index in range(600):
        call, created = service.ledger.call(run["id"], str(index), "log", {"message": str(index)}, str(index))
        assert created
        first = first or call
        service.ledger.patch(CALL, call["id"], status="completed")
    stored = workspace.store.get(RUN, run["id"])
    assert stored["call_count"] == 600 and len(stored["call_ids"]) <= 513
    assert len(service.ledger.calls(stored)) == 600
    visible = service.get_run(OWNER, run["id"])
    assert visible["calls_truncated"] and len(visible["calls"]) == 512
    assert visible["calls"][0]["sequence"] == 89
    replay, created = service.ledger.call(run["id"], "0", "log", {"message": "0"}, "0")
    assert not created and replay["id"] == first["id"]
    assert workspace.store.get(CALL, first["id"]) is not None
    workspace.store.close()


def test_manual_count_and_timeout_are_enforced_only_after_explicit_opt_in(tmp_path):
    async def count_program(args, host, cancel):
        await host("1", "agent", {"prompt": "one"})
        return await host("2", "agent", {"prompt": "two"})
    async def wait_program(args, host, cancel):
        await asyncio.Event().wait()
    async def scenario():
        workspace, runtime, service = setup_service(tmp_path, count_program)
        limited = await service.start(OWNER, request_id="manual-count", source="fixture", project_id="p", limits={"max_agents": 1})
        assert (await terminal(service, limited["id"]))["status"] == "failed"
        assert len(workspace.sent) == 1
        runtime.program = wait_program
        timed = await service.start(OWNER, request_id="manual-timeout", source="fixture", limits={"timeout_s": 1})
        result = await terminal(service, timed["id"])
        assert result["status"] == "failed" and result["error"]["code"] == "timeout"
        await service.close()
        workspace.store.close()
    asyncio.run(scenario())
