"""Protocol tests use fake restricted processes, never a substitute JS sandbox."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from termx.sandbox.models import SandboxCapabilities
from termx.workspace.workflows.runtime import RuntimeFailure, WorkflowRuntime, normalize_limits


class FakeChild:
    def __init__(self, respond) -> None:
        self.respond = respond
        self.stdout = asyncio.StreamReader()
        self.stdin = self
        self.process = self
        self.returncode = None
        self.finished = asyncio.Event()
        self.terminated = False
        self.sent = []

    def write(self, data: bytes) -> None:
        frame = json.loads(data)
        self.sent.append(frame)
        self.respond(self, frame)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        if self.returncode is None:
            self.finish(exit_code=1)

    def emit(self, frame: dict) -> None:
        self.stdout.feed_data(json.dumps(frame).encode() + b"\n")

    def finish(self, frame: dict | None = None, exit_code: int = 0) -> None:
        if frame is not None:
            self.emit(frame)
        if self.returncode is None:
            self.returncode = exit_code
            self.stdout.feed_eof()
            self.finished.set()

    async def wait(self) -> int:
        await self.finished.wait()
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        self.finish(exit_code=-15)


class FakeRunner:
    def __init__(self, respond, *, isolated: bool = True) -> None:
        self.respond = respond
        self.isolated = isolated
        self.child = None
        self.spec = None

    def capabilities(self):
        return SandboxCapabilities(
            backend="fixture", profile="agent", strength="kernel" if self.isolated else "none",
            filesystem_isolation=self.isolated, network_control=self.isolated,
            process_tree_kill=True,
        )

    async def spawn(self, spec):
        self.spec = spec
        self.child = FakeChild(self.respond)
        return self.child


class AvailableRuntime(WorkflowRuntime):
    @staticmethod
    def _dependency():
        # This tests the host protocol; it does not claim a real engine exists.
        return True, "fixture"


def execute(runtime, *, source="return args;", args=None, limits=None, on_call=None, cancel=None):
    async def default_call(*_):
        raise AssertionError("unexpected host effect")

    return asyncio.run(runtime.run(source, args or {}, limits, on_call or default_call, cancel or asyncio.Event()))


def test_missing_dependency_is_explicit_and_never_spawns(monkeypatch):
    runner = FakeRunner(lambda *_: None)
    runtime = WorkflowRuntime(runner=runner)
    monkeypatch.setattr(runtime, "_dependency", lambda: (False, None))
    assert runtime.capabilities()["available"] is False
    assert runtime.capabilities()["no_host_io"] is True
    checked = asyncio.run(runtime.validate("return 1"))
    assert checked["unsupported"] is True
    assert checked["error"]["code"] == "runtime_unavailable"
    assert execute(runtime)["error"]["code"] == "runtime_unavailable"
    assert runner.child is None


def test_rejects_host_fallback_before_spawn():
    runner = FakeRunner(lambda *_: None, isolated=False)
    runtime = AvailableRuntime(runner=runner)
    assert runtime.capabilities()["available"] is False
    assert execute(runtime)["error"]["code"] == "sandbox_unavailable"
    assert runner.child is None


def test_success_uses_private_restricted_spawn_and_sanitized_environment():
    runner = FakeRunner(lambda child, _: child.finish({"type": "result", "result": {"ok": True}}))
    outcome = execute(AvailableRuntime(runner=runner))
    assert outcome == {"status": "completed", "result": {"ok": True}, "error": None}
    assert runner.child.terminated
    assert runner.spec.profile == "agent"
    assert runner.spec.network == "none"
    assert runner.spec.cwd == runner.spec.workspace_root
    assert not Path(runner.spec.workspace_root).exists()
    assert set(runner.spec.env) == {"PATH", "LANG"}
    assert "-I" in runner.spec.argv and "-B" in runner.spec.argv
    assert runner.spec.limits.cpu_s is None and runner.spec.limits.wall_s is None
    assert runner.child.sent[0]["limits"]["max_agents"] is None
    assert runner.child.sent[0]["limits"]["concurrency"] is None
    assert runner.child.sent[0]["limits"]["timeout_s"] is None


def test_unlimited_defaults_and_optional_positive_run_limits():
    limits = normalize_limits()
    assert all(limits[key] is None for key in ("max_agents", "concurrency", "timeout_s"))
    assert normalize_limits({"max_agents": None, "concurrency": None, "timeout_s": None}) == limits
    assert normalize_limits({"max_agents": 1000, "concurrency": 100, "timeout_s": 86400})["max_agents"] == 1000
    assert normalize_limits({"max_agents": 10 ** 1000})["max_agents"] == 10 ** 1000
    with pytest.raises(RuntimeFailure):
        normalize_limits({"timeout_s": 10 ** 1000})
    capability = AvailableRuntime(runner=FakeRunner(lambda *_: None)).capabilities()
    assert capability["limits"] == {"max_agents": None, "concurrency": None, "timeout_s": None}
    assert "heap_bytes" in capability["safety_constraints"]
    assert "heap_bytes" not in capability["limits"]


def test_default_agent_count_and_concurrency_are_not_capped():
    active = 0
    peak = 0
    replies = 0
    count = 70
    def respond(child, frame):
        nonlocal replies
        if frame["type"] == "start":
            for index in range(count):
                child.emit({"type": "call", "id": str(index), "operation": "agent", "arguments": {"prompt": "work"}})
        else:
            replies += 1
            if replies == count:
                child.finish({"type": "result", "result": count})
    async def call(*_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return {}
    result = execute(AvailableRuntime(runner=FakeRunner(respond)), on_call=call)
    assert result["status"] == "completed" and peak == count


def test_explicit_manual_agent_count_remains_enforced():
    def respond(child, frame):
        if frame["type"] == "start":
            for index in range(3):
                child.emit({"type": "call", "id": str(index), "operation": "agent", "arguments": {"prompt": "work"}})
    result = execute(AvailableRuntime(runner=FakeRunner(respond)), limits={"max_agents": 2})
    assert result["status"] == "failed" and result["error"]["code"] == "agent_limit"


@pytest.mark.parametrize("frame,code", [
    ({"type": "call", "id": "1", "operation": "shell", "arguments": {}}, "invalid_protocol"),
    ({"type": "call", "id": "1", "operation": "agent", "arguments": []}, "invalid_protocol"),
    ({"type": "unexpected"}, "invalid_protocol"),
])
def test_worker_cannot_dispatch_arbitrary_operations(frame, code):
    runner = FakeRunner(lambda child, _: child.emit(frame))
    result = execute(AvailableRuntime(runner=runner))
    assert result["error"]["code"] == code
    assert runner.child.terminated


def test_dead_worker_without_terminal_is_failure():
    runner = FakeRunner(lambda child, _: child.finish())
    result = execute(AvailableRuntime(runner=runner))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "worker_exited"


def test_result_with_failed_process_is_not_success():
    runner = FakeRunner(lambda child, _: child.finish({"type": "result", "result": 1}, exit_code=7))
    result = execute(AvailableRuntime(runner=runner))
    assert result["error"]["code"] == "worker_failed"


def test_duplicate_call_id_is_rejected():
    def respond(child, frame):
        if frame["type"] == "start":
            item = {"type": "call", "id": "1", "operation": "log", "arguments": {"message": "hello"}}
            child.emit(item)
            child.emit(item)

    runner = FakeRunner(respond)
    result = execute(AvailableRuntime(runner=runner))
    assert result["error"]["code"] == "invalid_protocol"


def test_callback_errors_reject_guest_promise_with_structured_error():
    def respond(child, frame):
        if frame["type"] == "start":
            child.emit({"type": "call", "id": "1", "operation": "agent", "arguments": {"prompt": "inspect"}})
        else:
            assert frame["ok"] is False
            child.finish({"type": "error", "error": frame["error"]}, exit_code=1)

    class Denied(Exception):
        code = "permission_denied"

    async def denied(*_):
        raise Denied("Access denied")

    runner = FakeRunner(respond)
    result = execute(AvailableRuntime(runner=runner), on_call=denied)
    assert result["error"] == {"code": "permission_denied", "message": "Access denied"}


def test_individually_cancelled_host_call_rejects_without_hanging():
    def respond(child, frame):
        if frame["type"] == "start":
            child.emit({"type": "call", "id": "1", "operation": "agent", "arguments": {"prompt": "inspect"}})
        else:
            child.finish({"type": "error", "error": frame["error"]}, exit_code=1)

    async def cancelled(*_):
        raise asyncio.CancelledError()

    result = execute(AvailableRuntime(runner=FakeRunner(respond)), on_call=cancelled)
    assert result["error"]["code"] == "host_call_cancelled"


def test_host_response_aggregate_has_no_lifetime_quota():
    responses = 0
    def respond(child, frame):
        nonlocal responses
        if frame["type"] == "start":
            for index in range(20):
                child.emit({"type": "call", "id": str(index), "operation": "agent", "arguments": {"prompt": "read"}})
        else:
            responses += 1
            if responses == 20:
                child.finish({"type": "result", "result": responses})

    async def call(*_):
        return "x" * 600

    result = execute(AvailableRuntime(runner=FakeRunner(respond)), limits={"output_bytes": 1024}, on_call=call)
    assert result["status"] == "completed" and result["result"] == 20


def test_packaged_host_uses_only_allowlisted_self_worker_and_same_sandbox(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    runner = FakeRunner(lambda child, _: child.finish({"type": "result", "result": 1}))
    runtime = AvailableRuntime(runner=runner)
    assert runtime.capabilities()["available"]
    assert execute(runtime)["result"] == 1
    assert runner.spec.argv == (sys.executable, "--runtime-module", "termx.workspace.workflows.worker")
    assert runner.spec.network == "none" and runner.spec.workspace_root == runner.spec.cwd
    assert set(runner.spec.env) == {"PATH", "LANG"}
    assert str(Path(sys.executable).resolve().parent) in runner.spec.read_only_roots


def test_backend_worker_entry_is_json_only_and_starts_before_cli(tmp_path):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    # Invalid protocol is rejected before importing QuickJS, so this exercises
    # the actual entry path even while dependency installation is blocked.
    result = subprocess.run([sys.executable, "-B", str(root / "desktop" / "backend_entry.py"),
        "--runtime-module", "termx.workspace.workflows.worker"], input=b'{"type":"unexpected"}\n',
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=tmp_path, capture_output=True, timeout=5)
    assert result.returncode == 1
    assert not result.stderr
    frames = result.stdout.splitlines()
    assert len(frames) == 1
    assert json.loads(frames[0])["error"]["code"] == "invalid_protocol"


def test_agent_concurrency_ceiling_and_json_results():
    active = 0
    peak = 0
    responses = 0

    def respond(child, frame):
        nonlocal responses
        if frame["type"] == "start":
            for index in range(4):
                child.emit({"type": "call", "id": str(index), "operation": "agent", "arguments": {"prompt": str(index)}})
        else:
            responses += 1
            if responses == 4:
                child.finish({"type": "result", "result": "finished"})

    async def call(_id, operation, arguments):
        nonlocal active, peak
        assert operation == "agent"
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return {"output": arguments["prompt"]}

    result = execute(AvailableRuntime(runner=FakeRunner(respond)), limits={"concurrency": 2}, on_call=call)
    assert result["status"] == "completed"
    assert peak == 2


def test_cancellation_awaits_owned_callback_and_worker_cleanup():
    async def scenario():
        cancel = asyncio.Event()
        started = asyncio.Event()
        stopped = asyncio.Event()

        def respond(child, frame):
            if frame["type"] == "start":
                child.emit({"type": "call", "id": "1", "operation": "agent", "arguments": {"prompt": "wait"}})

        async def call(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        runner = FakeRunner(respond)
        task = asyncio.create_task(AvailableRuntime(runner=runner).run("return 1", {}, None, call, cancel))
        await started.wait()
        cancel.set()
        outcome = await task
        assert outcome["status"] == "cancelled"
        assert stopped.is_set()
        assert runner.child.terminated and runner.child.returncode is not None

    asyncio.run(scenario())


def test_wall_time_limit_and_output_limit():
    runner = FakeRunner(lambda *_: None)
    outcome = execute(AvailableRuntime(runner=runner), limits={"timeout_s": 0.1})
    assert outcome["error"]["code"] == "timeout"
    assert runner.child.terminated
    runner = FakeRunner(lambda child, _: child.finish({"type": "result", "result": "x" * 2048}))
    outcome = execute(AvailableRuntime(runner=runner), limits={"output_bytes": 1024})
    assert outcome["error"]["code"] == "output_limit"


def test_validation_compiles_without_effect_dispatch():
    runner = FakeRunner(lambda child, _: child.finish({"type": "validated", "valid": True}))
    assert asyncio.run(AvailableRuntime(runner=runner).validate("return 1")) == {"valid": True}
    assert runner.child.sent[0]["mode"] == "validate"


@pytest.mark.parametrize("limits", [{"concurrency": 0}, {"timeout_s": float("nan")}, {"max_agents": True}, {"max_jobs": 1.5}, {"host_io": True}])
def test_invalid_limits_are_rejected(limits):
    with pytest.raises(RuntimeFailure):
        normalize_limits(limits)


def test_source_and_json_bounds_precede_dependency_probe(monkeypatch):
    runtime = WorkflowRuntime()
    monkeypatch.setattr(runtime, "capabilities", lambda: (_ for _ in ()).throw(AssertionError("unexpected dependency probe")))
    assert execute(runtime, source="x" * 65_537)["error"]["code"] == "source_limit"
    assert execute(runtime, args={"x": "x" * 65_537})["error"]["code"] == "argument_limit"
    assert execute(runtime, args={"x": float("nan")})["error"]["code"] == "invalid_json"


def _real_bridge(source: str, args=None, *, heap=8 << 20):
    """Exercise the real interpreter separately from OS/process containment."""
    quickjs = pytest.importorskip("quickjs", reason="approved QuickJS dependency is not installed")
    context = quickjs.Context()
    context.set_memory_limit(heap)
    context.set_max_stack_size(256 << 10)
    bridge = Path(__file__).parents[1] / "src/termx/workspace/workflows/bridge.js"
    context.eval("globalThis.__workflow_controller = " + bridge.read_text())
    compile_source = context.eval("__workflow_controller.compile")
    start = context.eval("__workflow_controller.start")
    settle = context.eval("__workflow_controller.settle")
    poll = context.eval("__workflow_controller.poll")
    context.eval("delete globalThis.__workflow_controller")
    compile_source(source, json.dumps(normalize_limits()))
    start(json.dumps(args or {}))
    return context, settle, poll


def _drive_real(source: str, args=None):
    context, settle, poll = _real_bridge(source, args)
    calls = []
    for _ in range(1000):
        while context.execute_pending_job():
            pass
        state = json.loads(poll())
        for call in state["requests"]:
            calls.append(call)
            value = {"output": call["arguments"].get("prompt"), "status": "completed"} if call["operation"] == "agent" else {}
            settle(json.dumps({"id": call["id"], "ok": True, "value": value}))
        if state.get("outcome"):
            return state["outcome"], calls
    raise AssertionError("real bridge did not complete")


def test_real_quickjs_dynamic_branches_parallel_pipeline_and_phase():
    result, calls = _drive_real("""
      return await phase('Inspect', async () => {
        const values = [];
        for (let i = 0; i < args.count; i++) if (i % 2 === 0) values.push(i);
        const found = await parallel(values, async i => await agent({ prompt: 'item-' + i }));
        await log('inspected', { count: found.length });
        return await pipeline(found, [async values => values.map(v => v.output), async values => values.join(',')]);
      });
    """, {"count": 5})
    assert result == {"type": "result", "result": "item-0,item-2,item-4"}
    assert [call["operation"] for call in calls] == ["phase", "agent", "agent", "agent", "log", "phase"]
    assert calls[0]["arguments"]["state"] == "start"
    assert calls[-1]["arguments"]["state"] == "end"
    assert calls[0]["arguments"]["phase_id"] == calls[-1]["arguments"]["phase_id"]


def test_real_quickjs_has_no_host_globals_and_deeply_readonly_args():
    result, _ = _drive_real("""
      let blocked = false;
      try { args.nested.value = 2; } catch (_) { blocked = true; }
      return {
        globals: [typeof process, typeof require, typeof fetch, typeof std, typeof os, typeof __workflow_controller],
        constructorEscape: ({}).constructor.constructor('return typeof process')(),
        frozen: Object.isFrozen(args) && Object.isFrozen(args.nested),
        blocked,
        value: args.nested.value
      };
    """, {"nested": {"value": 1}})
    assert result["result"] == {"globals": ["undefined"] * 6, "constructorEscape": "undefined", "frozen": True, "blocked": True, "value": 1}


def test_real_quickjs_cannot_load_os_module():
    result, _ = _drive_real("return await import('qjs:os');")
    assert result["type"] == "error"
    assert "module" in result["error"]["message"].lower()


def test_real_quickjs_heap_safety_constraint():
    quickjs = pytest.importorskip("quickjs")
    try:
        context, _settle, poll = _real_bridge("return 'x'.repeat(32 * 1024 * 1024);", heap=2 << 20)
        while context.execute_pending_job():
            pass
        outcome = json.loads(poll())["outcome"]
        assert outcome["type"] == "error"
        assert "memory" in outcome["error"]["message"].lower()
    except quickjs.JSException as error:
        assert "memory" in str(error).lower()


def _restricted_runtime():
    runtime = WorkflowRuntime()
    capability = runtime.capabilities()
    if not capability["available"]:
        pytest.skip(capability["reason"])
    return runtime


def test_real_restricted_runtime_dynamic_rpc_and_validation():
    runtime = _restricted_runtime()
    assert asyncio.run(runtime.validate("return args.value + 1;")) == {"valid": True}
    invalid = asyncio.run(runtime.validate("return (;"))
    assert invalid["valid"] is False and invalid["error"]["code"] == "invalid_source"
    calls = []

    async def call(call_id, operation, arguments):
        calls.append((call_id, operation, arguments))
        return {"output": arguments.get("prompt")} if operation == "agent" else {}

    result = execute(runtime, source="return await phase('Read', async () => await parallel(args.items, async item => (await agent({prompt:item})).output));", args={"items": ["a", "b"]}, on_call=call)
    assert result == {"status": "completed", "result": ["a", "b"], "error": None}
    assert [item[1] for item in calls] == ["phase", "agent", "agent", "phase"]


@pytest.mark.parametrize("source,limits,code", [
    ("while (true) {}", {"timeout_s": 0.05}, "timeout"),
    ("await new Promise(() => {});", None, "no_progress"),
    ("return 'x'.repeat(2000);", {"output_bytes": 1024}, "output_limit"),
])
def test_real_restricted_runtime_stops_limits_and_no_progress(source, limits, code):
    result = execute(_restricted_runtime(), source=source, limits=limits)
    assert result["status"] == "failed"
    assert result["error"]["code"] == code


def test_real_restricted_runtime_heap_limit_and_import_rejection():
    runtime = _restricted_runtime()
    result = execute(runtime, source="return 'x'.repeat(32 * 1024 * 1024);", limits={"heap_bytes": 2 << 20})
    assert result["status"] == "failed"
    assert "memory" in result["error"]["message"].lower()
    result = execute(runtime, source="return await import('qjs:os');")
    assert result["status"] == "failed"
    assert "module" in result["error"]["message"].lower()


def test_real_restricted_runtime_exceeds_former_call_job_and_cpu_quotas():
    runtime = _restricted_runtime()
    calls = 0
    async def call(*_):
        nonlocal calls
        calls += 1
        return {}
    result = execute(runtime, source="""
      let total = 0;
      for (let i = 0; i < 60000000; i++) total += i % 7;
      for (let i = 0; i < 12000; i++) await Promise.resolve();
      for (let i = 0; i < 600; i++) await log('item-' + i);
      return {calls: 600, positive: total > 0};
    """, on_call=call)
    assert result == {"status": "completed", "result": {"calls": 600, "positive": True}, "error": None}
    assert calls == 600


@pytest.mark.parametrize("source", ["while (true) {}", "await new Promise(() => { function spin() { Promise.resolve().then(spin); } spin(); });"])
def test_real_unlimited_guest_cpu_remains_explicitly_cancellable(source):
    async def scenario():
        cancel = asyncio.Event()
        task = asyncio.create_task(_restricted_runtime().run(source, {}, None, lambda *_: None, cancel))
        await asyncio.sleep(.15)
        assert not task.done()
        cancel.set()
        result = await asyncio.wait_for(task, 5)
        assert result["status"] == "cancelled"
    asyncio.run(scenario())


def test_real_restricted_runtime_cancellation_drains_owned_call():
    runtime = _restricted_runtime()

    async def scenario():
        cancel = asyncio.Event()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def call(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        task = asyncio.create_task(runtime.run("return await agent({prompt:'wait'});", {}, None, call, cancel))
        await asyncio.wait_for(started.wait(), 5)
        cancel.set()
        result = await asyncio.wait_for(task, 5)
        assert result["status"] == "cancelled"
        assert stopped.is_set()

    asyncio.run(scenario())


def test_real_process_boundary_denies_external_file_and_network():
    """Verify the OS boundary independently of absent JavaScript host bindings."""
    runtime = _restricted_runtime()
    from termx.sandbox.models import ResourceLimits, SpawnSpec

    async def scenario():
        with tempfile.TemporaryDirectory(prefix="termx-workflow-canary-") as private, tempfile.TemporaryDirectory(prefix="termx-workflow-guest-") as workspace:
            canary = Path(private) / "outside.txt"
            canary.write_text("test-only-canary")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                port = listener.getsockname()[1]
                script = """import json,socket,sys
try:
    open(sys.argv[1]).read()
    denied = False
except OSError:
    # Seatbelt answers EPERM; bwrap's private /tmp answers ENOENT. Either way
    # the file's contents are unreachable.
    denied = True
connection = socket.socket()
connection.settimeout(1)
try:
    network_denied = connection.connect_ex(('127.0.0.1', int(sys.argv[2]))) != 0
except PermissionError:
    network_denied = True
finally:
    connection.close()
print(json.dumps({'file_denied':denied,'network_denied':network_denied}))
"""
                runner = runtime._select_runner(workspace)
                child = await runner.spawn(SpawnSpec(
                    profile="agent", argv=(sys.executable, "-I", "-B", "-c", script, str(canary), str(port)),
                    cwd=workspace, workspace_root=workspace,
                    read_only_roots=tuple({sys.prefix, sys.base_prefix}),
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, network="none",
                    limits=ResourceLimits(cpu_s=5, wall_s=5), purpose="workflow-boundary-test",
                ))
                try:
                    output = await asyncio.wait_for(child.process.stdout.read(), 5)
                    assert await asyncio.wait_for(child.wait(), 5) == 0
                    assert json.loads(output) == {"file_denied": True, "network_denied": True}
                finally:
                    await child.terminate()

    asyncio.run(scenario())
