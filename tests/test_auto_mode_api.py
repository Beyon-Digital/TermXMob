"""Auto mode through GraphQL: createAgentTask(auto), AgentTask.auto, setTaskAuto."""
from __future__ import annotations

from pathlib import Path

import pytest

from _gql import data, err_status, gql
from test_agent import _git_repo, wait_for_pending_approval
from test_auto_mode import _shell
from test_plan_mode_api import HEADERS, _RESOLVE, _client

_CREATE = (
    "mutation($input: AgentTaskInput!) "
    "{ create_agent_task(input: $input) { id status auto } }"
)
_TASK = "query($id: String!) { agent_task(task_id: $id) { id auto } }"
_SET = (
    "mutation($id: String!, $input: AutoModeInput!) "
    "{ set_task_auto(task_id: $id, input: $input) { id auto } }"
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    return repo


def _input(repo: Path, **overrides):
    return {"prompt": "Clean up", "cwd": str(repo), "provider_id": "fake", **overrides}


def test_graphql_create_with_auto_exposes_state_and_set_task_auto_toggles(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [("", [_shell("c1", "rm -rf build")])])
    repo = _repo(tmp_path)
    with client:
        created = data(client, _CREATE, "create_agent_task", {"input": _input(
            repo, execution_mode="worktree",
            auto={"enabled": True, "ceilings": {"tool_calls": 7}},
        )}, HEADERS)
        assert created["auto"]["enabled"] is True
        assert created["auto"]["ceilings"]["tool_calls"] == 7
        assert created["auto"]["used"]["tool_calls"] == 0
        task_id = created["id"]
        # Plan approval first, then the model's rm -rf is auto-approved.
        _task, plan = client.portal.call(wait_for_pending_approval, state.agent_store, task_id, "plan")
        data(client, _RESOLVE, "resolve_agent_approval", {
            "task": task_id, "approval": plan["id"], "input": {"decision": "approved"},
        }, HEADERS)

        off = data(client, _SET, "set_task_auto", {"id": task_id, "input": {"enabled": False}}, HEADERS)
        assert off["auto"]["enabled"] is False
        assert off["auto"]["ceilings"]["tool_calls"] == 7
        again = data(client, _TASK, "agent_task", {"id": task_id}, HEADERS)
        assert again["auto"]["enabled"] is False


def test_graphql_auto_without_worktree_is_rejected(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    repo = _repo(tmp_path)
    with client:
        variables = {"input": _input(repo, auto={"enabled": True})}
        assert err_status(client, _CREATE, variables, HEADERS) == 400
        assert "worktree" in gql(client, _CREATE, variables, HEADERS).text

        plain = data(client, _CREATE, "create_agent_task", {"input": _input(repo)}, HEADERS)
        assert plain["auto"] is None
        variables = {"id": plain["id"], "input": {"enabled": True}}
        assert err_status(client, _SET, variables, HEADERS) == 400
        assert "worktree" in gql(client, _SET, variables, HEADERS).text


def test_graphql_set_task_auto_rejects_bad_ceilings(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    repo = _repo(tmp_path)
    with client:
        task = data(client, _CREATE, "create_agent_task", {"input": _input(repo, execution_mode="worktree")}, HEADERS)
        bad = {"id": task["id"], "input": {"enabled": True, "ceilings": {"seconds": 0}}}
        assert err_status(client, _SET, bad, HEADERS) == 400
        missing = {"id": "missing", "input": {"enabled": False}}
        assert err_status(client, _SET, missing, HEADERS) == 404
