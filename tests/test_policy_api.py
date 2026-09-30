"""HTTP surface for policy v2: CRUD, scopes, approval remember=..., sandbox
capability reporting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from termx.app import AppState, create_app


@pytest.fixture
def client():
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


@pytest.fixture
def auth():
    return {"X-Termx-Passcode": "secret"}


def _rule_body(**overrides):
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


def test_policy_crud_round_trip(client, auth):
    created = client.post("/api/agent/policies", json=_rule_body(), headers=auth)
    assert created.status_code == 200, created.text
    rule = created.json()["rule"]
    assert rule["scope_type"] == "project"
    assert rule["revoked"] is False

    listed = client.get("/api/agent/policies?scope_type=project", headers=auth)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["rules"]] == [rule["id"]]

    patched = client.patch(
        f"/api/agent/policies/{rule['id']}",
        json={"effect": "deny", "display": "never"},
        headers=auth,
    )
    assert patched.status_code == 200
    assert patched.json()["rule"]["effect"] == "deny"

    deleted = client.delete(f"/api/agent/policies/{rule['id']}", headers=auth)
    assert deleted.status_code == 200
    again = client.delete(f"/api/agent/policies/{rule['id']}", headers=auth)
    assert again.status_code == 404


def test_policy_create_validation(client, auth):
    bad = client.post(
        "/api/agent/policies",
        json=_rule_body(effect="maybe"),
        headers=auth,
    )
    assert bad.status_code == 422
    missing_scope = client.post(
        "/api/agent/policies",
        json=_rule_body(scope_id=None),
        headers=auth,
    )
    assert missing_scope.status_code == 400
    bad_patch = client.patch(
        "/api/agent/policies/whatever",
        json={"effect": "yolo"},
        headers=auth,
    )
    assert bad_patch.status_code == 422


def test_policy_list_filters_revoked(client, auth):
    rule = client.post("/api/agent/policies", json=_rule_body(), headers=auth).json()["rule"]
    client.delete(f"/api/agent/policies/{rule['id']}", headers=auth)
    visible = client.get("/api/agent/policies", headers=auth)
    assert not visible.json()["rules"]
    with_revoked = client.get("/api/agent/policies?include_revoked=true", headers=auth)
    assert len(with_revoked.json()["rules"]) == 1
    assert with_revoked.json()["rules"][0]["revoked"] is True


def test_policies_require_scopes(client):
    """Mutations need agent-control; reads need agent-view."""
    assert client.get("/api/agent/policies").status_code == 401
    assert client.post("/api/agent/policies", json=_rule_body()).status_code == 401


def test_effective_policies_reports_truthful_sandbox(client, auth):
    client.post("/api/agent/policies", json=_rule_body(scope_type="host", scope_id=None), headers=auth)
    agent = client.post(
        "/api/custom-agents",
        json={"name": "bot", "approval_mode": "autonomous", "sandbox_profile": "workspace"},
        headers=auth,
    ).json()["agent"]
    eff = client.get(
        f"/api/agent/policies/effective?custom_agent_id={agent['id']}",
        headers=auth,
    )
    assert eff.status_code == 200
    body = eff.json()
    assert body["approval_mode"] == "autonomous"
    assert body["sandbox_profile"] == "workspace"
    # Host backend truthfully reports the unrestricted envelope — never
    # claimed isolation that doesn't exist.
    assert body["sandbox_capabilities"] == ["*:any"]
    assert len(body["rules"]) == 1  # only the host-scope rule matches


def test_effective_policies_hides_expired_rules(client, auth):
    """Expired rules stay visible in the ordinary list for audit but must not
    appear in the effective view — matching already ignores them."""
    import time

    rule = client.post(
        "/api/agent/policies",
        json=_rule_body(expires_at=time.time() - 5),
        headers=auth,
    ).json()["rule"]
    listed = client.get("/api/agent/policies", headers=auth)
    assert any(r["id"] == rule["id"] for r in listed.json()["rules"])  # audit keeps it
    eff = client.get("/api/agent/policies/effective?project_id=proj-1", headers=auth)
    assert not [r for r in eff.json()["rules"] if r["id"] == rule["id"]]


def test_custom_agent_approval_mode_validation(client, auth):
    bad = client.post(
        "/api/custom-agents",
        json={"name": "x", "approval_mode": "yolo"},
        headers=auth,
    )
    assert bad.status_code == 422
    bad_patch = client.patch(
        "/api/custom-agents/missing",
        json={"sandbox_profile": "root"},
        headers=auth,
    )
    assert bad_patch.status_code == 422

    agent = client.post(
        "/api/custom-agents",
        json={"name": "bot"},
        headers=auth,
    ).json()["agent"]
    assert agent["approval_mode"] == "standard"
    assert agent["sandbox_profile"] == "agent"
    patched = client.patch(
        f"/api/custom-agents/{agent['id']}",
        json={"approval_mode": "remember"},
        headers=auth,
    )
    assert patched.status_code == 200
    assert patched.json()["agent"]["approval_mode"] == "remember"


def test_approval_body_remember_field_validated():
    from termx.app import AgentApprovalBody

    AgentApprovalBody(decision="approved")
    AgentApprovalBody(decision="approved", remember="project")
    with pytest.raises(Exception):
        AgentApprovalBody(decision="approved", remember="everything")


def test_machine_snapshot_reports_execution_sandbox(client, auth):
    snap = client.get("/api/machine", headers=auth)
    assert snap.status_code == 200
    caps = snap.json()["capabilities"]
    assert caps["remembered_approvals"] is True
    assert caps["policy_engine_v2"] is True
    sandbox = caps["execution_sandbox"]
    # Truthful PR A state: host backend only, no kernel isolation claimed.
    assert sandbox["backend"] == "host"
    assert sandbox["strength"] == "none"
    assert sandbox["network_control"] is False
    assert sandbox["filesystem_isolation"] is False
    assert sandbox["identity_isolation"] is False
    assert sandbox["resource_limits"] is False
