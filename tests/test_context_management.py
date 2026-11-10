"""Per-model context management: limits from the model, usage-driven compaction,
ingestion caps, optional LLM summariser and the subagent handoff."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from test_agent import FakeAdapter, _FanOutAdapter, _fn, _git_repo, build_manager, wait_for_status
from test_model_routing import _info, _routed
from termx.agent.context import ContextEngine
from termx.agent.context.engine import (
    DEFAULT_CONTEXT_LIMITS,
    checkpoint_message_payload,
    estimate_tokens,
    limits_for_model,
)
from termx.agent.providers import ProviderCall, ProviderError, ProviderTurn


def _transcript(turns: int, big: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{"role": "user", "content": "do the task"}]
    for i in range(turns):
        items.append({"type": "function_call", "call_id": f"c{i}", "name": "read_file",
                      "arguments": {"path": f"src/file{i}.py"}})
        items.append({"type": "function_call_output", "call_id": f"c{i}", "output": big})
    return items


# -- limits from the model --------------------------------------------------------------------


def test_limits_come_from_window_minus_reserved_output() -> None:
    limits = limits_for_model(_info(128_000, 16_000))
    assert limits["context_window"] == 128_000
    assert limits["max_context_estimate_tokens"] == 112_000
    # Output room unknown: a default reservation, never more than half the window.
    assert limits_for_model(_info(64_000))["max_context_estimate_tokens"] == 64_000 - 8_192
    assert limits_for_model(_info(8_000, 50_000))["max_context_estimate_tokens"] == 4_000


def test_unknown_window_uses_the_120k_default() -> None:
    for meta in (None, _info(), {}):
        limits = limits_for_model(meta)
        assert limits["context_window"] is None
        assert limits["max_context_estimate_tokens"] == 120_000
    assert DEFAULT_CONTEXT_LIMITS["max_context_estimate_tokens"] == 120_000


def test_tool_result_cap_scales_with_the_window() -> None:
    assert limits_for_model(_info(8_000, 1_000))["tool_result_cap_chars"] == 2_000
    assert limits_for_model(_info(32_000, 4_000))["tool_result_cap_chars"] == 2_560
    assert limits_for_model(_info(200_000, 8_000))["tool_result_cap_chars"] == 8_000
    assert limits_for_model(None)["tool_result_cap_chars"] == 8_000


# -- compaction triggers -----------------------------------------------------------------------


def test_the_window_decides_when_the_same_history_compacts() -> None:
    history = _transcript(30, "y" * 400)
    assert estimate_tokens(history) > 3_000
    assert ContextEngine("/tmp", limits_for_model(_info(200_000, 8_000))).plan_compaction(history) is None
    assert ContextEngine("/tmp", limits_for_model(_info(4_000, 500))).plan_compaction(history) is not None


def test_reconfiguring_to_a_smaller_window_compacts_the_next_call() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(200_000, 8_000)))
    history = _transcript(30, "y" * 400)
    assert engine.plan_compaction(history) is None
    engine.configure(limits_for_model(_info(4_000, 500)))
    assert engine.plan_compaction(history) is not None


def test_real_input_tokens_beat_the_estimate_in_both_directions() -> None:
    history = _transcript(40, "y" * 400)
    limits = limits_for_model(_info(8_000, 1_000))  # trigger at 5,250 tokens
    assert estimate_tokens(history) > 5_250
    # Provider says the prompt was small: trust it, do not compact.
    low = ContextEngine("/tmp", limits)
    low.note_usage(1_000, len(history))
    assert low.plan_compaction(history) is None
    # Provider says the prompt was large while the estimate is small.
    small = _transcript(30, "y")
    assert estimate_tokens(small) < 5_250
    assert ContextEngine("/tmp", limits).plan_compaction(small) is None
    high = ContextEngine("/tmp", limits)
    high.note_usage(7_000, len(small))
    assert high.plan_compaction(small) is not None


def test_items_added_after_the_last_usage_are_estimated_on_top() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(8_000, 1_000)))
    history = _transcript(30, "y")
    engine.note_usage(5_000, len(history))
    assert engine.plan_compaction(history) is None
    history += [{"type": "function_call_output", "call_id": "c0", "output": "z" * 4_000}]
    assert engine.context_used(history) >= 5_000 + 1_000
    assert engine.plan_compaction(history) is not None


def test_a_route_change_drops_the_old_models_usage() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(8_000, 1_000)))
    history = _transcript(30, "y")
    engine.note_usage(7_000, len(history))
    engine.configure(limits_for_model(_info(8_000, 1_000)))  # same route: usage kept
    assert engine.plan_compaction(history) is not None
    engine.configure(limits_for_model(_info(16_000, 1_000)))  # different route: tokenizer unknown
    assert engine.context_used(history) == estimate_tokens(history)


def test_compaction_is_idempotent() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(4_000, 500)))
    history = _transcript(30, "y" * 400)
    plan = engine.plan_compaction(history)
    assert plan is not None
    compacted = plan["history"]
    # Still over budget (the retained tail is big) but there is nothing older to fold in.
    assert engine.plan_compaction(compacted) is None


def test_a_second_compaction_carries_the_first_summary_forward() -> None:
    engine = ContextEngine("/tmp", {"max_context_estimate_tokens": 10})
    first = engine.plan_compaction(_transcript(4, "y"), keep_recent=2)
    assert first is not None and first["payload"]["completed_steps"]
    history = first["history"] + _transcript(2, "w")[1:]
    second = engine.plan_compaction(history, keep_recent=2)
    assert second is not None
    steps = second["payload"]["completed_steps"]
    assert all(step in steps for step in first["payload"]["completed_steps"])
    assert "file0.py" in " ".join(second["payload"]["important_files"])
    # The old checkpoint is folded in, never retained next to the new one.
    assert sum(1 for item in second["history"] if checkpoint_message_payload(item)) == 1


# -- tool results at ingestion ----------------------------------------------------------------


def test_oversized_tool_results_keep_head_and_tail_with_a_marker() -> None:
    engine = ContextEngine("/tmp", {"tool_result_cap_chars": 1_000})
    body = "HEAD" + "m" * 5_000 + "TAIL"
    items = [{"type": "function_call_output", "call_id": "c", "output": body},
             {"type": "function_call_output", "call_id": "d", "output": "small"}]
    capped = engine.cap_tool_results(items)
    out = capped[0]["output"]
    assert len(out) <= 1_000
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert "re-read with offset" in out
    assert capped[1] is items[1] and items[0]["output"] == body  # inputs are not mutated
    # Idempotent: a capped result is left alone.
    assert engine.cap_tool_results(capped)[0]["output"] == out


def test_the_marker_reports_how_many_characters_were_cut() -> None:
    engine = ContextEngine("/tmp", {"tool_result_cap_chars": 1_000})
    body = "#" * 4_000
    out = engine.cap_tool_results([{"type": "function_call_output", "call_id": "c", "output": body}])[0]["output"]
    kept = out.count("#")
    cut = int(out.split("truncated ")[1].split(" ")[0])
    assert kept + cut == len(body)


def test_slim_history_applies_the_per_result_cap_as_a_second_defence() -> None:
    engine = ContextEngine("/tmp", {"tool_result_cap_chars": 500})
    slim = engine.slim_history(_transcript(2, "q" * 3_000))
    assert all(len(i["output"]) <= 500 for i in slim if i.get("type") == "function_call_output")


def test_a_capped_read_file_result_stays_valid_json_and_points_at_the_next_line() -> None:
    engine = ContextEngine("/tmp", {"tool_result_cap_chars": 2_000})
    lines = [f"line {i}\n" for i in range(1, 3_001)]
    result = {"ok": True, "path": "big.py", "revision": "r1", "start_line": 10, "line_count": 3_010,
              "content": "".join(lines), "truncated": False}
    out = engine.cap_tool_results([{"type": "function_call_output", "call_id": "c", "output": json.dumps(result)}])[0]["output"]
    assert len(out) <= 2_000
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    kept = parsed["content"].splitlines()
    assert kept and kept[0] == "line 1"
    # The next read starts at the first line that was cut, so nothing is skipped or repeated.
    assert parsed["next_offset"] == 10 + len(kept)
    assert "offset" in parsed["cap_note"]
    assert engine.cap_tool_results([{"type": "function_call_output", "call_id": "c", "output": out}])[0]["output"] == out


def test_decisions_and_the_model_summary_survive_a_later_rule_based_compaction() -> None:
    async def run() -> None:
        engine = ContextEngine("/tmp", {"max_context_estimate_tokens": 10})

        async def summarise(_older: list[dict[str, Any]]) -> dict[str, Any]:
            return {"summary": "Model summary.", "decisions": ["use argparse"], "pending_work": []}

        first = await engine.compact(_transcript(4, "y"), keep_recent=2, summarise=summarise)
        assert first is not None and first["summariser"] == "model"
        history = first["history"] + _transcript(2, "w")[1:]
        second = await engine.compact(history, keep_recent=2)
        assert second is not None and second["summariser"] is None
        assert "use argparse" in second["payload"]["decisions"]
        assert "Model summary." in second["payload"]["earlier_summary"]

    asyncio.run(run())


def test_a_short_history_still_compacts_when_a_small_window_cannot_hold_it() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(4_000, 500)))  # 3,500 budget, trigger at 2,625
    history = _transcript(18, "y" * 700)  # 37 items, under the usual 40 kept, about 4,000 tokens
    assert estimate_tokens(history) > 3_500
    plan = engine.plan_compaction(history)
    assert plan is not None
    assert estimate_tokens(plan["history"]) < 3_500
    types = [item.get("type") for item in plan["history"][2:]]
    assert types[0] != "function_call_output"  # no orphaned output at the new tail


def test_context_used_is_a_plain_query() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(8_000, 1_000)))
    history = _transcript(3, "y")
    assert engine.context_used(history) == estimate_tokens(history)
    assert not hasattr(engine, "last_used")


# -- optional LLM summariser ------------------------------------------------------------------


def test_summariser_text_replaces_the_rule_based_summary() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(4_000, 500)))
    seen: list[int] = []

    async def summarise(older: list[dict[str, Any]]) -> dict[str, Any]:
        seen.append(len(older))
        return {"summary": "Read thirty files.", "decisions": ["keep the API"], "pending_work": ["run tests"]}

    plan = asyncio.run(engine.compact(_transcript(30, "y" * 400), summarise=summarise))
    assert plan is not None and seen and seen[0] == plan["dropped"]
    assert plan["summariser"] == "model"
    payload = plan["payload"]
    assert payload["summary"] == "Read thirty files."
    assert payload["decisions"] == ["keep the API"] and "run tests" in payload["pending_work"]
    assert payload["completed_steps"]  # structural fields still come from the rules
    body = checkpoint_message_payload(plan["history"][1])
    assert body is not None and body["summary"]["summary"] == "Read thirty files."


@pytest.mark.parametrize("failure", ["raise", "timeout", "garbage"])
def test_summariser_failure_falls_back_to_the_rule_based_plan(failure: str) -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(4_000, 500)))
    history = _transcript(30, "y" * 400)
    expected = engine.plan_compaction(history)

    async def summarise(_older: list[dict[str, Any]]) -> Any:
        if failure == "raise":
            raise RuntimeError("model down")
        if failure == "timeout":
            await asyncio.sleep(5)
        return "not a dict"

    plan = asyncio.run(engine.compact(history, summarise=summarise, timeout_s=0.05))
    assert plan is not None and expected is not None
    assert plan["summariser"] == "fallback" and plan["summariser_error"]
    assert plan["history"] == expected["history"] or plan["history"][2:] == expected["history"][2:]
    assert plan["payload"]["summary"] == expected["payload"]["summary"]


def test_compact_without_a_summariser_is_the_rule_based_plan() -> None:
    engine = ContextEngine("/tmp", limits_for_model(_info(4_000, 500)))
    history = _transcript(30, "y" * 400)
    plan = asyncio.run(engine.compact(history))
    assert plan is not None and plan["summariser"] is None
    assert asyncio.run(ContextEngine("/tmp").compact(_transcript(3, "y"))) is None


# -- manager wiring ---------------------------------------------------------------------------


class LoopAdapter(FakeAdapter):
    """Reads a file for `turns` turns, padding the transcript with assistant text each time."""

    def __init__(self, turns: int = 20, pad: int = 3_000, usage: dict[str, int] | None = None) -> None:
        super().__init__()
        self.limit, self.pad, self.usage = turns, pad, usage or {}
        self.sizes: list[int] = []
        self.on_turn: Any = None

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        items = kwargs.get("input_items") or []
        self.inputs.append(items)
        self.sizes.append(estimate_tokens(items))
        self.turns += 1
        if self.on_turn is not None:
            self.on_turn(self.turns)
        if self.turns > self.limit:
            return ProviderTurn("done", "Finished.", [], {}, [])
        call = ProviderCall(type="function", call_id=f"r{self.turns}", name="read_file", arguments={"path": "a.txt"})
        return ProviderTurn(
            f"response-{self.turns}", "", [call], dict(self.usage),
            [{"role": "assistant", "content": "p" * self.pad},
             {"type": "function_call", "call_id": call.call_id, "name": "read_file",
              "arguments": json.dumps(call.arguments)}],
        )


def _events(store: Any, task_id: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(task_id) if e["type"] == kind]


async def _run(tmp_path: Path, adapter: Any, model: str, routing: dict | None = None):
    (tmp_path / "a.txt").write_text("hello\n")
    manager, store = _routed(tmp_path, adapter, routing)
    task = await manager.create_task(
        prompt="read", cwd=str(tmp_path), provider_id="fake", model=model, mode="ask")
    return manager, store, task


def test_a_small_window_model_compacts_while_a_big_one_does_not(tmp_path: Path) -> None:
    async def run() -> None:
        small = LoopAdapter()
        (tmp_path / "s").mkdir()
        manager, store, task = await _run(tmp_path / "s", small, "fake-model")
        await wait_for_status(store, task["id"], "completed")
        assert _events(store, task["id"], "context.compaction.started")
        assert _events(store, task["id"], "context.checkpoint")
        await manager.close()
        store.close()

        big = LoopAdapter()
        (tmp_path / "b").mkdir()
        manager, store, task = await _run(tmp_path / "b", big, "big-model")
        await wait_for_status(store, task["id"], "completed")
        assert not _events(store, task["id"], "context.compaction.started")
        # The same turns kept growing instead.
        assert max(big.sizes) > max(small.sizes)
        await manager.close()
        store.close()

    asyncio.run(run())


def test_switching_to_a_smaller_model_compacts_before_the_next_call(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = LoopAdapter(turns=24)
        manager, store, task = await _run(tmp_path, adapter, "big-model")

        def switch(turn: int) -> None:
            if turn == 18:  # the user (or a reassignment) moves the task to the 8k model
                store.update_task(task["id"], model="fake-model")

        adapter.on_turn = switch
        await wait_for_status(store, task["id"], "completed")
        assert _events(store, task["id"], "context.compaction.started"), "shrinking the window must trigger compaction"
        before_switch = adapter.sizes[17]
        after_switch = adapter.sizes[18]
        assert after_switch < before_switch, "the call right after the switch was already compacted"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_fallback_to_a_smaller_window_compacts_before_the_fallback_call(tmp_path: Path) -> None:
    class FailsAfter(LoopAdapter):
        async def turn(self, **kwargs: Any) -> ProviderTurn:
            if self.turns >= 18:
                self.turns += 1
                raise ProviderError("denied", status_code=401)
            return await super().turn(**kwargs)

    async def run() -> None:
        (tmp_path / "a.txt").write_text("hello\n")
        primary, backup = FailsAfter(turns=40), LoopAdapter(turns=0)
        routing = {"orchestrator": {"provider_id": "fake", "model": "big-model",
                                    "fallbacks": [{"provider_id": "fake", "model": "fake-model"}]}}
        manager, store = _routed(tmp_path, FakeAdapter(), routing)

        def pick(provider: dict[str, Any], key: str) -> Any:
            return backup if provider["model"] == "fake-model" else primary

        manager._adapter_factory = pick
        task = await manager.create_task(
            prompt="read", cwd=str(tmp_path), provider_id="fake", model="big-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        assert _events(store, task["id"], "provider.fallback")
        assert _events(store, task["id"], "context.compaction.started")
        assert backup.sizes and backup.sizes[0] < max(primary.sizes)
        await manager.close()
        store.close()

    asyncio.run(run())


def test_real_usage_from_the_provider_drives_compaction(tmp_path: Path) -> None:
    async def run() -> None:
        # Tiny transcript, but the provider reports an almost full 8k window every turn.
        adapter = LoopAdapter(turns=45, pad=10, usage={"input_tokens": 6_900, "output_tokens": 10})
        manager, store, task = await _run(tmp_path, adapter, "fake-model")
        await wait_for_status(store, task["id"], "completed")
        assert _events(store, task["id"], "context.compaction.started")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_task_context_reports_used_over_window(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = LoopAdapter(turns=3, usage={"input_tokens": 1_234, "output_tokens": 5})
        manager, store, task = await _run(tmp_path, adapter, "fake-model")
        await wait_for_status(store, task["id"], "completed")
        context = manager.task_context(task["id"])
        assert context["window"] == 8_000 and context["limit"] == 7_000
        assert context["window_known"] is True
        assert context["used"] and context["used"] >= 1_234
        await manager.close()
        store.close()

    asyncio.run(run())


def test_task_context_flags_an_unknown_window(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, LoopAdapter(turns=0))
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        context = manager.task_context(task["id"])
        assert context["window"] is None and context["window_known"] is False
        assert context["limit"] == 120_000
        await manager.close()
        store.close()

    asyncio.run(run())


def test_oversized_tool_output_is_capped_before_it_reaches_the_next_prompt(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = LoopAdapter(turns=2, pad=10)
        (tmp_path / "a.txt").write_text("line of text\n" * 5_000)
        manager, store = _routed(tmp_path, adapter)
        task = await manager.create_task(
            prompt="read", cwd=str(tmp_path), provider_id="fake", model="fake-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        outputs = [i["output"] for i in adapter.inputs[-1] if i.get("type") == "function_call_output"]
        assert outputs and all(len(o) <= 2_000 for o in outputs)
        assert any("re-read with offset" in o for o in outputs)
        await manager.close()
        store.close()

    asyncio.run(run())


class SummaryAdapter(FakeAdapter):
    def __init__(self, text: str | Exception, usage: dict[str, int] | None = None) -> None:
        super().__init__()
        self.text, self.prompts, self.usage = text, [], usage or {}

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        self.prompts.append(kwargs)
        if isinstance(self.text, Exception):
            raise self.text
        return ProviderTurn("s", self.text, [], dict(self.usage), [])


def _summariser_manager(tmp_path: Path, summary: SummaryAdapter, main: LoopAdapter):
    (tmp_path / "a.txt").write_text("hello\n")
    routing = {"summariser": {"provider_id": "other", "model": "o-1", "fallbacks": []}}
    manager, store = _routed(tmp_path, FakeAdapter(), routing)
    manager._adapter_factory = lambda provider, _key: summary if provider["id"] == "other" else main
    return manager, store


def test_a_configured_summariser_writes_the_checkpoint_summary(tmp_path: Path) -> None:
    async def run() -> None:
        reply = json.dumps({"summary": "Model summary.", "decisions": ["d1"], "pending_work": []})
        summary, main = SummaryAdapter(reply), LoopAdapter()
        manager, store = _summariser_manager(tmp_path, summary, main)
        task = await manager.create_task(
            prompt="read", cwd=str(tmp_path), provider_id="fake", model="fake-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        assert summary.prompts, "the summariser role's model was called"
        assert summary.prompts[0]["read_only"] is True and summary.prompts[0]["allow_subagents"] is False
        checkpoint = store.latest_checkpoint(task["id"], kind="context")
        assert checkpoint["payload"]["summary"] == "Model summary."
        assert _events(store, task["id"], "context.compaction.completed")[0]["summariser"] == "model"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_summariser_calls_count_toward_the_task_usage(tmp_path: Path) -> None:
    async def run() -> None:
        reply = json.dumps({"summary": "Model summary.", "decisions": [], "pending_work": []})
        summary = SummaryAdapter(reply, usage={"input_tokens": 700, "output_tokens": 50})
        manager, store = _summariser_manager(tmp_path, summary, LoopAdapter())
        task = await manager.create_task(
            prompt="read", cwd=str(tmp_path), provider_id="fake", model="fake-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        calls = len(summary.prompts)
        assert calls >= 1
        assert manager.task_usage(task["id"])["tokens"] >= calls * 750
        await manager.close()
        store.close()

    asyncio.run(run())


def test_summariser_error_keeps_the_task_running_on_rule_based_compaction(tmp_path: Path) -> None:
    async def run() -> None:
        summary, main = SummaryAdapter(RuntimeError("summariser down")), LoopAdapter()
        manager, store = _summariser_manager(tmp_path, summary, main)
        task = await manager.create_task(
            prompt="read", cwd=str(tmp_path), provider_id="fake", model="fake-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        completed = _events(store, task["id"], "context.compaction.completed")
        assert completed and completed[0]["summariser"] == "fallback"
        checkpoint = store.latest_checkpoint(task["id"], kind="context")
        assert checkpoint["payload"]["summary"].startswith("Compacted ")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_no_summariser_role_means_no_extra_model_call(tmp_path: Path) -> None:
    async def run() -> None:
        main = LoopAdapter()
        manager, store, task = await _run(tmp_path, main, "fake-model")
        await wait_for_status(store, task["id"], "completed")
        completed = _events(store, task["id"], "context.compaction.completed")
        assert completed and completed[0].get("summariser") is None
        await manager.close()
        store.close()

    asyncio.run(run())


# -- subagent handoff -------------------------------------------------------------------------


def test_brief_has_goal_constraints_files_and_context() -> None:
    from termx.agent.context.handoff import build_brief

    brief = build_brief(
        "Add the flag", constraints="No network.", files=["src/cli.py", "tests/test_cli.py"],
        context="Design says: use argparse.")
    for text in ("Goal", "Add the flag", "Constraints", "No network.", "src/cli.py",
                 "tests/test_cli.py", "Context", "use argparse."):
        assert text in brief
    assert build_brief("Only a goal") == "Goal\nOnly a goal"


def test_changed_files_lists_committed_and_uncommitted_edits(tmp_path: Path) -> None:
    from termx.agent import worktrees
    from termx.agent.context.handoff import changed_files

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    spec = worktrees.create_worktree(str(repo), "abc123def456")
    path = Path(spec["worktree_path"])
    (path / "new.txt").write_text("x")
    (path / "README.md").write_text("changed\n")
    assert changed_files(spec["worktree_path"], spec["base_ref"]) == ["README.md", "new.txt"]
    assert changed_files(str(tmp_path / "missing"), "HEAD") == []


def test_finished_worktree_child_reports_changed_files_off_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from termx.agent import manager as manager_module

    seen: list[bool] = []

    def fake_changed_files(path: str, base_ref: str) -> list[str]:
        seen.append(threading.current_thread() is threading.main_thread())
        return ["a.py"]

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        handle = manager_module._SubagentHandle(parent_id="p", child_id="c", status="completed", result="ok")
        handle.done.set()
        handles = {"c": handle}
        monkeypatch.setattr(manager, "_subagent_handles", lambda _task_id: handles)
        monkeypatch.setattr(manager.store, "task_worktree", lambda _id: {
            "worktree_path": "/wt", "base_ref": "HEAD", "status": "active"})
        monkeypatch.setattr(manager_module, "changed_files", fake_changed_files)
        call = ProviderCall(type="function", call_id="s", name="subagent_status", arguments={})
        # The synchronous status never runs git; the async report adds the files from a worker thread.
        assert "changed_files" not in manager._subagent_statuses("p", call)["children"]["c"]
        report = await manager._subagent_report("p", call)
        assert report["children"]["c"]["changed_files"] == ["a.py"]
        again = await manager._subagent_report("p", call)  # cached once the child has finished
        assert again["children"]["c"]["changed_files"] == ["a.py"]
        assert seen == [False]
        await manager.close()
        store.close()

    asyncio.run(run())


class BriefAdapter(_FanOutAdapter):
    def __init__(self, routes: dict) -> None:
        super().__init__(routes)
        self.prompts: list[str] = []

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        self.prompts.append(kwargs["prompt"])
        return await super().turn(**kwargs)


def test_spawned_child_gets_a_structured_brief_and_parent_gets_the_final_message(tmp_path: Path) -> None:
    async def run() -> None:
        from test_agent import _approve_pending

        adapter = BriefAdapter({
            "Coordinate": ([[_fn("s1", "spawn_subagent", task="child-goal", instructions="No network.",
                                 files=["src/a.py"], context="Design says: use argparse."),
                             ], [_fn("w1", "await_subagents")]], 0.0),
            "child-goal": ([], 0.0),
        })
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await wait_for_status(store, parent["id"], "completed")
        child_prompt = next(p for p in adapter.prompts if "child-goal" in p)
        for text in ("Goal", "child-goal", "Constraints", "No network.", "src/a.py",
                     "Context", "use argparse"):
            assert text in child_prompt
        awaited = [e["payload"] for e in store.events(parent["id"]) if e["type"] == "subagent.finished"]
        assert awaited and awaited[-1]["status"] == "completed"
        assert awaited[-1]["result"] == "child-goal done."
        await manager.close()
        store.close()

    asyncio.run(run())
