"""HTTP surface for policy v2: CRUD, scopes, approval remember=..., sandbox
capability reporting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status

from termx.app import AppState, create_app


@pytest.fixture
def client():
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


@pytest.fixture
def auth():
    return {"X-Termx-Passcode": "secret"}


def _rule_input(**overrides):
    body = {
        "effect": "allow",
        "scope_type": "project",
        "scope_id": "proj-1",
        "action_type": "tool",
        "tool": "run_shell",
        "fingerprint": "abc123",
        "display": "pnpm install",
    }
    body.update(overrides)
    return body


_RULE = "id scope_type scope_id effect matcher revoked_at"
_CREATE = (
    "mutation($input: PolicyRuleInput!) "
    "{ create_agent_policy(input: $input) { %s } }" % _RULE
)
_PATCH = (
    "mutation($id: String!, $input: PolicyRulePatchInput!) "
    "{ patch_agent_policy(rule_id: $id, input: $input) { %s } }" % _RULE
)
_REVOKE = 'mutation($id: String!) { revoke_agent_policy(rule_id: $id) { ok } }'
_LIST = (
    "query($scope_type: String, $include_revoked: Boolean!) "
    "{ agent_policies(scope_type: $scope_type, include_revoked: $include_revoked) "
    "{ %s } }" % _RULE
)
_EFFECTIVE = (
    "query($project_id: String, $custom_agent_id: String) "
    "{ effective_agent_policies(project_id: $project_id, custom_agent_id: $custom_agent_id) "
    "{ approval_mode sandbox_profile sandbox_capabilities rules { %s } } }" % _RULE
)
_AGENT_FIELDS = "id name approval_mode sandbox_profile"
_CREATE_AGENT = (
    "mutation($input: CustomAgentInput!) "
    "{ create_custom_agent(input: $input) { %s } }" % _AGENT_FIELDS
)
_PATCH_AGENT = (
    "mutation($id: String!, $input: CustomAgentPatchInput!) "
    "{ patch_custom_agent(agent_id: $id, input: $input) { %s } }" % _AGENT_FIELDS
)


def test_policy_crud_round_trip(client, auth):
    rule = data(client, _CREATE, "create_agent_policy", {"input": _rule_input()}, auth)
    assert rule["scope_type"] == "project"
    assert rule["revoked_at"] is None

    listed = data(
        client, _LIST, "agent_policies",
        {"scope_type": "project", "include_revoked": False}, auth,
    )
    assert [r["id"] for r in listed] == [rule["id"]]

    patched = data(
        client, _PATCH, "patch_agent_policy",
        {"id": rule["id"], "input": {"effect": "deny", "display": "never"}}, auth,
    )
    assert patched["effect"] == "deny"

    data(client, _REVOKE, "revoke_agent_policy", {"id": rule["id"]}, auth)
    assert err_status(client, _REVOKE, {"id": rule["id"]}, auth) == 404


def test_policy_create_validation(client, auth):
    # Validation now runs in the store layer (ValueError -> 400); the REST-era
    # 422s came from pydantic patterns on the body models.
    assert (
        err_status(client, _CREATE, {"input": _rule_input(effect="maybe")}, auth)
        == 400
    )
    assert (
        err_status(client, _CREATE, {"input": _rule_input(scope_id=None)}, auth)
        == 400
    )
    assert (
        err_status(
            client, _PATCH,
            {"id": "whatever", "input": {"effect": "yolo"}}, auth,
        )
        == 400
    )


def test_policy_list_filters_revoked(client, auth):
    rule = data(client, _CREATE, "create_agent_policy", {"input": _rule_input()}, auth)
    data(client, _REVOKE, "revoke_agent_policy", {"id": rule["id"]}, auth)
    visible = data(
        client, _LIST, "agent_policies",
        {"scope_type": None, "include_revoked": False}, auth,
    )
    assert not visible
    with_revoked = data(
        client, _LIST, "agent_policies",
        {"scope_type": None, "include_revoked": True}, auth,
    )
    assert len(with_revoked) == 1
    assert with_revoked[0]["revoked_at"] is not None


def test_policies_require_scopes(client):
    """Mutations need agent-control; reads need agent-view."""
    assert err_status(client, _LIST, {"scope_type": None, "include_revoked": False}) == 401
    assert err_status(client, _CREATE, {"input": _rule_input()}) == 401


def test_effective_policies_reports_truthful_sandbox(client, auth):
    data(
        client, _CREATE, "create_agent_policy",
        {"input": _rule_input(scope_type="host", scope_id=None)}, auth,
    )
    agent = data(
        client, _CREATE_AGENT, "create_custom_agent",
        {"input": {
            "name": "bot", "approval_mode": "autonomous",
            "sandbox_profile": "workspace",
        }},
        auth,
    )
    body = data(
        client, _EFFECTIVE, "effective_agent_policies",
        {"project_id": None, "custom_agent_id": agent["id"]}, auth,
    )
    assert body["approval_mode"] == "autonomous"
    assert body["sandbox_profile"] == "workspace"
    # The endpoint reports the selected backend's real envelope — host's
    # unrestricted "*:any" or linux-ns's restricted set — never invented.
    from termx.sandbox import runner_for

    expected = sorted(runner_for("workspace").capabilities().granted)
    assert sorted(body["sandbox_capabilities"]) == expected
    assert len(body["rules"]) == 1  # only the host-scope rule matches


def test_effective_policies_hides_expired_rules(client, auth):
    """Expired rules stay visible in the ordinary list for audit but must not
    appear in the effective view — matching already ignores them."""
    import time

    rule = data(
        client, _CREATE, "create_agent_policy",
        {"input": _rule_input(expires_at=time.time() - 5)}, auth,
    )
    listed = data(
        client, _LIST, "agent_policies",
        {"scope_type": None, "include_revoked": False}, auth,
    )
    assert any(r["id"] == rule["id"] for r in listed)  # audit keeps it
    eff = data(
        client, _EFFECTIVE, "effective_agent_policies",
        {"project_id": "proj-1", "custom_agent_id": None}, auth,
    )
    assert not [r for r in eff["rules"] if r["id"] == rule["id"]]


def test_custom_agent_approval_mode_validation(client, auth):
    # approval_mode flows to the agent-file validator now (ValueError -> 400).
    assert (
        err_status(client, _CREATE_AGENT, {"input": {"name": "x", "approval_mode": "yolo"}}, auth)
        in (400, 422)
    )
    # Patching a missing agent surfaces the not-found check first.
    assert (
        err_status(
            client, _PATCH_AGENT,
            {"id": "missing", "input": {"sandbox_profile": "root"}}, auth,
        )
        in (400, 404)
    )

    agent = data(client, _CREATE_AGENT, "create_custom_agent", {"input": {"name": "bot"}}, auth)
    assert agent["approval_mode"] == "standard"
    assert agent["sandbox_profile"] == "agent"
    patched = data(
        client, _PATCH_AGENT, "patch_custom_agent",
        {"id": agent["id"], "input": {"approval_mode": "remember"}}, auth,
    )
    assert patched["approval_mode"] == "remember"


def test_approval_remember_field_validated(client, auth):
    mutation = (
        "mutation($input: AgentApprovalInput!) { "
        "resolve_agent_approval(task_id: \"nope\", approval_id: \"a1\", input: $input) }"
    )
    # Invalid enums are rejected before any task lookup; valid ones reach the
    # store and surface the missing approval as 404.
    assert err_status(
        client, mutation,
        {"input": {"decision": "approved", "remember": "everything"}}, auth,
    ) == 422
    assert err_status(
        client, mutation,
        {"input": {"decision": "shrug"}}, auth,
    ) == 422
    assert err_status(
        client, mutation,
        {"input": {"decision": "approved", "remember": "project"}}, auth,
    ) == 404


def test_machine_snapshot_reports_execution_sandbox(client, auth):
    snap = data(client, "{ machine { capabilities } }", "machine", headers=auth)
    caps = snap["capabilities"]
    assert caps["remembered_approvals"] is True
    assert caps["policy_engine_v2"] is True
    sandbox = caps["execution_sandbox"]
    # Truthful report: the snapshot mirrors whatever the runner selected for
    # this machine — host ("none") where no restricted backend probes OK,
    # linux-ns on linux, macos-seatbelt/helper on darwin, windows-token/user
    # on win32 — and never invents controls.
    import sys

    from termx.sandbox import (
        linux_ns_available,
        macos_backend_available,
        runner_for,
    )

    if sys.platform == "darwin" and macos_backend_available():
        from termx.sandbox.macos_runner import macos_helper_available

        expected_backend = "macos-helper" if macos_helper_available() else "macos-seatbelt"
    elif sys.platform == "win32":
        from termx.sandbox.windows_runner import windows_backend_available

        expected_backend = (
            runner_for("agent", backend="windows").capabilities().backend
            if windows_backend_available()
            else "host"
        )
    elif linux_ns_available():
        expected_backend = "linux-ns"
    else:
        expected_backend = "host"
    assert sandbox["backend"] == expected_backend
    agent_caps = runner_for("agent").capabilities()
    assert sandbox["strength"] == agent_caps.strength
    assert sandbox["network_control"] == agent_caps.network_control
    assert sandbox["filesystem_isolation"] == agent_caps.filesystem_isolation
    assert sandbox["identity_isolation"] == agent_caps.identity_isolation
    assert sandbox["resource_limits"] == agent_caps.resource_limits
    assert set(sandbox["profiles"]) == {"host", "workspace", "agent"}
    assert sandbox["profiles"]["host"]["backend"] == "host"
