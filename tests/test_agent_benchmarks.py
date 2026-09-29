"""AG2-001: deterministic fake-provider benchmark harness.

These tests pin scenario invariants (status, tool dispatch, event ordering);
the wall-clock and count metrics are written to plans/agent-v2/benchmarks/
via scripts/agent_bench.py for before/after comparison.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from bench_agent import (
    SCENARIOS,
    run_all,
    scenario_check_command,
    scenario_computer_loop,
    scenario_subagents_two,
)


def test_ask_simple_completes(tmp_path: Path) -> None:
    async def run() -> None:
        results = await run_all(tmp_path, ["ask_simple"])
        result = results["ask_simple"]
        assert result["ok"] is True
        assert result["status"] == "completed"
        assert result["provider_turns"] == 1
        assert result["tool_calls"] == 0

    asyncio.run(run())


def test_file_inspection_variants_read_ten_files(tmp_path: Path) -> None:
    async def run() -> None:
        results = await run_all(tmp_path, ["file_inspection_shell", "file_inspection_structured"])
        shell = results["file_inspection_shell"]
        assert shell["status"] == "completed"
        assert shell["tool_calls"] == 10
        structured = results["file_inspection_structured"]
        assert structured["status"] == "completed"
        assert structured["tool_calls"] == 10

    asyncio.run(run())


def test_check_command_records_output_events(tmp_path: Path) -> None:
    async def run() -> None:
        result = await scenario_check_command(tmp_path / "check")
        assert result["status"] == "completed"
        # AG2-005: streaming runner emits process.* events before exit.
        assert result["process_events"] >= 3
        assert result["events"].get("process.started") == 1
        assert result["events"].get("process.exited") == 1

    asyncio.run(run())


def test_two_subagents_complete(tmp_path: Path) -> None:
    async def run() -> None:
        result = await scenario_subagents_two(tmp_path / "subs")
        assert result["status"] == "completed"
        assert result["events"].get("subagent.started") == 2
        assert result["events"].get("subagent.finished") == 2

    asyncio.run(run())


def test_computer_loop_captures_screenshots(tmp_path: Path) -> None:
    async def run() -> None:
        result = await scenario_computer_loop(tmp_path / "computer")
        assert result["status"] == "completed"
        assert result["events"].get("computer.screenshot") == 3
        assert result["artifacts"] == 3

    asyncio.run(run())


def test_all_scenarios_registered() -> None:
    assert set(SCENARIOS) == {
        "ask_simple",
        "file_inspection_shell",
        "file_inspection_structured",
        "check_command",
        "subagents_two",
        "computer_loop",
    }
