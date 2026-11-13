from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager, ExitStack
import functools
import json
from pathlib import Path
import sys
from time import monotonic, perf_counter, process_time
from unittest.mock import patch

import pytest
import httpx
from fastapi.testclient import TestClient

from _gql import data, err_status
import test_desktop as desktop_fixtures
from test_agent import FakeAdapter, build_manager, wait_for_status
from termx.agent.limits import resolve_limits
from termx.agent.providers import OpenAIResponsesAdapter, ProviderCall, ProviderTurn
from termx.app import AppState, create_app
from termx.config import ConfigStore


configuration_desktop_io = pytest.fixture(name="configuration_desktop_io", autouse=False)(
    desktop_fixtures.desktop_io.__wrapped__)


class ManyReads(FakeAdapter):
    def __init__(self, count=30):
        super().__init__()
        self.count = count
        self.seen = []
        self.started_at = perf_counter()
        self.turn_times = deque(maxlen=8)

    async def turn(self, **kwargs):
        history = kwargs.get("input_items") or []
        self.seen.append(history)
        self.turns += 1
        self.turn_times.append({"turn": self.turns, "seconds": perf_counter() - self.started_at})
        if self.turns > self.count:
            return ProviderTurn("done", "verified", [], {})
        call = ProviderCall(type="function", call_id=f"read-{self.turns}",
                            name="read_file", arguments={"path": "sample.txt"})
        return ProviderTurn(f"response-{self.turns}", "", [call], {}, [
            {"type": "function_call", "call_id": call.call_id,
             "name": "read_file", "arguments": '{"path":"sample.txt"}'}])


def _code_positions(worker):
    def frames(frame):
        rows = []
        while frame is not None and len(rows) < 12:
            rows.append({"file": Path(frame.f_code.co_filename).name,
                         "function": frame.f_code.co_name, "line": frame.f_lineno})
            frame = frame.f_back
        return rows

    def chain(coroutine):
        rows = []
        for _ in range(24):
            if coroutine is None:
                break
            frame = getattr(coroutine, "cr_frame", None) or getattr(coroutine, "gi_frame", None)
            if frame is not None:
                rows.append({"file": Path(frame.f_code.co_filename).name,
                             "function": frame.f_code.co_name, "line": frame.f_lineno})
            coroutine = getattr(coroutine, "cr_await", None) or getattr(coroutine, "gi_yieldfrom", None)
        return rows

    tasks = [worker] + sorted(asyncio.all_tasks() - {worker}, key=lambda task: task.get_name())[:11]
    return {"worker_chain": chain(worker.get_coro()),
            "tasks": [{"name": task.get_name()[:80], "chain": chain(task.get_coro())} for task in tasks],
            "threads": [{"id": identifier, "frames": frames(frame)}
                        for identifier, frame in sorted(sys._current_frames().items())[:8]]}


@contextmanager
def _runtime_method_timings(manager):
    """Inclusive, overlapping wall times; original bound methods and instance state are restored."""
    timings = {}

    def timed(original, row, asynchronous):
        def record(start):
            elapsed = perf_counter() - start
            row["count"] += 1
            row["seconds"] += elapsed
            row["max_seconds"] = max(row["max_seconds"], elapsed)
        @functools.wraps(original)
        def sync(*args, **kwargs):
            start = perf_counter()
            try: return original(*args, **kwargs)
            finally: record(start)
        @functools.wraps(original)
        async def async_call(*args, **kwargs):
            start = perf_counter()
            try: return await original(*args, **kwargs)
            finally: record(start)
        return async_call if asynchronous else sync

    with ExitStack() as stack:
        for owner, name, asynchronous in ((manager, "_invoke_tool", True), (manager, "_tool_context", False),
                (manager.store, "update_task", False), (manager.store, "append_event", False),
                (manager.store, "get_task", False)):
            row = {"count": 0, "seconds": 0.0, "max_seconds": 0.0}
            timings[type(owner).__name__ + "." + name] = row
            stack.enter_context(patch.object(owner, name, timed(getattr(owner, name), row, asynchronous)))
        yield timings


async def _completed_internal_task(manager, store, task_id, adapter, *, timeout=10, timings=None):
    worker = manager._workers[task_id]
    started, cpu_started = perf_counter(), process_time()

    def observation(reason):
        phase_times = {name: dict(values) for name, values in (timings or {}).items()}
        task = store.get_task(task_id)
        tail = store.events(task_id, tail_limit=5, event_types=(
            "task.status", "task.completed", "task.failed", "tool.finished", "approval.requested"))
        return {"reason": reason, "status": task["status"], "error": str(task.get("error") or "")[:400],
                "turns": adapter.turns, "step": (task.get("runtime") or {}).get("step"),
                "worker_done": worker.done(), "worker_cancelled": worker.cancelled(),
                "wait_wall_seconds": perf_counter() - started, "process_cpu_seconds": process_time() - cpu_started,
                "provider_turn_times": list(adapter.turn_times), "positions": _code_positions(worker),
                "provider_age_at_wait_start_seconds": started - adapter.started_at,
                "method_timings": phase_times,
                "timing_scope": "inclusive/overlapping completed spans; active span excluded; uncalibrated observer overhead",
                "event_tail": [{"sequence": event["sequence"], "type": event["type"],
                                "call_id": event["payload"].get("call_id")} for event in tail]}

    try:
        # Join execution and its final cleanup without repeatedly replaying the
        # growing durable history. Keep the original ten-second safety guard.
        await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
    except TimeoutError as exc:
        # Capture progress before the caller's finally block cancels and joins
        # the worker through manager.close(), then closes the store.
        raise AssertionError(observation("completion_wait_timeout")) from exc
    task = store.get_task(task_id)
    assert task["status"] == "completed", observation("worker_stopped_without_completion")
    return task


def test_completion_wait_joins_worker_without_replaying_live_history(tmp_path, monkeypatch):
    (tmp_path / "sample.txt").write_text("sample")

    async def run():
        reached_second_turn, release = asyncio.Event(), asyncio.Event()

        class GatedReads(ManyReads):
            async def turn(self, **kwargs):
                if self.turns == 1:
                    reached_second_turn.set()
                    await release.wait()
                return await super().turn(**kwargs)

        adapter = GatedReads(2)
        manager, store = build_manager(tmp_path, adapter)
        waiter = None
        try:
            task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path),
                                             provider_id="fake", mode="ask")
            worker = manager._workers[task["id"]]
            await asyncio.wait_for(reached_second_turn.wait(), timeout=10)
            replayed_rows = []
            events = store.events

            def observed_events(*args, **kwargs):
                rows = events(*args, **kwargs)
                if asyncio.current_task() is waiter and not worker.done():
                    replayed_rows.append(len(rows))
                return rows

            monkeypatch.setattr(store, "events", observed_events)
            waiting = asyncio.Event()

            async def observe_completion():
                waiting.set()
                return await _completed_internal_task(manager, store, task["id"], adapter)

            waiter = asyncio.create_task(observe_completion())
            await waiting.wait()
            release.set()
            done = await waiter
            assert replayed_rows == [], f"completion observer replayed live event rows: {replayed_rows}"
            assert worker.done() and not worker.cancelled()
            assert done["status"] == "completed" and done["result"] == "verified"
            assert adapter.turns == 3
            finished = [e for e in store.events(task["id"]) if e["type"] == "tool.finished"]
            assert [e["payload"]["call_id"] for e in finished] == ["read-1", "read-2"]
            assert all(e["payload"]["result"]["content"] == "sample" for e in finished)
            assert not any(e["type"] == "task.failed" for e in store.events(task["id"]))
        finally:
            release.set()
            if waiter is not None and not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            await manager.close()
            store.close()

    asyncio.run(run())


def test_completion_wait_timeout_captures_progress_before_worker_cleanup(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockedAnswer(ManyReads):
            async def turn(self, **kwargs):
                answer = await super().turn(**kwargs)
                entered.set()
                await release.wait()
                return answer

        adapter = BlockedAnswer(0)
        manager, store = build_manager(tmp_path, adapter)
        worker = None
        try:
            missing = object()
            originals = [(owner, name, owner.__dict__.get(name, missing)) for owner, name in (
                (manager, "_invoke_tool"), (manager, "_tool_context"), (store, "update_task"),
                (store, "append_event"), (store, "get_task"))]
            with pytest.raises(AssertionError) as error:
                with _runtime_method_timings(manager) as timings:
                    task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path),
                                                     provider_id="fake", mode="ask")
                    worker = manager._workers[task["id"]]
                    await asyncio.wait_for(entered.wait(), timeout=10)
                    await _completed_internal_task(manager, store, task["id"], adapter, timeout=0, timings=timings)
            snapshot = error.value.args[0]
            assert all(owner.__dict__.get(name, missing) is original for owner, name, original in originals)
            assert set(snapshot["method_timings"]) == {"AgentManager._invoke_tool", "AgentManager._tool_context",
                "AgentStore.update_task", "AgentStore.append_event", "AgentStore.get_task"}
            assert all(row["count"] >= 0 and 0 <= row["max_seconds"] <= row["seconds"]
                       for row in snapshot["method_timings"].values())
            assert snapshot["method_timings"]["AgentStore.get_task"]["count"] > 0
            assert snapshot["reason"] == "completion_wait_timeout"
            assert snapshot["status"] == "running" and snapshot["turns"] == 1
            assert snapshot["step"] == 0 and snapshot["error"] == ""
            assert not snapshot["worker_done"] and not snapshot["worker_cancelled"]
            assert len(snapshot["event_tail"]) <= 5
            assert any(event["type"] == "task.status" for event in snapshot["event_tail"])
            assert snapshot["provider_turn_times"] == [{"turn": 1, "seconds": adapter.turn_times[0]["seconds"]}]
            assert snapshot["provider_turn_times"][0]["seconds"] >= 0
            assert snapshot["positions"]["worker_chain"]
            assert 1 <= len(snapshot["positions"]["tasks"]) <= 12
            assert 1 <= len(snapshot["positions"]["threads"]) <= 8
            assert any(position["function"] == "turn" for task in snapshot["positions"]["tasks"]
                       for position in task["chain"])
            assert all(set(position) == {"file", "function", "line"} for thread in snapshot["positions"]["threads"]
                       for position in thread["frames"])
            assert not worker.done() and not worker.cancelled()
        finally:
            release.set()
            await manager.close()
            assert worker is None or worker.done()
            store.close()

    asyncio.run(run())


def test_internal_task_survives_more_than_old_default_requests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.txt").write_text("sample")
    async def run():
        adapter = ManyReads(300)
        # This checks the request count, independently of disk commit latency.
        # Restart persistence is exercised below with the default on-disk store.
        manager, store = build_manager(tmp_path, adapter, store_path=Path(":memory:"))
        try:
            assert next(row["file"] for row in store._db.execute("PRAGMA database_list")
                        if row["name"] == "main") == ""
            task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path),
                                             provider_id="fake", mode="ask")
            with _runtime_method_timings(manager) as timings:
                done = await _completed_internal_task(manager, store, task["id"], adapter, timings=timings)
            assert done["result"] == "verified"
            assert adapter.turns == 301
            events = store.events(task["id"])
            finished = [e for e in events if e["type"] == "tool.finished"]
            assert len(finished) == 300
            assert [e["payload"]["call_id"] for e in finished] == [f"read-{i}" for i in range(1, 301)]
            assert all(e["payload"]["result"]["ok"] and e["payload"]["result"]["content"] == "sample"
                       for e in finished)
            assert done["mode"] == "ask" and all(value is None for value in done["limits"].values())
            assert not any(e["type"] == "task.failed" for e in events)
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize("reason", ["shared_max_steps", "auto_tokens"])
def test_legacy_budget_approval_resumes_without_numeric_limits_after_restart(tmp_path, reason):
    (tmp_path / "sample.txt").write_text("sample")
    async def run():
        adapter = ManyReads(0)
        manager, store = build_manager(tmp_path, adapter)
        assert store.path == tmp_path / "agent.sqlite3"
        assert store.path.is_file()
        task = store.create_task(prompt="Inspect", cwd=str(tmp_path), provider_id="fake",
            model="fake-model", mode="ask", limits={"max_steps": 1, "max_seconds": 1})
        manager.tree_budget.reserve(task["id"], ["completed-read-1"])
        history = [{"type": "function_call_output", "call_id": "read-1", "output": "saved result"}]
        parked = ProviderCall("function", "read-2", "read_file", {"path": "sample.txt"})
        store.update_task(task["id"], status="awaiting_approval", runtime={"history": history,
            "step": 1, "tree_pending_calls": [manager._call_payload(parked)],
            "auto": {"enabled": False, "calls": 99, "tokens": 456, "cost_usd": 7.0}})
        approval = store.create_approval(task["id"], "budget", {
            "reason": reason, "tree_budget": manager.tree_budget.snapshot(task["id"]),
            "suggested_limits": {"max_steps": 2}})
        await manager.close()
        store.close()
        manager, store = build_manager(tmp_path, adapter)
        try:
            assert store.path == tmp_path / "agent.sqlite3"
            assert store.path.is_file()
            assert store.get_task(task["id"])["status"] == "awaiting_approval"
            assert adapter.turns == 0
            await manager.resolve_approval(task["id"], approval["id"], "approved")
            done = await wait_for_status(store, task["id"], "completed")
            assert all(value is None for value in done["limits"].values())
            finished = [e["payload"]["call_id"] for e in store.events(task["id"])
                        if e["type"] == "tool.finished"]
            assert finished == ["read-2"]
            assert any(i.get("call_id") == "read-1" for i in adapter.seen[-1])
            assert manager.tree_budget.snapshot(task["id"])["used_steps"] == 2
            assert done["runtime"]["auto"]["calls"] == 99
            assert done["runtime"]["auto"]["tokens"] == 456
            assert done["runtime"]["auto"]["cost_usd"] == 7.0
            with pytest.raises(ValueError):
                await manager.resolve_approval(task["id"], approval["id"], "approved")
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_old_task_time_limit_does_not_stop_a_task_after_four_hours(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, ManyReads(0))
        try:
            task = store.create_task(prompt="Inspect", cwd=str(tmp_path), provider_id="fake",
                model="fake-model", limits={"max_seconds": 1, "max_steps": 1}, mode="ask")
            await manager._drive(task["id"], started_at=monotonic() - 18_000)
            assert store.get_task(task["id"])["status"] == "completed"
            assert not store.approvals(task["id"])
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_host_configuration_persists_and_does_not_silently_clamp(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    prefs = store.update_agent({"limits": {"max_steps": 2000, "max_seconds": 86400},
        "provider_timeout_s": 600, "engines": {"devin": {
            "args": ["acp"], "env_names": ["DEVIN_API_KEY"], "mode": "code",
            "model": "fast", "config_options": {"safe": True}}}})
    assert ConfigStore(store.path).get().agent == prefs
    assert all(value is None for value in resolve_limits(prefs.limits).values())
    for patch in ({"limits": {"max_steps": 0}}, {"provider_timeout_s": True},
                  {"engines": {"devin": {"env": {"SECRET": "value"}}}}):
        with pytest.raises(ValueError):
            store.update_agent(patch)
    assert store.get().agent == prefs


@pytest.mark.parametrize("limits", [{"max_steps": 1.2}, {"max_steps": "100"},
                                   {"max_runs": 50}, {"max_seconds": 0}, {"max_steps": True}])
def test_invalid_run_limits_are_actionable(limits):
    with pytest.raises(ValueError):
        resolve_limits(limits)


def test_graphql_host_agent_configuration_is_applied_and_reloaded(tmp_path, monkeypatch, configuration_desktop_io):
    from termx.desktop import session
    releases = []

    def release_only(payload, *_args, **_kwargs):
        if payload != {"type": "release_all"}:
            configuration_desktop_io.blocked.append("unexpected configuration input")
            raise AssertionError("configuration cleanup may only release_all")
        releases.append(dict(payload))

    monkeypatch.setattr(session, "apply_event", release_only)
    state = AppState(passcode="test-only")
    with TestClient(create_app(state)) as client:
        headers = {"X-Termx-Passcode": "test-only"}
        out = data(client, 'mutation($input: JSON!) { update_agent_configuration(input: $input) }',
                   variables={"input": {"limits": {"max_steps": 1500, "max_seconds": 14400,
                        "shell_timeout_s": 120, "max_parallel_subagents": 3,
                        "max_subagents_total": 8}, "provider_timeout_s": 500}}, headers=headers)
        assert out["update_agent_configuration"]["limits"]["max_steps"] is None
        assert state.agent._limits(None)["max_steps"] is None
        out = data(client, "{ agent_configuration }", headers=headers)
        assert out["agent_configuration"]["provider_timeout_s"] is None
        assert err_status(client, '{ agent_configuration }') == 401
    assert ConfigStore(state.store.path).get().agent.limits["max_steps"] is None
    assert releases == [{"type": "release_all"}]
    assert configuration_desktop_io.closed == ["capture", "capture"]
    assert configuration_desktop_io.blocked == configuration_desktop_io.prompts == []


@pytest.mark.parametrize("visible_output", [False, True])
def test_internal_premature_sse_eof_retries_only_before_visible_output(tmp_path, visible_output):
    async def run():
        requests = []
        async def handler(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                # A cleanly closed HTTP stream without the required terminal
                # response is still a transport failure, not a successful turn.
                events = ([{"type": "response.output_text.delta", "delta": "Partial answer"}]
                          if visible_output else [{"type": "response.created", "response": {"id": "first"}}])
            else:
                events = [
                    {"type": "response.output_text.delta", "delta": "Recovered answer"},
                    {"type": "response.completed", "response": {"id": "second", "status": "completed",
                     "output": [{"type": "message", "content": [{"type": "output_text", "text": "Recovered answer"}]}]}},
                ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAIResponsesAdapter(base_url="https://fixture.invalid", model="fixture", api_key="",
                                             capabilities=["functions"], client=client)
            manager, store = build_manager(tmp_path, adapter)
            try:
                task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path), provider_id="fake", mode="ask")
                done = await wait_for_status(store, task["id"], "failed" if visible_output else "completed", timeout=3)
                events = store.events(task["id"])
                assert len(requests) == (1 if visible_output else 2)
                assert not any(event["type"] == "tool.started" for event in events)
                if visible_output:
                    assert "without a completion event" in done["error"]
                    failure = next(event for event in events if event["type"] == "provider.failed")
                    assert failure["payload"]["retry_suppressed"]
                else:
                    assert done["result"] == "Recovered answer"
                    assert any(event["type"] == "provider.retry" for event in events)
            finally:
                await manager.close()
                store.close()
    asyncio.run(run())
