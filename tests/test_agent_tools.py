"""Focused tests for the typed Agent tool surface (Phase 1)."""
from __future__ import annotations

import asyncio
import json
import os
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


def test_context_engine_hides_sensitive_names(tmp_path):
    from termx.agent.context import ContextEngine

    (tmp_path / ".env").write_text("TOKEN=x\n")
    (tmp_path / "credentials.json").write_text("{}\n")
    (tmp_path / "id_rsa").write_text("key\n")
    (tmp_path / "visible.py").write_text("x\n")
    snapshot = asyncio.run(ContextEngine(str(tmp_path)).snapshot())
    assert ".env" not in snapshot["files"]
    assert not any(".env" in e or "credentials.json" in e or "id_rsa" in e for e in snapshot["tree"])
    assert not any(".env" in e or "credentials.json" in e or "id_rsa" in e for e in snapshot["recent"])


def test_context_engine_bounds_history(tmp_path):
    from termx.agent.context import ContextEngine

    engine = ContextEngine(str(tmp_path), {"max_history_events": 5})
    items = [{"role": "user", "content": "hello"}] + [
        {"type": "function_call", "name": "read_file", "call_id": f"c{i}"}
        for i in range(20)
    ]
    slim = engine.slim_history(items)
    assert len(slim) <= 6  # leading user item + <=5 newest


def test_context_engine_cumulative_output_budget(tmp_path):
    from termx.agent.context import ContextEngine

    engine = ContextEngine(str(tmp_path), {"max_tool_output_chars_per_turn": 1000})
    items = [
        {"type": "function_call_output", "call_id": "old", "output": "o" * 900},
        {"type": "function_call_output", "call_id": "new", "output": "n" * 900},
    ]
    slim = engine.slim_history(items)
    assert slim[1]["output"].startswith("n" * 500)  # newest keeps most budget
    assert "elided" in slim[0]["output"] or len(slim[0]["output"]) <= 200


def test_search_filters_sensitive(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("search_project").execute(
            _call("search_project", {"query": "TOKEN"}), make_ctx()
        )
    )
    assert outcome.result["ok"] is True
    assert outcome.result["matches"] == []
    assert outcome.result.get("filtered_sensitive") == 1


def test_git_diff_refuses_sensitive(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".env").write_text("A=1\n")
    (repo / "ok.txt").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / ".env").write_text("A=2\n")
    (repo / "ok.txt").write_text("y\n")

    files = ProjectFiles()
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
        project_id=files.register(str(repo))["id"],
    )
    registry = default_registry()
    refused = asyncio.run(
        registry.get("git_diff").execute(_call("git_diff", {"path": ".env"}), ctx)
    )
    assert refused.result["ok"] is False and refused.result["refused"] is True
    whole = asyncio.run(
        registry.get("git_diff").execute(_call("git_diff"), ctx)
    )
    assert whole.result["ok"] is True
    assert "A=2" not in whole.result["diff"]
    assert whole.result.get("filtered_sensitive") == 1


def test_apply_patch_requires_hunks(env):
    root, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(
            _call("apply_patch", {"patch": "--- a/a.py\n+++ b/a.py\n"}),
            make_ctx(),
        )
    )
    assert outcome.result["ok"] is False
    assert "no hunks" in outcome.result["output"]
    assert (root / "a.py").read_text() == "print('one')\nprint('two')\n"


def test_write_file_revision_sensitive(env):
    root, make_ctx, _ = env
    (root / ".env").write_text("A=1\n")
    outcome = asyncio.run(
        default_registry().get("write_file").execute(
            _call("write_file", {"path": ".env", "content": "A=2", "expected_revision": "0"}),
            make_ctx(),
        )
    )
    assert outcome.result["ok"] is False and outcome.result["refused"] is True
    assert (root / ".env").read_text() == "A=1\n"


def test_list_files_hides_sensitive_and_symlinks(env):
    root, make_ctx, _ = env
    (root / "credentials.json").write_text("{}\n")
    (root / "id_rsa").write_text("k\n")
    target = tmp_external = root.parent / "outside.txt"
    tmp_external.write_text("x\n")
    (root / "link_out").symlink_to(target)
    outcome = asyncio.run(
        default_registry().get("list_files").execute(_call("list_files", {}), make_ctx())
    )
    paths = {e["path"] for e in outcome.result["entries"]}
    assert "credentials.json" not in paths and "id_rsa" not in paths
    assert "link_out" not in paths
    assert "a.py" in paths


def test_git_fetch_and_stage_guards(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".env").write_text("A=1\n")
    (repo / "ok.txt").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    files = ProjectFiles()
    ctx = ToolContext(
        task_id="t",
        cwd=str(repo),
        task={"limits": {}, "mode": "agent"},
        read_only=True,
        cancel=asyncio.Event(),
        emit=lambda *_: None,
        store=None,
        manager=None,
        project_files=files,
        project_id=files.register(str(repo))["id"],
    )
    registry = default_registry()
    fetch = asyncio.run(registry.get("git_fetch").execute(_call("git_fetch"), ctx))
    assert fetch.result["ok"] is False and fetch.result["refused"] is True

    ctx_rw = ToolContext(
        task_id="t2", cwd=str(repo), task={"limits": {}, "mode": "agent"},
        read_only=False, cancel=asyncio.Event(), emit=lambda *_: None,
        store=None, manager=None, project_files=files,
        project_id=ctx.project_id,
    )
    stage = asyncio.run(
        registry.get("git_stage").execute(_call("git_stage", {"paths": [".env", "ok.txt"]}), ctx_rw)
    )
    assert stage.result["ok"] is False and stage.result["refused"] is True


def test_apply_patch_deletes_file(env):
    root, make_ctx, _ = env
    (root / "dead.txt").write_text("gone\n")
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(
            _call("apply_patch", {"patch": "--- a/dead.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"}),
            make_ctx(),
        )
    )
    assert outcome.result["ok"] is True, outcome.result
    assert not (root / "dead.txt").exists()


def test_apply_patch_preserves_trailing_newline(env):
    root, make_ctx, _ = env
    (root / "nonl.txt").write_text("first\nsecond")  # no trailing newline
    patch = "--- a/nonl.txt\n+++ b/nonl.txt\n@@ -1,2 +1,2 @@\n-first\n+FIRST\n second\n\\ No newline at end of file\n"
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(_call("apply_patch", {"patch": patch}), make_ctx())
    )
    assert outcome.result["ok"] is True, outcome.result
    assert (root / "nonl.txt").read_text() == "FIRST\nsecond"


def test_apply_patch_rollback_on_write_failure(env, monkeypatch):
    root, make_ctx, _ = env
    (root / "b.py").write_text("b1\n")
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-print('one')\n+print('ONE')\n print('two')\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b1\n+B1\n"
    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 4:  # 1st = a.py backup move, 2nd = a.py write, 3rd = b.py backup move, 4th = b.py write
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(_call("apply_patch", {"patch": patch}), make_ctx())
    )
    assert outcome.result["ok"] is False
    # a.py was already replaced — rollback must restore it.
    assert (root / "a.py").read_text() == "print('one')\nprint('two')\n"
    assert (root / "b.py").read_text() == "b1\n"


def test_stream_shell_reader_failure_terminates(tmp_path):
    from termx.agent.execution import stream_shell

    def boom(_data: bytes) -> None:
        raise RuntimeError("callback died")

    async def run():
        import time
        start = time.monotonic()
        try:
            await stream_shell("yes x", str(tmp_path), timeout_s=120.0, on_output=boom)
        except RuntimeError:
            pass
        assert time.monotonic() - start < 10.0  # process killed promptly, not timed out

    asyncio.run(run())


def test_provider_runtime_evict_retires_client():
    from termx.agent.runtime import ProviderHttpRuntime

    runtime = ProviderHttpRuntime()
    client = runtime.client_for("p", timeout_s=5.0, headers={})
    runtime.evict("p")  # no running loop — must not drop it unclosed
    assert not client.is_closed
    asyncio.run(runtime.aclose())
    assert client.is_closed


def test_slim_history_bounds_computer_screenshots(tmp_path):
    from termx.agent.context import ContextEngine

    engine = ContextEngine(str(tmp_path), {"max_images_in_context": 2})
    items = [
        {
            "type": "computer_call_output",
            "call_id": f"c{i}",
            "output": {"type": "computer_screenshot", "image_url": f"data:image/jpeg;base64,IMG{i}"},
        }
        for i in range(6)
    ]
    slim = engine.slim_history(items)
    urls = [i["output"]["image_url"] for i in slim]
    assert urls[-1].startswith("data:image/jpeg") and urls[-2].startswith("data:image/jpeg")
    assert all(u.startswith("data:image/png") for u in urls[:-2])


def test_stream_shell_bounds_output_memory(tmp_path):
    from termx.agent.execution import OUTPUT_LIMIT, stream_shell

    result = asyncio.run(
        stream_shell(
            "python3 -c 'import sys; sys.stdout.write(\"x\" * 3_000_000)'",
            str(tmp_path),
            timeout_s=60.0,
        )
    )
    assert result.truncated is True
    assert len(result.output) <= OUTPUT_LIMIT


def test_stream_shell_early_stdout_eof_not_timeout(tmp_path):
    from termx.agent.execution import stream_shell

    result = asyncio.run(stream_shell("exec 1>&-; sleep 0.2", str(tmp_path), timeout_s=10.0))
    assert result.timed_out is False
    assert result.exit_code == 0


def test_apply_patch_preserves_crlf(env):
    root, make_ctx, _ = env
    (root / "win.txt").write_bytes(b"first\r\nsecond\r\nthird\r\n")
    patch = "--- a/win.txt\n+++ b/win.txt\n@@ -1,3 +1,3 @@\n first\n-second\n+SECOND\n third\n"
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(_call("apply_patch", {"patch": patch}), make_ctx())
    )
    assert outcome.result["ok"] is True, outcome.result
    assert (root / "win.txt").read_bytes() == b"first\r\nSECOND\r\nthird\r\n"


def test_apply_patch_rejects_stale_ambiguous_hunk(env):
    root, make_ctx, _ = env
    lines = "".join(f"line{i}\n" for i in range(30)) + "enabled = false\n" + "".join(
        f"mid{i}\n" for i in range(5)
    ) + "enabled = false\n"
    (root / "dup.txt").write_text(lines)
    # Hunk claims line 31 area; both copies match within the window -> ambiguous? Only if
    # equal distance; force single wrong block: patch context targets line 31 block but
    # it no longer matches (context differs), nearest match is the duplicate.
    patch = (
        "--- a/dup.txt\n+++ b/dup.txt\n@@ -30,2 +30,2 @@\n line29\n-enabled = false\n+enabled = true\n"
    )
    # Make the stated context stale so only the *other* copy matches fully.
    (root / "dup.txt").write_text(lines.replace("line29\nenabled = false", "line29\nchanged"))
    outcome = asyncio.run(
        default_registry().get("apply_patch").execute(_call("apply_patch", {"patch": patch}), make_ctx())
    )
    assert outcome.result["ok"] is False
    assert "does not apply" in outcome.result["output"] or "ambiguous" in outcome.result["output"]
    assert (root / "dup.txt").read_text().count("enabled = true") == 0


def test_slim_history_drops_orphaned_computer_output(tmp_path):
    from termx.agent.context import ContextEngine

    engine = ContextEngine(str(tmp_path), {"max_history_events": 3})
    items = [
        {"role": "user", "content": "go"},
        {"type": "computer_call", "call_id": "c1"},
        {"type": "computer_call_output", "call_id": "c1", "output": {"image_url": "x"}},
        {"type": "function_call", "call_id": "f1", "name": "list_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "f1", "output": "ok"},
    ]
    slim = engine.slim_history(items)
    types = [i.get("type", i.get("role")) for i in slim]
    # cut keeps newest 3 after the head user item: [computer_call_output(orphan), fc, fco]
    # -> orphan dropped, call/output pair stays intact.
    assert "computer_call_output" not in types
    assert "function_call" in types and "function_call_output" in types


def test_snapshot_tree_skips_external_symlink(tmp_path):
    from termx.agent.context.engine import project_snapshot

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "real.txt").write_text("x\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external_secret_names.txt").write_text("x\n")
    (proj / "linkdir").symlink_to(outside, target_is_directory=True)
    snap = project_snapshot(str(proj))
    flat = " ".join(snap.get("tree", []))
    assert "external_secret_names" not in flat


def test_git_stage_directory_refuses_sensitive(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "ok.txt").write_text("x\n")
    (repo / ".env").write_text("A=1\n")  # untracked sensitive file

    files = ProjectFiles()
    ctx = ToolContext(
        task_id="t", cwd=str(repo), task={"limits": {}, "mode": "agent"},
        read_only=False, cancel=asyncio.Event(), emit=lambda *_: None,
        store=None, manager=None, project_files=files,
        project_id=files.register(str(repo))["id"],
    )
    outcome = asyncio.run(
        default_registry().get("git_stage").execute(_call("git_stage", {"paths": ["."]}), ctx)
    )
    assert outcome.result["ok"] is False and outcome.result["refused"] is True


def test_git_status_hides_sensitive_filenames(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".env").write_text("A=1\n")

    files = ProjectFiles()
    ctx = ToolContext(
        task_id="t", cwd=str(repo), task={"limits": {}, "mode": "agent"},
        read_only=False, cancel=asyncio.Event(), emit=lambda *_: None,
        store=None, manager=None, project_files=files,
        project_id=files.register(str(repo))["id"],
    )
    outcome = asyncio.run(default_registry().get("git_status").execute(_call("git_status"), ctx))
    paths = [f["path"] for f in outcome.result["files"]]
    assert ".env" not in paths and outcome.result.get("filtered_sensitive") == 1


def test_run_mutating_cancel_kills_process(tmp_path):
    from termx.agent.tools.git import _run_mutating

    ctx = ToolContext(
        task_id="t", cwd=str(tmp_path), task={"limits": {}, "mode": "agent"},
        read_only=False, cancel=asyncio.Event(), emit=lambda *_: None,
        store=None, manager=None, project_files=None, project_id="p",
    )

    # `git daemon` runs until killed: cancellation must terminate the process
    # group, not just abandon the waiting task.
    async def run():
        task = asyncio.create_task(
            _run_mutating(ctx, "daemon", "--listen=127.0.0.1", "--port=0", "--export-all")
        )
        await asyncio.sleep(0.3)
        ctx.cancel.set()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(run()) is True


def test_provider_runtime_evict_defers_close_with_loop(monkeypatch):
    import termx.agent.runtime as runtime_mod
    from termx.agent.runtime import ProviderHttpRuntime

    monkeypatch.setattr(runtime_mod, "_EVICT_GRACE_S", 0.05)

    async def run():
        runtime = ProviderHttpRuntime()
        client = runtime.client_for("p", timeout_s=5.0, headers={})
        runtime.evict("p")
        await asyncio.sleep(0.02)
        assert not client.is_closed
        await asyncio.sleep(0.2)
        assert client.is_closed
        await runtime.aclose()

    asyncio.run(run())


def test_run_check_ask_refusal(env):
    _, make_ctx, _ = env
    outcome = asyncio.run(
        default_registry().get("run_check").execute(
            _call("run_check", {"kind": "test", "command": "echo hi"}), make_ctx(read_only=True)
        )
    )
    assert outcome.result["ok"] is False and outcome.result["refused"] is True


def test_run_check_timeout_clamped(env):
    from termx.agent.tools.shell import _shell_timeout

    _, make_ctx, _ = env
    ctx = make_ctx()
    call = _call("run_check", {"kind": "test", "command": "x", "timeout_s": 600})
    assert _shell_timeout(call, ctx, 300.0) == 30.0  # task limit is 30 in env


def test_stream_redactor_splits_secret():
    from termx.agent.tools.shell import _StreamRedactor

    redactor = _StreamRedactor(hold_back=32)
    out = redactor.feed("api_ke")
    assert out == ""
    out = redactor.feed("y=TOPSECRET1234567890" + "x" * 64)
    assert "TOPSECRET" not in out
    tail = redactor.flush()
    joined = (out + tail)
    assert "TOPSECRET" not in joined


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
