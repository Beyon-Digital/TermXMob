"""Workflow registry, bound authority and real native review without inference."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from pathlib import Path
import os
import sys

import pytest

from termx.agent.providers import ProviderCall
from termx.agent.tools import ToolContext, default_registry
from termx.agent.tools.workflows import LIMITS, READ_TOOLS, WORKFLOW_TOOLS
from termx.browser.service import BrowserService
from termx.engines.claude import ClaudeEngine
from termx.engines.codex import CodexEngine
from termx.engines.types import EffectiveRunConfiguration, EngineSessionBinding
from termx.workspace.workflow_tools import WorkflowToolService


def fixture(tmp_path):
    actor = SimpleNamespace(id="owner", policy_version=1, scopes=frozenset({"agent-view", "agent-control", "agent-run"}), authority_session_id="managed-session")
    session_actor = [actor]
    live = [True]
    broker = BrowserService(tmp_path / "broker", session_valid=lambda *_: live[0])
    broker.records.put("agent-task-authority", "task", {"id": "task", "principal_id": "owner",
        "session_id": "managed-session", "conversation_id": "conversation", "project_id": "project", "policy_version": 1})
    rows = {
        ("task", "task"): {"id": "task", "owner": "owner", "conversation_id": "conversation", "project_id": "project", "cwd": str(tmp_path)},
        ("conversation", "conversation"): {"id": "conversation", "owner": "owner", "project_id": "project", "cwd": str(tmp_path), "mode": "agent"},
    }
    tasks = {"task": {"id": "task", "mode": "agent", "status": "running", "cwd": str(tmp_path), "limits": {}}}
    effects = []
    definitions = {"saved": {"id": "saved", "revision": 1, "project_id": "project", "source": "return args;"}}
    class Workflows:
        def list(self, actor, **kwargs): return list(definitions.values())
        def get(self, actor, identifier): return definitions[identifier]
        def create(self, actor, **kwargs):
            effects.append(("save", kwargs))
            row = {"id": "new", "revision": 1, **kwargs}
            definitions[row["id"]] = row
            return row
        def update(self, actor, identifier, **kwargs):
            effects.append(("update", kwargs))
            return {**definitions[identifier], "revision": 2, **kwargs["changes"]}
        async def validate(self, actor, **kwargs): return {"valid": True}
        async def start(self, actor, **kwargs):
            effects.append(("run", kwargs))
            return {"id": "run", "status": "queued", "project_id": kwargs["project_id"]}
        def get_run(self, actor, identifier): return {"id": identifier, "project_id": "project", "status": "running"}
        async def cancel(self, actor, identifier): return {"id": identifier, "status": "cancelling"}
    cancel = asyncio.Event()
    agents = SimpleNamespace(get_task=tasks.get, task_agent=lambda _: None)
    def record(principal, kind, identifier, **kwargs):
        row = rows[(kind, identifier)]
        if row["owner"] != principal.id: raise PermissionError("Wrong owner")
        if kwargs.get("scope", "agent-view") not in principal.scopes: raise PermissionError("Managed session scope denied")
        return row
    workspace = SimpleNamespace(store=SimpleNamespace(get=lambda kind, identifier: rows.get((kind, identifier))),
        agents=agents, record=record, workflows=Workflows(), validate_task_execution=lambda _: None)
    manager = SimpleNamespace(_cancel={"task": cancel})
    workspace.state = SimpleNamespace(browser=broker, identity=SimpleNamespace(principal_by_id=lambda identifier: actor if identifier == actor.id else None,
        session_by_id=lambda identifier: SimpleNamespace(principal=session_actor[0]) if identifier == "managed-session" and live[0] else None), agent=manager)
    manager._tool_context = lambda task_id, task: ToolContext(task_id, str(tmp_path), task,
        task.get("mode") == "ask", cancel, lambda *_: None, None, manager)
    host = WorkflowToolService(workspace)
    manager.workflow_tools = host
    return SimpleNamespace(host=host, workspace=workspace, broker=broker, rows=rows, tasks=tasks,
        manager=manager, cancel=cancel, effects=effects, definitions=definitions, live=live, session_actor=session_actor)


def call(tool_name, **arguments):
    return ProviderCall("function", "fixture-call", tool_name, arguments)


def test_registry_fixed_tools_policy_and_read_only_scope(tmp_path):
    env = fixture(tmp_path)
    registry = default_registry()
    assert set(WORKFLOW_TOOLS).issubset(registry.names())
    schemas = registry.provider_tools(read_only=True)
    assert {item["name"] for item in schemas} & set(WORKFLOW_TOOLS) == READ_TOOLS
    assert not ({item["name"] for item in registry.provider_tools(allow_subagents=False)} & set(WORKFLOW_TOOLS))
    ctx = env.manager._tool_context("task", env.tasks["task"])
    for name in ("save_workflow", "run_workflow"):
        decision = registry.get(name).decide(call(name), ctx)
        assert decision.approval_required and not decision.allowed
    for name in WORKFLOW_TOOLS:
        assert not ({"owner", "cwd", "project_id", "parent_session_id", "managed_session_id", "profile"} & set(registry.get(name).parameters["properties"]))


def test_native_claude_does_not_turn_task_steps_into_sdk_quota(tmp_path):
    engine = ClaudeEngine()
    options = engine._options(EffectiveRunConfiguration(cwd=str(tmp_path),
        tools={"limits": {"max_steps": 1, "max_seconds": 1}}), EngineSessionBinding.new("claude", "native"))
    assert options.max_turns is None


def test_workflow_tool_manual_limit_settings_are_optional_without_ceilings():
    for name in ("max_agents", "concurrency", "timeout_s"):
        setting = LIMITS["properties"][name]
        assert setting["type"] == ["integer", "null"]
        assert "maximum" not in setting and "default" not in setting


def test_internal_manager_dispatch_waits_for_workflow_tool_approval(tmp_path):
    from test_agent import ScriptedAdapter, _fn, build_manager, wait_for_pending_approval, wait_for_status
    async def run():
        adapter = ScriptedAdapter([[_fn("launch", "run_workflow", workflow_id="saved", revision=1, args={})]])
        manager, store = build_manager(tmp_path, adapter)
        effects = []
        class Host:
            async def command(self, task_id, tool_call, **kwargs):
                effects.append((task_id, tool_call.arguments))
                return {"ok": True, "run_id": "durable-receipt", "status": "queued"}
        manager.workflow_tools = Host()
        try:
            task = await manager.create_task(prompt="Run the saved workflow", cwd=str(tmp_path), provider_id="fake")
            await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
            _, approval = await wait_for_pending_approval(store, task["id"], "tool")
            assert not effects and approval["payload"]["call"]["name"] == "run_workflow"
            await manager.resolve_approval(task["id"], approval["id"], "approved")
            completed = await wait_for_status(store, task["id"], "completed")
            assert len(effects) == 1
            receipt = [event for event in completed["events"] if event["type"] == "tool.finished"][-1]["payload"]["result"]
            assert receipt["status"] == "queued" and receipt["run_id"] == "durable-receipt"
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_saved_definition_immediately_runs_and_receipt_is_asynchronous(tmp_path):
    async def run():
        env = fixture(tmp_path)
        created = await default_registry().get("save_workflow").execute(call("save_workflow", name="Dynamic", source="return args;"), env.manager._tool_context("task", env.tasks["task"]))
        assert created.result["workflow_id"] == "new" and created.result["revision"] == 1
        result = await env.host.command("task", call("run_workflow", workflow_id="new", revision=1, args={"value": 1}))
        assert result["ok"] and result["run_id"] == "run" and result["status"] == "queued"
        params = env.effects[-1][1]
        assert params["actor_task_id"] == "task" and params["managed_session_id"] == "managed-session"
        assert params["project_id"] == "project" and params["parent_session_id"] == "conversation"
        assert "complete" not in result
        assert not any(name.startswith("workflow_new") for name in default_registry().names())
    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["cross_owner", "cross_project", "browser", "runner", "read_only", "cancelled", "revoked", "recursive", "enrolling_child", "ancestor", "preset", "model_scope"])
def test_bound_tools_refuse_scope_escalation_without_effect(tmp_path, invalid):
    async def run():
        env = fixture(tmp_path)
        args = {"workflow_id": "saved", "revision": 1, "args": {}}
        if invalid == "cross_owner": env.rows[("task", "task")]["owner"] = "other"
        if invalid == "cross_project": env.definitions["saved"]["project_id"] = "other"
        if invalid == "browser": env.rows[("conversation", "conversation")]["workflow"] = "browser"
        if invalid == "runner": env.rows[("conversation", "conversation")]["runner_id"] = "cloud"
        if invalid == "read_only": env.tasks["task"]["mode"] = "ask"
        if invalid == "cancelled": env.cancel.set()
        if invalid == "revoked": env.live[0] = False
        if invalid == "recursive": env.rows[("task", "task")]["workflow_run_id"] = "ancestor-run"
        if invalid == "enrolling_child": env.rows[("workflow_child_session", "conversation")] = {"workflow_run_id": "ancestor-run"}
        if invalid == "ancestor":
            env.tasks["task"]["parent_id"] = "ancestor"
            env.tasks["ancestor"] = {"id": "ancestor"}
            env.rows[("task", "ancestor")] = {"workflow_run_id": "ancestor-run"}
        if invalid == "preset": env.workspace.agents.task_agent = lambda _: {"tools": ["read_file"]}
        if invalid == "model_scope": args["managed_session_id"] = "other-session"
        result = await env.host.command("task", call("run_workflow", **args))
        assert result["refused"] and not env.effects
    asyncio.run(run())


@pytest.mark.parametrize("engine_type", [CodexEngine, ClaudeEngine])
@pytest.mark.parametrize("decision", ["approve", "deny", "revoke", "cancel"])
def test_native_effect_requires_exact_review_and_live_authority(tmp_path, engine_type, decision):
    async def run():
        env = fixture(tmp_path)
        pending = asyncio.Event()
        approvals = []
        async def sink(*args):
            approvals.append(args)
            pending.set()
            return "approval"
        engine = engine_type(approval_sink=sink)
        engine.browser_service = env.broker
        engine.workflow_tools = env.host
        binding = EngineSessionBinding.new(engine.id, "native", cwd=str(tmp_path), conversation_id="conversation",
            extensions_snapshot={"managed_task_id": "task", "workflow_tool_names": list(WORKFLOW_TOOLS)})
        effect = asyncio.create_task(env.host.native_execute(engine, binding, "save_workflow", {"name": "Exact", "source": "return args;"}, "exact-call"))
        await asyncio.wait_for(pending.wait(), 3)
        assert not env.effects
        assert approvals[0][2] == "browser.review"
        review = approvals[0][3]["browser_review"]
        assert review["status"] == "needs_user" and review["effect"] == "unknown"
        if decision == "revoke": env.live[0] = False
        if decision == "cancel": env.cancel.set()
        await engine.respond_approval(binding, approvals[0][1], "deny" if decision == "deny" else "approve")
        result = await asyncio.wait_for(effect, 3)
        assert bool(env.effects) == (decision == "approve")
        assert result.get("ok") == (decision == "approve")
        assert not engine._pending_decisions
    asyncio.run(run())


def test_native_policy_denial_and_read_do_not_create_review(tmp_path, monkeypatch):
    async def run():
        from termx.agent.policy import PolicyDecision
        env = fixture(tmp_path)
        engine = CodexEngine()
        engine.browser_service = env.broker
        binding = EngineSessionBinding.new("codex", "native", cwd=str(tmp_path), conversation_id="conversation",
            extensions_snapshot={"managed_task_id": "task", "workflow_tool_names": list(WORKFLOW_TOOLS)})
        read = await env.host.native_execute(engine, binding, "get_workflow", {"workflow_id": "saved"}, "read-call")
        assert read["ok"] and not env.broker.records.list("review")
        registry = default_registry()
        spec = registry.get("save_workflow")
        from dataclasses import replace
        registry.register(replace(spec, decide=lambda *_: PolicyDecision(False, False, "Policy denies", "Denied")))
        try:
            denied = await env.host.native_execute(engine, binding, "save_workflow", {"name": "No", "source": "return 1;"}, "denied-call")
            assert denied["refused"] and not env.effects and not env.broker.records.list("review")
        finally: registry.register(spec)
    asyncio.run(run())


def test_native_policy_changed_during_human_review_cannot_execute(tmp_path):
    from dataclasses import replace
    from termx.agent.policy import PolicyDecision
    async def run():
        env = fixture(tmp_path)
        approvals = []
        pending = asyncio.Event()
        async def sink(*args):
            approvals.append(args)
            pending.set()
            return "approval"
        engine = CodexEngine(approval_sink=sink)
        engine.browser_service = env.broker
        binding = EngineSessionBinding.new("codex", "native", cwd=str(tmp_path), conversation_id="conversation",
            extensions_snapshot={"managed_task_id": "task", "workflow_tool_names": list(WORKFLOW_TOOLS)})
        registry = default_registry()
        original = registry.get("save_workflow")
        effect = asyncio.create_task(env.host.native_execute(engine, binding, "save_workflow", {"name": "Exact", "source": "return 1;"}, "policy-change"))
        try:
            await asyncio.wait_for(pending.wait(), 3)
            registry.register(replace(original, decide=lambda *_: PolicyDecision(False, False, "New deny", "Denied")))
            await engine.respond_approval(binding, approvals[0][1], "approve")
            result = await asyncio.wait_for(effect, 3)
            assert result["refused"] and not env.effects
        finally:
            registry.register(original)
            if not effect.done():
                effect.cancel()
                await asyncio.gather(effect, return_exceptions=True)
    asyncio.run(run())


def test_codex_dynamic_tools_and_reply_contract_without_mcp(tmp_path):
    async def run():
        env = fixture(tmp_path)
        requests = []
        class Connection:
            async def request(self, method, params, **kwargs):
                requests.append((method, params))
                return {"thread": {"id": "native"}}
        engine = CodexEngine()
        engine.workflow_tools = env.host
        engine.browser_service = env.broker
        engine._conn = Connection()
        async def ready(): pass
        engine._ensure_conn = ready
        binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), tools={"managed_task_id": "task"}))
        assert engine.capabilities().mcp_native == "unsupported"
        assert {tool["name"] for tool in requests[0][1]["dynamicTools"]} == set(WORKFLOW_TOOLS)
        assert all(tool["type"] == "function" and "inputSchema" in tool for tool in requests[0][1]["dynamicTools"])
        binding.native_turn_id = "turn"
        response = await engine._on_server_request("item/tool/call", {"threadId": "native", "turnId": "turn",
            "callId": "call", "tool": "get_workflow", "arguments": {"workflow_id": "saved"}})
        assert response["success"]
        assert response["contentItems"][0]["type"] == "inputText"
        assert json.loads(response["contentItems"][0]["text"])["workflow"]["id"] == "saved"
        for overrides in ({"turnId": "stale"}, {"namespace": "other"}, {"tool": "run_shell"}, {"threadId": "other"}):
            denied = await engine._on_server_request("item/tool/call", {"threadId": "native", "turnId": "turn", "callId": "call",
                "tool": "get_workflow", "arguments": {"workflow_id": "saved"}, **overrides})
            assert not denied["success"]
        legacy = EngineSessionBinding.new("codex", "legacy", status="idle")
        await engine.attach(legacy)
        assert "dynamicTools" not in requests[-1][1] and not legacy.extensions_snapshot.get("workflow_tool_names")
    asyncio.run(run())


def test_claude_real_mcp_protocol_scoped_tools_and_error_shape(tmp_path):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    async def run():
        env = fixture(tmp_path)
        engine = ClaudeEngine()
        engine.workflow_tools = env.host
        engine.browser_service = env.broker
        binding = EngineSessionBinding.new("claude", "native", cwd=str(tmp_path))
        options = engine._options(EffectiveRunConfiguration(cwd=str(tmp_path), tools={"managed_task_id": "task"}), binding)
        assert "termx-workflows" in options.mcp_servers
        sdk = options.mcp_servers["termx-workflows"]["instance"]
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as group:
                group.start_soon(sdk.run, *server_streams, sdk.create_initialization_options())
                async with ClientSession(*client_streams) as client:
                    await client.initialize()
                    assert {tool.name for tool in (await client.list_tools()).tools} == set(WORKFLOW_TOOLS)
                    read = await client.call_tool("get_workflow", {"workflow_id": "saved"})
                    assert not read.is_error and json.loads(read.content[0].text)["ok"]
                    env.live[0] = False
                    denied = await client.call_tool("get_workflow", {"workflow_id": "saved"})
                    assert denied.is_error and not json.loads(denied.content[0].text)["ok"]
                    assert "managed-session" not in denied.content[0].text
                group.cancel_scope.cancel()
    asyncio.run(run())


def test_workflow_child_native_delegation_controls_apply_before_first_turn(tmp_path):
    async def run():
        from claude_agent_sdk import PermissionResultDeny
        env = fixture(tmp_path)
        env.rows[("workflow_child_session", "conversation")] = {"run_id": "workflow-run"}
        assert env.host.is_workflow_child("task")
        with pytest.raises(PermissionError): env.host.validate_delegation("task")
        requests = []
        class Connection:
            async def request(self, method, params, **kwargs):
                requests.append((method, params))
                return {"thread": {"id": "native"}}
        codex = CodexEngine()
        codex.workflow_tools = env.host
        codex._conn = Connection()
        async def ready(): pass
        codex._ensure_conn = ready
        cfg = EffectiveRunConfiguration(cwd=str(tmp_path), tools={"managed_task_id": "task", "denied": ["WebFetch"]})
        child = await codex.create_session(cfg)
        assert requests[-1][1]["config"] == {"features.multi_agent": False, "features.multi_agent_v2": False,
            "features.multi_agent_v2_dynamic_tools": False, "agents.enabled": False}
        assert "dynamicTools" not in requests[-1][1]
        assert child.extensions_snapshot["workflow_child_delegation_disabled"]
        claude = ClaudeEngine()
        claude.workflow_tools = env.host
        binding = EngineSessionBinding.new("claude", "native", cwd=str(tmp_path))
        options = claude._options(cfg, binding)
        assert set(options.disallowed_tools) == {"WebFetch", "Agent", "Task", "Workflow"}
        assert "termx-workflows" not in options.mcp_servers
        for name in ("Agent", "Task", "Workflow"):
            assert isinstance(await options.can_use_tool(name, {}, None), PermissionResultDeny)
        env.rows.pop(("workflow_child_session", "conversation"))
        ordinary = await codex.create_session(cfg)
        assert "config" not in requests[-1][1] and not ordinary.extensions_snapshot["workflow_child_delegation_disabled"]
        ordinary_options = claude._options(cfg, EngineSessionBinding.new("claude", "ordinary"))
        assert ordinary_options.disallowed_tools == ["WebFetch"]
    asyncio.run(run())


def test_workflow_transport_errors_do_not_expose_credentials(tmp_path):
    async def run():
        env = fixture(tmp_path)
        def fail(*_): raise RuntimeError("Authorization: Bearer do-not-expose-workflow-secret")
        env.workspace.workflows.get = fail
        refused = await env.host.command("task", call("get_workflow", workflow_id="saved"))
        assert refused["refused"] and "do-not-expose" not in json.dumps(refused)
    asyncio.run(run())


def test_explicit_empty_preset_has_no_workflow_tools(tmp_path):
    async def run():
        env = fixture(tmp_path)
        env.workspace.agents.task_agent = lambda _: {"tools": [], "file": {"tools_mode": "explicit", "tools": []}}
        assert not env.host.schemas("task", allowed=[], tools_mode="explicit")
        result = await env.host.command("task", call("get_workflow", workflow_id="saved"))
        assert result["refused"] and not env.effects
    asyncio.run(run())


def test_workflow_tools_use_narrowed_managed_session_principal(tmp_path):
    async def run():
        env = fixture(tmp_path)
        # The account retains run authority, while this device session has a
        # read-only grant. Reloading the account would silently widen it.
        env.session_actor[0] = SimpleNamespace(id="owner", policy_version=1, scopes=frozenset({"agent-view"}), authority_session_id="managed-session")
        assert "agent-run" in env.workspace.state.identity.principal_by_id("owner").scopes
        assert (await env.host.command("task", call("get_workflow", workflow_id="saved")))["ok"]
        assert (await env.host.command("task", call("run_workflow", workflow_id="saved", revision=1, args={})))["refused"]
        assert not env.effects
    asyncio.run(run())


def test_global_definition_can_be_read_and_run_in_bound_parent_context(tmp_path):
    async def run():
        env = fixture(tmp_path)
        env.definitions["saved"]["project_id"] = None
        assert (await env.host.command("task", call("get_workflow", workflow_id="saved")))["ok"]
        assert (await env.host.command("task", call("run_workflow", workflow_id="saved", revision=1, args={})))["ok"]
        assert env.effects[-1][1]["parent_session_id"] == "conversation"
        assert env.effects[-1][1]["project_id"] is None
        assert (await env.host.command("task", call("save_workflow", workflow_id="saved", revision=1, name="No edit across global boundary")))["refused"]
    asyncio.run(run())


def test_tool_listing_and_run_output_are_bounded(tmp_path):
    async def run():
        env = fixture(tmp_path)
        env.definitions.update({str(index): {"id": str(index), "revision": 1, "name": "Workflow", "source": "large source " * 4000, "project_id": "project"} for index in range(120)})
        page = await env.host.command("task", call("list_workflows", limit=10, offset=10))
        assert len(page["workflows"]) == 10 and page["next_offset"] == 20
        assert all("source" not in row for row in page["workflows"])
        env.workspace.workflows.get_run = lambda *_: {"id": "run", "status": "completed", "project_id": "project", "result": "x" * (300 * 1024), "calls": [{"id": "child", "status": "completed", "task_id": "task-child"}]}
        result = await env.host.command("task", call("get_workflow_run", run_id="run"))
        assert result["truncated"] and result["run"]["status"] == "completed"
        assert result["run"]["calls"][0]["task_id"] == "task-child"
        assert len(json.dumps(result)) < 256 * 1024
    asyncio.run(run())


@pytest.mark.parametrize("eof", [False, True])
def test_codex_real_stdio_dynamic_workflow_dispatch_and_eof(tmp_path, monkeypatch, eof):
    from termx.engines.jsonrpc import JsonlProcess
    async def run():
        env = fixture(tmp_path)
        executable = Path(__file__).parent / "fixtures" / "fake_workflow_codex.py"
        def process(_argv, **kwargs):
            return JsonlProcess([sys.executable, "-u", str(executable)], **kwargs)
        monkeypatch.setattr("termx.engines.codex.JsonlProcess", process)
        pending = asyncio.Event()
        completed = asyncio.Event()
        async def sink(*args):
            pending.set()
            return "approval"
        def events(_binding_id, event):
            if event.type == "engine.turn.completed": completed.set()
        engine = CodexEngine(approval_sink=sink, event_sink=events,
            spawn_env={"PATH": os.environ.get("PATH", ""), "WORKFLOW_FIXTURE_SAVE": "1" if eof else "0"})
        engine.workflow_tools = env.host
        engine.browser_service = env.broker
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), tools={"managed_task_id": "task"}))
            binding.conversation_id = "conversation"
            await engine.send(binding, "Synthetic workflow protocol request")
            if eof:
                await asyncio.wait_for(pending.wait(), 3)
                assert not env.effects
                await engine._conn.notify("fixture/exit", {})
                await asyncio.wait_for(engine._conn._closed.wait(), 3)
                await asyncio.sleep(0)
                assert binding.status == "lost" and not env.effects
                assert not engine._pending_decisions
                assert not completed.is_set()
            else:
                await asyncio.wait_for(completed.wait(), 3)
                assert binding.status == "idle" and not pending.is_set()
        finally:
            await engine.shutdown()
    asyncio.run(run())
