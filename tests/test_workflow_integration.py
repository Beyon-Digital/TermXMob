"""Real workflow HTTP, QuickJS worker and AgentManager integration.

Only inference is deterministic. Authentication, authority, durable stores,
JavaScript subprocess, normal task approvals and scratch shell remain real.
Run on a host with an enforced workflow sandbox; capability failures skip the
runtime-specific cases and must be reported as a validation gap.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.app import AppState, create_app

USERNAME = "workflow-owner"
PASSWORD = "isolated-workflow-password-123"
PREFIX = "/api/workspace/workflows"
ORIGIN = {"Origin": "http://testserver"}
TERMINAL = {"completed", "failed", "cancelled", "needs_attention"}
PURE_SOURCE = """return await phase('Compute values', async () => {
  const values = [];
  for (let i = 0; i < args.count; i++) { if (i % 2 === 0) values.push(i); }
  const doubled = await parallel(values, async (value, index) => value * 2 + index);
  const total = await pipeline(doubled, [async value => value.reduce((a,b) => a+b, 0), async value => ({total:value})]);
  log('Computed values', {values:doubled});
  return {values:doubled, ...total, globals:[typeof process,typeof require,typeof fetch]};
});"""
INPUT_SCHEMA = {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": ["count"], "additionalProperties": False}
AGENT_SOURCE = """const child = await agent({name:'Verify scratch command', prompt:'Run the harmless scratch verification command', provider_id:'offline-workflow', model:'fixture-workflow'}); return child;"""
WORKFLOW_TOOL_PROMPT = "Run the inline workflow receipt fixture"


class OfflineWorkflowAdapter:
    """A scripted provider response, never a network model or task mock."""
    def __init__(self, calls):
        self.calls = calls
        self.turns = 0

    async def test(self, *args, **kwargs):
        raise AssertionError("Provider tests are forbidden in workflow QA")

    async def plan(self, prompt, cwd, manifest):
        self.calls.append({"operation": "plan", "cwd": cwd})
        return {"summary": "Verify the owned scratch command", "steps": ["Print the verification marker", "Report its output"], "tools": ["shell"], "risks": []}, "fixture-plan"

    async def turn(self, *, prompt, cwd, manifest, input_items=None, **kwargs):
        self.turns += 1
        self.calls.append({"operation": "turn", "cwd": cwd, "turn": self.turns})
        # Production recreates adapters when resuming an approved tool. The
        # fixture must behave like a stateless provider keyed by tool output.
        if not input_items or not any(item.get("type") == "function_call_output" for item in input_items):
            call = (ProviderCall("function", "workflow-receipt-call", "run_workflow", {"source": "return {receipt:'workflow-tool-ok'};", "name": "Agent-created workflow", "args": {}})
                    if prompt == WORKFLOW_TOOL_PROMPT else
                    ProviderCall("function", "workflow-safe-printf", "run_shell", {"command": "printf workflow-shell-ok", "purpose": "Verify the owned scratch command"}))
            return ProviderTurn(response_id="fixture-turn-1", text="I will print the verification marker.", calls=[call], usage={}, output_items=[{"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": json.dumps(call.arguments)}])
        assert input_items and any(item.get("type") == "function_call_output" for item in input_items)
        return ProviderTurn(response_id="fixture-turn-2", text="Verified workflow command.", calls=[], usage={}, output_items=[{"type": "message", "content": [{"type": "output_text", "text": "Verified workflow command."}]}])


def build_state(root: Path):
    """Environment must already point to ``root`` before constructing state."""
    calls = []
    state = AppState(passcode=None, credentials=CredentialStore({}), adapter_factory=lambda *_: OfflineWorkflowAdapter(calls))
    owner = state.identity.setup_owner(USERNAME, PASSWORD)
    folder = root / "owned-project"
    folder.mkdir()
    (folder / "source.txt").write_text("Owned synthetic workflow source remains unchanged.\n")
    project = state.projects.register(str(folder), name="Workflow verification folder")
    state.agent_store.put_provider("offline-workflow", kind="openai-compatible", name="Offline workflow fixture", base_url="http://127.0.0.1:1/v1", model="fixture-workflow", capabilities=["shell"], secret_configured=False)
    async def no_catalog(_):
        return {}
    state.agent.catalogue._fetch = no_catalog
    return state, owner, project, calls


@pytest.fixture
def workflow_host(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TERMX_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("TERMX_ENGINE_STARTUP_REFRESH", "0")
    state, owner, project, calls = build_state(tmp_path)
    with TestClient(create_app(state, web_dir=None)) as client:
        response = client.post("/auth/login", headers=ORIGIN, json={"method": "local-password", "username": USERNAME, "password": PASSWORD})
        assert response.status_code == 200, response.text
        headers = {**ORIGIN, "X-Termx-CSRF": response.json()["csrf_token"]}
        yield state, owner, project, calls, client, headers
    assert (Path(project["path"]) / "source.txt").read_text() == "Owned synthetic workflow source remains unchanged.\n"


def runtime_required(client):
    response = client.get(PREFIX + "/capabilities")
    assert response.status_code == 200, response.text
    capability = response.json()["runtime"]
    if not capability["available"]:
        pytest.skip("Actual workflow runtime unavailable: " + str(capability.get("reason")))
    assert capability["process_boundary"]["available"] and capability["no_host_io"]
    return capability


def post(client, headers, path, body, expected=200):
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def wait_run(client, run_id, statuses=TERMINAL, seconds=15, poll_interval=.025):
    deadline = monotonic() + seconds
    last = None
    while monotonic() < deadline:
        response = client.get(PREFIX + "/runs/" + run_id)
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in statuses:
            return last
        sleep(poll_interval)
    raise AssertionError("Workflow did not reach " + repr(statuses) + ": " + repr(last))


def test_authenticated_definition_crud_cas_schema_and_owner_boundary(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    body = {"request_id": "create-definition-001", "name": "Compute values", "source": PURE_SOURCE, "input_schema": INPUT_SCHEMA, "project_id": project["id"]}
    assert client.get(PREFIX, headers={"Cookie": ""}).status_code == 401
    assert client.post(PREFIX, headers=ORIGIN, json=body).status_code == 403
    assert client.post(PREFIX, headers={**ORIGIN, "X-Termx-CSRF": "wrong"}, json=body).status_code == 403
    definition = post(client, headers, PREFIX, body)
    assert post(client, headers, PREFIX, body)["id"] == definition["id"]
    assert client.post(PREFIX, headers=headers, json={**body, "name": "Changed request"}).status_code == 409
    path = PREFIX + "/" + definition["id"]
    changed = client.patch(path, headers=headers, json={"revision": 1, "changes": {"description": "Reviewed definition"}})
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert client.patch(path, headers=headers, json={"revision": 1, "changes": {"name": "Stale"}}).status_code == 409
    assert client.post(PREFIX + "/validate", headers=headers, json={"source": "return 1;", "input_schema": {"type": "object", "$ref": "https://example.invalid/schema"}}).status_code == 400
    from termx.authorization import ROLES
    other = state.identity.create_local_user("workflow-other", PASSWORD, list(ROLES["operator"]))
    state.authorization.set_role(other.id, "operator")
    login = client.post("/auth/login", json={"method": "local-password", "username": "workflow-other", "password": PASSWORD, "transport": "bearer"})
    assert login.status_code == 200
    foreign = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get(PREFIX, headers=foreign).json()["workflows"] == []
    assert client.get(path, headers=foreign).status_code == 404
    assert client.patch(path, headers=foreign, json={"revision": 2, "changes": {"name": "Foreign"}}).status_code == 404
    assert client.post(PREFIX + "/runs", headers=foreign, json={"request_id": "foreign-project-run", "source": "return 1;", "project_id": project["id"]}).status_code == 403
    assert client.post(PREFIX + "/runs", headers=headers, json={"request_id": "untrusted-actor-run", "source": "return 1;", "actor_task_id": "foreign"}).status_code == 422
    state.identity.set_scopes(other.id, ["files-read"])
    assert client.get(PREFIX, headers=foreign).status_code == 403
    assert calls == []


def test_actual_quickjs_http_validation_execution_snapshot_and_archive(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    validated = post(client, headers, PREFIX + "/validate", {"source": PURE_SOURCE, "input_schema": INPUT_SCHEMA})
    assert validated["valid"] is True
    invalid = post(client, headers, PREFIX + "/validate", {"source": "return (;"})
    assert invalid["valid"] is False
    definition = post(client, headers, PREFIX, {"request_id": "create-pure-001", "name": "Compute values", "source": PURE_SOURCE, "input_schema": INPUT_SCHEMA})
    launch = {"request_id": "run-pure-00001", "workflow_id": definition["id"], "revision": 1, "args": {"count": 6}}
    assert client.post(PREFIX + "/runs", headers=headers, json={**launch, "args": {"count": "bad"}}).status_code == 400
    assert client.get(PREFIX + "/runs").json()["runs"] == []
    run = post(client, headers, PREFIX + "/runs", launch)
    final = wait_run(client, run["id"])
    assert final["status"] == "completed", final
    assert final["result"] == {"values": [0, 5, 10], "total": 15, "globals": ["undefined"] * 3}
    assert [(call["operation"], call["status"]) for call in final["calls"]] == [("phase", "completed"), ("log", "completed")]
    assert final["snapshot"]["source"] == PURE_SOURCE and final["workflow_revision"] == 1
    from termx.authorization import ROLES
    other = state.identity.create_local_user("workflow-run-other", PASSWORD, list(ROLES["operator"]))
    state.authorization.set_role(other.id, "operator")
    foreign_login = client.post("/auth/login", json={"method": "local-password", "username": "workflow-run-other", "password": PASSWORD, "transport": "bearer"})
    assert foreign_login.status_code == 200
    foreign = {"Authorization": "Bearer " + foreign_login.json()["access_token"]}
    assert client.get(PREFIX + "/runs", headers=foreign).json()["runs"] == []
    assert client.get(PREFIX + "/runs/" + run["id"], headers=foreign).status_code == 404
    assert client.post(PREFIX + "/runs/" + run["id"] + "/cancel", headers=foreign, json={}).status_code == 404
    path = PREFIX + "/" + definition["id"]
    assert client.patch(path, headers=headers, json={"revision": 1, "changes": {"source": "return 9;", "archived": True}}).status_code == 200
    assert post(client, headers, PREFIX + "/runs", launch)["id"] == run["id"]
    assert client.post(PREFIX + "/runs", headers=headers, json={**launch, "args": {"count": 5}}).status_code == 409
    assert client.post(PREFIX + "/runs", headers=headers, json={**launch, "request_id": "archive-new-run", "revision": 2}).status_code == 400
    assert client.get(PREFIX).json()["workflows"] == []
    assert client.get(PREFIX + "/runs?workflow_id=" + definition["id"]).json()["runs"][0]["result"] == final["result"]
    assert calls == [] and state.agent_store.list_tasks() == []


def test_actual_worker_cancel_and_never_resolving_guest_are_bounded(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    run = post(client, headers, PREFIX + "/runs", {"request_id": "run-cancel-00001", "source": "await new Promise(() => {});", "limits": {"timeout_s": 5}})
    wait_run(client, run["id"], {"running"})
    started = monotonic()
    acknowledgement = post(client, headers, PREFIX + "/runs/" + run["id"] + "/cancel", {})
    assert acknowledgement["status"] in {"cancelling", "cancelled"}
    final = wait_run(client, run["id"])
    assert final["status"] == "cancelled" and monotonic() - started < 4
    started = monotonic()
    unresolved = post(client, headers, PREFIX + "/runs", {"request_id": "run-no-progress-001", "source": "await new Promise(() => {});", "limits": {"timeout_s": 5}})
    stopped = wait_run(client, unresolved["id"])
    assert stopped["status"] == "failed" and stopped["error"]["code"] == "no_progress", stopped
    assert monotonic() - started < 4
    assert calls == [] and state.agent_store.list_tasks() == []


@pytest.mark.parametrize("decision", ["approved", "denied", "cancel", "revoke", "scratch_approved"])
def test_actual_javascript_agent_uses_real_send_approval_and_cancellation(workflow_host, decision):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    approves = decision in {"approved", "scratch_approved"}
    boundary = {"project_id": project["id"]}
    if decision == "scratch_approved":
        parent = state.workspace.create_session(owner, title="Owned scratch parent", mode="agent", provider_id="offline-workflow", model="fixture-workflow")
        assert parent["scratch"] is True and parent["project_id"] is None
        boundary = {"parent_session_id": parent["id"]}
    run = post(client, headers, PREFIX + "/runs", {"request_id": "run-real-agent-001", "source": AGENT_SOURCE, **boundary, "limits": {"max_agents": 1, "concurrency": 1}})
    waiting = wait_run(client, run["id"], {"awaiting_approval"})
    child = next(call for call in waiting["calls"] if call["operation"] == "agent")
    task_id = child["task_id"]
    task = state.agent_store.get_task(task_id)
    assert task["status"] == "awaiting_approval"
    assert state.workspace.store.get("task", task_id)["conversation_id"] == child["session_id"]
    approval = next(a for a in state.agent_store.approvals(task_id) if a["status"] == "pending")
    assert approval["kind"] == "plan" and [item["operation"] for item in calls] == ["plan"]
    assert not any(e["type"] == "process.started" for e in state.agent_store.events(task_id))
    if decision == "cancel":
        post(client, headers, PREFIX + "/runs/" + run["id"] + "/cancel", {})
    elif decision == "revoke":
        state.identity.set_scopes(owner.id, [scope for scope in owner.scopes if scope != "agent-run"])
        sleep(.25)
    else:
        post(client, headers, "/api/workspace/tasks/" + task_id + "/approvals/" + approval["id"] + "/resolve", {"decision": "approved" if approves else decision})
    if approves:
        # The real sandbox may require a separate, exact tool/capability review.
        # Approve that one harmless call through the normal route as well.
        deadline = monotonic() + 10
        while monotonic() < deadline:
            pending = [item for item in state.agent_store.approvals(task_id) if item["status"] == "pending"]
            if pending or state.agent_store.get_task(task_id)["status"] in TERMINAL:
                break
            sleep(.025)
        if pending:
            assert len(pending) == 1 and pending[0]["kind"] == "tool", pending
            call = pending[0]["payload"].get("call", {})
            assert call.get("name") == "run_shell" and call.get("arguments", {}).get("command") == "printf workflow-shell-ok", pending
            post(client, headers, "/api/workspace/tasks/" + task_id + "/approvals/" + pending[0]["id"] + "/resolve", {"decision": "approved"})
    final = wait_run(client, run["id"])
    events = state.agent_store.events(task_id)
    if approves:
        assert final["status"] == "completed" and final["result"]["output"] == "Verified workflow command.", final
        assert any(e["type"] == "process.output" and "workflow-shell-ok" in e["payload"].get("chunk", "") for e in events)
        assert len(state.workspace.session(owner, child["session_id"])["turns"]) == 1
    else:
        assert final["status"] in {"failed", "cancelled"}, final
        assert not any(e["type"] == "process.started" for e in events)
        assert [item["operation"] for item in calls] == ["plan"]
    assert state.agent_store.get_task(task_id)["status"] in {"completed", "cancelled", "failed"}


def test_coding_agent_runs_actual_workflow_via_fixed_tool_and_normal_review(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    parent = state.workspace.create_session(owner, title="Workflow tool receipt", project_id=project["id"], mode="agent", provider_id="offline-workflow", model="fixture-workflow")
    task = post(client, headers, "/api/workspace/sessions/" + parent["id"] + "/turns", {"prompt": WORKFLOW_TOOL_PROMPT, "request_id": "workflow-tool-parent-001"})
    plan = next(a for a in state.agent_store.approvals(task["id"]) if a["status"] == "pending")
    assert plan["kind"] == "plan"
    post(client, headers, "/api/workspace/tasks/" + task["id"] + "/approvals/" + plan["id"] + "/resolve", {"decision": "approved"})
    deadline = monotonic() + 10
    pending = []
    while monotonic() < deadline:
        pending = [a for a in state.agent_store.approvals(task["id"]) if a["status"] == "pending"]
        if pending:
            break
        sleep(.025)
    assert len(pending) == 1 and pending[0]["kind"] == "tool", pending
    assert pending[0]["payload"]["call"]["name"] == "run_workflow"
    assert client.get(PREFIX + "/runs").json()["runs"] == []
    post(client, headers, "/api/workspace/tasks/" + task["id"] + "/approvals/" + pending[0]["id"] + "/resolve", {"decision": "approved"})
    deadline = monotonic() + 10
    while monotonic() < deadline:
        runs = client.get(PREFIX + "/runs").json()["runs"]
        if runs and state.agent_store.get_task(task["id"])["status"] == "completed":
            break
        sleep(.025)
    assert len(runs) == 1 and state.agent_store.get_task(task["id"])["status"] == "completed"
    final = wait_run(client, runs[0]["id"])
    assert final["status"] == "completed" and final["result"] == {"receipt": "workflow-tool-ok"}
    assert final["parent_session_id"] == parent["id"] and final["project_id"] == project["id"]
    assert not any(e["type"] == "process.started" for e in state.agent_store.events(task["id"]))


def test_unlimited_actual_http_run_exceeds_old_call_job_and_cpu_quotas(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    source = """let total=0;
for(let i=0;i<60000000;i++) total+=i%7;
for(let i=0;i<12000;i++) await Promise.resolve();
for(let i=0;i<600;i++) await log('item-'+i);
return {calls:600,jobs:12000,positive:total>0};"""
    definition = post(client, headers, PREFIX, {"request_id": "legacy-limits-create", "name": "Long workflow", "source": source, "limits": {"max_agents": 1, "concurrency": 1, "timeout_s": 1}})
    assert definition["limits"] == {"max_agents": None, "concurrency": None, "timeout_s": None}
    run = post(client, headers, PREFIX + "/runs", {"request_id": "unlimited-long-run", "workflow_id": definition["id"], "revision": definition["revision"]})
    # Each status read serializes the retained call/event history. Leave time
    # for the same ledger to record progress between these heavier reads.
    final = wait_run(client, run["id"], seconds=40, poll_interval=.2)
    assert final["status"] == "completed", final
    assert final["limits"] == {"max_agents": None, "concurrency": None, "timeout_s": None}
    assert final["result"] == {"calls": 600, "jobs": 12000, "positive": True}
    assert final["call_count"] == 600 and final["calls_truncated"] is True
    assert len(final["calls"]) == 512
    assert (final["calls"][0]["sequence"], final["calls"][-1]["sequence"]) == (89, 600)
    from termx.workspace.workflows.store import RUN
    stored = state.workspace.store.get(RUN, run["id"])
    assert len(state.workspace.workflows.ledger.calls(stored)) == 600
    assert final["events_truncated"] is True
    assert calls == [] and state.agent_store.list_tasks() == []


def test_actual_cpu_run_has_no_default_deadline_but_manual_timeout_and_cancel_work(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    run = post(client, headers, PREFIX + "/runs", {"request_id": "unlimited-cpu-cancel", "source": "while(true){}"})
    wait_run(client, run["id"], {"running"})
    sleep(.25)
    assert client.get(PREFIX + "/runs/" + run["id"]).json()["status"] == "running"
    started = monotonic()
    post(client, headers, PREFIX + "/runs/" + run["id"] + "/cancel", {})
    assert wait_run(client, run["id"])["status"] == "cancelled"
    assert monotonic() - started < 4
    manual = post(client, headers, PREFIX + "/runs", {"request_id": "explicit-cpu-timeout", "source": "while(true){}", "limits": {"timeout_s": 1}})
    final = wait_run(client, manual["id"])
    assert final["limits"] == {"max_agents": None, "concurrency": None, "timeout_s": 1}
    assert final["status"] == "failed" and final["error"]["code"] == "timeout", final
    assert calls == [] and state.agent_store.list_tasks() == []


@pytest.mark.parametrize("concurrency,expected_children", [(None, 6), (2, 2)])
def test_actual_agent_parallelism_is_unlimited_unless_manually_enabled(workflow_host, concurrency, expected_children):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    source = """return await parallel([0,1,2,3,4,5], async index => await agent({name:'Parallel '+index,prompt:'Wait for a normal reviewed plan',provider_id:'offline-workflow',model:'fixture-workflow'}));"""
    run = post(client, headers, PREFIX + "/runs", {"request_id": "parallel-quota-check", "source": source, "project_id": project["id"], "limits": {"concurrency": concurrency}})
    deadline = monotonic() + 15
    while monotonic() < deadline:
        tasks = state.agent_store.list_tasks()
        if len(tasks) == expected_children and all(task["status"] == "awaiting_approval" for task in tasks):
            break
        sleep(.025)
    assert len(tasks) == expected_children, tasks
    assert all(task["status"] == "awaiting_approval" for task in tasks)
    assert all(all(value is None for value in task["limits"].values()) for task in tasks)
    assert not any(event["type"] == "process.started" for task in tasks for event in state.agent_store.events(task["id"]))
    post(client, headers, PREFIX + "/runs/" + run["id"] + "/cancel", {})
    final = wait_run(client, run["id"])
    assert final["status"] == "cancelled", final
    assert all(state.agent_store.get_task(task["id"])["status"] == "cancelled" for task in tasks)
    assert len(state.agent_store.list_tasks()) == expected_children


def test_manual_agent_count_stops_a_second_real_child_after_exact_first_review(workflow_host):
    state, owner, project, calls, client, headers = workflow_host
    runtime_required(client)
    invocation = "await agent({prompt:'Run the harmless scratch verification command',provider_id:'offline-workflow',model:'fixture-workflow'})"
    run = post(client, headers, PREFIX + "/runs", {"request_id": "explicit-agent-count", "source": "const first=" + invocation + ";return " + invocation + ";", "project_id": project["id"], "limits": {"max_agents": 1}})
    waiting = wait_run(client, run["id"], {"awaiting_approval"})
    child = next(call for call in waiting["calls"] if call.get("task_id"))
    task_id = child["task_id"]
    plan = next(item for item in state.agent_store.approvals(task_id) if item["status"] == "pending")
    assert plan["kind"] == "plan"
    post(client, headers, "/api/workspace/tasks/" + task_id + "/approvals/" + plan["id"] + "/resolve", {"decision": "approved"})
    deadline = monotonic() + 10
    pending = []
    while monotonic() < deadline:
        pending = [item for item in state.agent_store.approvals(task_id) if item["status"] == "pending"]
        if pending or state.agent_store.get_task(task_id)["status"] == "completed":
            break
        sleep(.025)
    if pending:
        assert len(pending) == 1 and pending[0]["kind"] == "tool", pending
        call = pending[0]["payload"]["call"]
        assert call["name"] == "run_shell" and call["arguments"]["command"] == "printf workflow-shell-ok"
        post(client, headers, "/api/workspace/tasks/" + task_id + "/approvals/" + pending[0]["id"] + "/resolve", {"decision": "approved"})
    final = wait_run(client, run["id"])
    assert final["status"] == "failed" and final["error"]["code"] == "agent_limit", final
    assert len(state.agent_store.list_tasks()) == 1
    assert state.agent_store.get_task(task_id)["status"] == "completed"
    assert any(event["type"] == "process.output" and "workflow-shell-ok" in event["payload"].get("chunk", "") for event in state.agent_store.events(task_id))
