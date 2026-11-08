"""Approving a plan moves its conversation to Agent mode, through REST and GraphQL alike."""
from __future__ import annotations

import asyncio
from functools import partial

import pytest

from _gql import data
from test_agent import wait_for_pending_approval, wait_for_status
from test_plan_mode import _call
from test_plan_mode_api import _RESOLVE, _client


def _owner(state):
    state.identity.setup_owner("plan-owner", "plan-fixture-password-123")
    login = asyncio.run(state.identity.login(
        "local-password", {"username": "plan-owner", "password": "plan-fixture-password-123"}, peer="fixture"))
    return state.identity.resolve(login.access_token).principal, {"Authorization": "Bearer " + login.access_token}


def _plan_turn(tmp_path, monkeypatch):
    script = [("", [_call("exit_plan", "c1", plan_markdown="v1 plan")]), ("Implemented.", [])]
    state, _adapter, client = _client(tmp_path, monkeypatch, script)
    principal, headers = _owner(state)
    project = state.projects.register(str(tmp_path), name="Plan fixture")
    session = state.workspace.create_session(
        principal, title="Plan", project_id=project["id"], cwd=str(tmp_path),
        provider_id="fake", model="fake-model", mode="plan")
    return state, client, principal, headers, session


def _send(client, state, principal, session):
    task = client.portal.call(partial(state.workspace.send, principal, session["id"], prompt="Plan the change", request_id="plan-approval-1"))
    _task, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task["id"], "plan")
    assert state.workspace.session(principal, session["id"])["mode"] == "plan"
    return task["id"], approval["id"]


def test_graphql_plan_approval_moves_the_conversation_to_agent_mode(tmp_path, monkeypatch):
    state, client, principal, headers, session = _plan_turn(tmp_path, monkeypatch)
    with client:
        task_id, approval_id = _send(client, state, principal, session)
        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": approval_id, "input": {"decision": "approved"}}, headers)
        client.portal.call(wait_for_status, state.agent_store, task_id, "completed")
        assert state.workspace.session(principal, session["id"])["mode"] == "agent"
        assert state.agent_store.get_conversation(session["id"])["mode"] == "agent"


def test_rest_plan_approval_moves_the_conversation_to_agent_mode(tmp_path, monkeypatch):
    state, client, principal, headers, session = _plan_turn(tmp_path, monkeypatch)
    with client:
        task_id, approval_id = _send(client, state, principal, session)
        response = client.post(f"/api/workspace/tasks/{task_id}/approvals/{approval_id}/resolve", headers=headers, json={"decision": "approved"})
        assert response.status_code == 200, response.text
        client.portal.call(wait_for_status, state.agent_store, task_id, "completed")
        assert state.workspace.session(principal, session["id"])["mode"] == "agent"


@pytest.mark.parametrize("decision", ["denied", "revise"])
def test_other_plan_decisions_keep_the_conversation_in_plan_mode(tmp_path, monkeypatch, decision):
    state, client, principal, headers, session = _plan_turn(tmp_path, monkeypatch)
    with client:
        task_id, approval_id = _send(client, state, principal, session)
        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": approval_id, "input": {"decision": decision}}, headers)
        assert state.workspace.session(principal, session["id"])["mode"] == "plan"
