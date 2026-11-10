"""Plan mode through GraphQL and the other task-level mode gates."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status, gql
from test_agent import wait_for_pending_approval, wait_for_status
from test_plan_mode import PlanAdapter, _call
from termx.agent.secrets import CredentialStore
from termx.app import AppState, create_app
from termx.sandbox.host import HostSandboxRunner

HEADERS = {"X-Termx-Passcode": "secret"}

_CREATE = (
    "mutation($input: AgentTaskInput!) "
    "{ create_agent_task(input: $input) { id mode status } }"
)
_TASK = "query($id: String!) { agent_task(task_id: $id) { id mode status plan } }"
_RESOLVE = (
    "mutation($task: String!, $approval: String!, $input: AgentApprovalInput!) "
    "{ resolve_agent_approval(task_id: $task, approval_id: $approval, input: $input) }"
)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script):
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TERMX_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("TERMX_ENGINE_STARTUP_REFRESH", "0")
    adapter = PlanAdapter(script)
    state = AppState(passcode="secret", credentials=CredentialStore(memory={}), adapter_factory=lambda *_: adapter)
    state.agent._runner_for = lambda profile, **kw: HostSandboxRunner(profile=profile)
    state.agent.save_provider(
        provider_id="fake", kind="openai-compatible", name="Fake",
        base_url="http://127.0.0.1:9999/v1", model="fake-model", capabilities=["shell"], api_key="k",
    )
    return state, adapter, TestClient(create_app(state, web_dir=None))


def _input(tmp_path: Path, **overrides):
    return {"prompt": "Add a --verbose flag", "cwd": str(tmp_path), "provider_id": "fake", **overrides}


def test_graphql_plan_task_revise_then_approve(tmp_path, monkeypatch):
    script = [
        ("", [_call("exit_plan", "c1", plan_markdown="v1 plan")]),
        ("", [_call("exit_plan", "c2", plan_markdown="v2 plan with tests")]),
        ("Implemented.", []),
    ]
    state, adapter, client = _client(tmp_path, monkeypatch, script)
    with client:
        created = data(client, _CREATE, "create_agent_task", {"input": _input(tmp_path, mode="plan")}, HEADERS)
        assert created["mode"] == "plan"
        task_id = created["id"]
        _task, first = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")

        shown = data(client, _TASK, "agent_task", {"id": task_id}, HEADERS)
        assert shown["mode"] == "plan" and shown["status"] == "awaiting_approval"
        assert shown["plan"]["markdown"] == "v1 plan"

        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": first["id"],
            "input": {"decision": "revise", "feedback": "Also cover --quiet"},
        }, HEADERS)
        _task, second = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")
        assert second["id"] != first["id"]
        shown = data(client, _TASK, "agent_task", {"id": task_id}, HEADERS)
        assert shown["plan"]["markdown"] == "v2 plan with tests"
        assert "--quiet" in str(adapter.inputs[-1])

        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": second["id"], "input": {"decision": "approved"},
        }, HEADERS)
        client.portal.call(wait_for_status, state.agent_store, task_id, "completed")
        assert data(client, _TASK, "agent_task", {"id": task_id}, HEADERS)["mode"] == "agent"


def test_graphql_revise_without_feedback_is_accepted(tmp_path, monkeypatch):
    script = [
        ("", [_call("exit_plan", "c1", plan_markdown="v1")]),
        ("", [_call("exit_plan", "c2", plan_markdown="v2")]),
    ]
    state, _adapter, client = _client(tmp_path, monkeypatch, script)
    with client:
        task_id = data(client, _CREATE, "create_agent_task", {"input": _input(tmp_path, mode="plan")}, HEADERS)["id"]
        _task, first = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")
        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": first["id"], "input": {"decision": "revise"},
        }, HEADERS)
        client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")


def test_graphql_revise_is_rejected_outside_plan_mode(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    with client:
        task_id = data(client, _CREATE, "create_agent_task", {"input": _input(tmp_path, mode="agent")}, HEADERS)["id"]
        _task, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")
        variables = {"task": task_id, "approval": approval["id"], "input": {"decision": "revise", "feedback": "x"}}
        assert err_status(client, _RESOLVE, variables, HEADERS) == 409
        variables["input"] = {"decision": "nonsense"}
        assert err_status(client, _RESOLVE, variables, HEADERS) == 422
        variables["input"] = {"decision": "revise", "feedback": "x" * 10_001}
        assert err_status(client, _RESOLVE, variables, HEADERS) == 422


def test_graphql_denying_a_plan_cancels(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [("", [_call("exit_plan", "c1", plan_markdown="v1")])])
    with client:
        task_id = data(client, _CREATE, "create_agent_task", {"input": _input(tmp_path, mode="plan")}, HEADERS)["id"]
        _task, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")
        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": approval["id"], "input": {"decision": "denied"},
        }, HEADERS)
        client.portal.call(wait_for_status, state.agent_store, task_id, "cancelled")


def test_graphql_rejects_plan_mode_for_native_engines(tmp_path, monkeypatch):
    _state, _adapter, client = _client(tmp_path, monkeypatch, [])
    with client:
        res = gql(client, _CREATE, {"input": _input(tmp_path, mode="plan", engine="codex")}, HEADERS).json()
        assert res["errors"][0]["extensions"]["http_status"] == 400
        assert "plan" in res["errors"][0]["message"].lower()


def test_workspace_conversations_accept_plan_only_for_internal_local_execution(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    state.identity.setup_owner("plan-owner", "plan-fixture-password-123")
    login = asyncio.run(state.identity.login(
        "local-password", {"username": "plan-owner", "password": "plan-fixture-password-123"}, peer="fixture"))
    principal = state.identity.resolve(login.access_token).principal
    project = state.projects.register(str(tmp_path), name="Plan fixture")
    with client:
        session = state.workspace.create_session(
            principal, title="Plan", project_id=project["id"], cwd=str(tmp_path),
            provider_id="fake", model="fake-model", mode="plan")
        assert session["mode"] == "plan"
        other = state.workspace.create_session(
            principal, title="Ask", project_id=project["id"], cwd=str(tmp_path),
            provider_id="fake", model="fake-model", mode="ask")
        updated = state.workspace.update_session(principal, other["id"], revision=other["revision"], changes={"mode": "plan"})
        assert updated["mode"] == "plan"
        with pytest.raises(ValueError, match="Plan mode"):
            state.workspace.create_session(principal, title="Native", project_id=project["id"], cwd=str(tmp_path), engine="codex", mode="plan")
        with pytest.raises(ValueError, match="Plan mode"):
            state.workspace.update_session(principal, other["id"], revision=updated["revision"], changes={"runner_id": "runner-1"})
        with pytest.raises(ValueError, match="Unknown"):
            state.workspace.update_session(principal, other["id"], revision=updated["revision"], changes={"mode": "yolo"})


def test_runner_tasks_reject_plan_mode():
    from fastapi import HTTPException
    from termx.runners.agent import RunnerAgentService

    service = object.__new__(RunnerAgentService)  # the mode gate runs before any service state is used
    with pytest.raises(HTTPException) as caught:
        asyncio.run(service.create_task(None, "runner", "ref", "prompt", mode="plan", request_id="request-0001"))
    assert caught.value.status_code == 400 and "Ask or Agent" in caught.value.detail
