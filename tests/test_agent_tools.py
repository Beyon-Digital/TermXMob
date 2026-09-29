"""Focused tests for the typed Agent tool surface (Phase 1)."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from termx.agent.metrics import TaskMetrics
from termx.agent.providers import ProviderCall
from termx.agent.runtime import ProviderHttpRuntime
from termx.agent.scheduler import CallScheduler
from termx.agent.store import AgentStore
from termx.agent.tools import ToolContext, default_registry
from termx.project_files import ProjectFiles


def _call(name: str, arguments: dict | None = None, *, call_id: str = "c1") -> ProviderCall:
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments or {})


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("print('one')\nprint('two')\n", encoding="utf-8")
    (root / "b.txt").write_text("hello world\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "c.py").write_text("x = 1\n", encoding="utf-8")

    files = ProjectFiles()
    project_id = files.register(str(root))["id"]
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    store.create_task(
        prompt="t",
        cwd=str(root),
        provider_id="p",
        model="m",
        limits={"shell_timeout_s": 30, "max_steps": 10, "max_seconds": 60},
    )
    task_id = next(iter(store.list_tasks()))["id"]
    events: list[tuple[str, dict]] = []
    task = store.get_task(task_id)

    def make_ctx(read_only: bool = False) -> ToolContext:
        return ToolContext(
            task_id=task_id,
            cwd=str(root),
            task=task,
            read_only=read_only,
            cancel=asyncio.Event(),
            emit=lambda event_type, payload: events.append((event_type, payload)),
            store=store,
            manager=None,
            project_files=files,
            project_id=project_id,
            metrics=TaskMetrics(),
        )

    return root, make_ctx, events


def test_read_file(env):
    _, make_ctx, _ = env
    registry = default_registry()
    outcome = asyncio.run(registry.get("read_file").execute(_call("read_file", {"path": "a.py"}), make_ctx()))
    assert outcome.result["ok"] is True
    assert outcome.result["content"] == "print('one')\nprint('two')\n"
    assert outcome.result["revision"]


def test_read_file_pagination_and_missing(env):
    _, make_ctx, _ = env
    registry = default_registry()
    outcome = asyncio.run(
        registry.get("read_file").execute(
            _call("read_file", {"path": "a.py", "offset": 2, "limit": 1}), make_ctx()
        )
    )
    assert outcome.result["content"] == "print('two')\n"
    missing = asyncio.run(
        registry.get("read_file").execute(_call("read_file", {"path": "nope.txt"}), make_ctx())
    )
    assert missing.result["ok"] is False


def test_read_file_refuses_sensitive(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("read_file").execute(_call("read_file", {"path": ".env"}), make_ctx())
    )
    assert outcome.result["ok"] is False
    assert outcome.result["refused"] is True


def test_list_files(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("list_files").execute(_call("list_files"), make_ctx())
    )
    paths = {entry["path"] for entry in outcome.result["entries"]}
    assert {"a.py", "b.txt", "sub/", "sub/c.py"} <= paths


def test_write_file_create_and_revision(env):
    root, make_ctx, _ = env
    registry = default_registry()
    created = asyncio.run(
        registry.get("write_file").execute(
            _call("write_file", {"path": "new.txt", "content": "fresh\n"}), make_ctx()
        )
    )
    assert created.result["ok"] is True and created.result["created"] is True
    assert (root / "new.txt").read_text() == "fresh\n"

    revision = created.result["revision"]
    updated = asyncio.run(
        registry.get("write_file").execute(
            _call(
                "write_file",
                {"path": "new.txt", "content": "fresh v2\n", "expected_revision": revision},
            ),
            make_ctx(),
        )
    )
    assert updated.result["ok"] is True
    conflict = asyncio.run(
        registry.get("write_file").execute(
            _call(
                "write_file",
                {"path": "new.txt", "content": "x\n", "expected_revision": revision},
            ),
            make_ctx(),
        )
    )
    assert conflict.result["ok"] is False and conflict.result["conflict"] is True


def test_write_file_refused_read_only_and_sensitive(env):
    _, make_ctx, _ = env
    registry = default_registry()
    outcome = asyncio.run(
        registry.get("write_file").execute(
            _call("write_file", {"path": "x.txt", "content": "y"}), make_ctx(read_only=True)
        )
    )
    assert outcome.result["ok"] is False and outcome.result["refused"] is True
    sensitive = asyncio.run(
        registry.get("write_file").execute(
            _call("write_file", {"path": "id_rsa", "content": "y"}), make_ctx()
        )
    )
    assert sensitive.result["ok"] is False and sensitive.result["refused"] is True


def test_apply_patch(env):
    root, make_ctx, _ = env
    registry = default_registry()
    patch = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        " print('one')\n"
        "-print('two')\n"
        "+print('TWO')\n"
        "--- /dev/null\n"
        "+++ b/added.txt\n"
        "@@ -0,0 +1 @@\n"
        "+brand new\n"
    )
    dry = asyncio.run(
        registry.get("apply_patch").execute(
            _call("apply_patch", {"patch": patch, "dry_run": True}), make_ctx()
        )
    )
    assert dry.result["ok"] is True and dry.result["dry_run"] is True
    assert (root / "added.txt").exists() is False

    applied = asyncio.run(
        registry.get("apply_patch").execute(_call("apply_patch", {"patch": patch}), make_ctx())
    )
    assert applied.result["ok"] is True
    assert (root / "a.py").read_text() == "print('one')\nprint('TWO')\n"
    assert (root / "added.txt").read_text() == "brand new\n"

    bad = asyncio.run(
        registry.get("apply_patch").execute(
            _call("apply_patch", {"patch": "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-TOKEN=secret\n+X=1\n"}),
            make_ctx(),
        )
    )
    assert bad.result["ok"] is False and bad.result["refused"] is True


def test_search_project(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("search_project").execute(
            _call("search_project", {"query": "print"}), make_ctx()
        )
    )
    assert outcome.result["ok"] is True
    assert {m["path"] for m in outcome.result["matches"]} == {"a.py"}


def test_run_check_streaming_events(env):
    _, make_ctx, events = env
    outcome = asyncio.run(
        default_registry().get("run_check").execute(
            _call("run_check", {"kind": "test", "command": "echo alpha && echo beta"}), make_ctx()
        )
    )
    assert outcome.result["status"] == "passed"
    assert outcome.result["summary"].strip().endswith("beta")
    names = [name for name, _ in events]
    assert "process.started" in names
    assert "process.exited" in names
    assert "process.output" in names


def test_run_check_failed_status(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("run_check").execute(
            _call("run_check", {"kind": "lint", "command": "exit 3"}), make_ctx()
        )
    )
    assert outcome.result["status"] == "failed" and outcome.result["exit_code"] == 3


def test_run_shell_ask_refusal(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("run_shell").execute(
            _call("run_shell", {"command": "rm -rf a.py", "purpose": "x"}), make_ctx(read_only=True)
        )
    )
    assert outcome.result["ok"] is False and outcome.result["refused"] is True


def test_scheduler_groups():
    registry = default_registry()
    scheduler = CallScheduler(registry)
    calls = [
        _call("read_file", {"path": "a"}, call_id="1"),
        _call("read_file", {"path": "b"}, call_id="2"),
        _call("run_shell", {"command": "ls", "purpose": "x"}, call_id="3"),
        _call("read_file", {"path": "c"}, call_id="4"),
        ProviderCall(type="computer", call_id="5"),
        _call("bogus_tool", {}, call_id="6"),
    ]
    ctx = type(
        "Ctx",
        (),
        {
            "cwd": "/tmp",
            "read_only": False,
            "manager": type(
                "Mgr",
                (),
                {"_decide_computer": staticmethod(lambda call: __import__('termx.agent.policy', fromlist=['PolicyDecision']).PolicyDecision(True, False, "ok", "ok"))},
            )(),
        },
    )()
    groups = scheduler.schedule(calls, ctx)
    shapes = [[entry.call.call_id for entry in group] for group in groups]
    assert shapes == [["1", "2"], ["3"], ["4"], ["5"], ["6"]]
    unknown = groups[-1][0]
    assert unknown.decision.approval_required and unknown.decision.reason == "Unknown tool"


def test_git_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    files = ProjectFiles()
    project_id = files.register(str(repo))["id"]
    ctx = ToolContext(
        task_id="t",
        cwd=str(repo),
        task={"limits": {}, "mode": "agent"},
        read_only=False,
        cancel=asyncio.Event(),
        emit=lambda *_: None,
        store=None,
        manager=None,
        project_files=files,
        project_id=project_id,
    )
    outcome = asyncio.run(
        default_registry().get("git_status").execute(_call("git_status"), ctx)
    )
    assert outcome.result["ok"] is True
    assert any(f["path"] == "f.txt" for f in outcome.result["files"])


def test_context_engine(tmp_path):
    from termx.agent.context import ContextEngine

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "a.py").write_text("x\n")
    engine = ContextEngine(str(tmp_path))
    snapshot = asyncio.run(engine.snapshot())
    assert snapshot["kind"] == "project_snapshot"
    assert "uv run pytest -q" in snapshot["commands"]
    assert "a.py" in snapshot["files"]
    big = {"type": "function_call_output", "call_id": "c", "output": "y" * 60000}
    slim = engine.slim_history([big])
    assert len(slim[0]["output"]) < 60000


def test_metrics():
    metrics = TaskMetrics()
    metrics.record_provider(120, {"input_tokens": 3, "output_tokens": 2})
    metrics.record_tool("read_file", 5)
    metrics.record_shell(40)
    metrics.record_screenshot(10)
    metrics.record_subagent()
    metrics.record_approval_wait(99)
    snap = metrics.snapshot()
    assert snap["provider_calls"] == 1 and snap["provider_ms"] == 120
    assert snap["input_tokens"] == 3 and snap["output_tokens"] == 2
    assert snap["tool_ms"]["read_file"] == 5 and snap["tool_calls"]["read_file"] == 1
    assert snap["shell_ms"] == 40 and snap["shell_calls"] == 1
    assert snap["screenshots"] == 1 and snap["screenshot_bytes"] == 10
    assert snap["subagents"] == 1 and snap["approval_wait_ms"] == 99
    restored = TaskMetrics(snap)
    assert restored.snapshot()["provider_calls"] == 1


def test_http_runtime():
    runtime = ProviderHttpRuntime()
    first = runtime.client_for("p", timeout_s=5, headers={"x": "1"})
    assert runtime.client_for("p", timeout_s=5, headers={"x": "1"}) is first
    runtime.evict("p")
    assert runtime.client_for("p", timeout_s=5, headers={"x": "1"}) is not first
    asyncio.run(runtime.aclose())


def test_provider_tools_read_only_filtering():
    registry = default_registry()
    agent_names = {tool["name"] for tool in registry.provider_tools(read_only=False)}
    ask_names = {tool["name"] for tool in registry.provider_tools(read_only=True)}
    assert {"read_file", "list_files", "search_project", "run_shell", "share_file"} <= ask_names
    assert {"write_file", "apply_patch", "git_stage", "spawn_subagent"} <= agent_names
    assert "write_file" not in ask_names and "spawn_subagent" not in ask_names
