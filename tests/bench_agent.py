"""Deterministic Agent benchmark harness (AG2-001).

Drives AgentManager end-to-end with a scripted fake provider so measurements
are provider-independent: wall-clock, provider turns, tool-call counts and
event histograms. Used by tests/test_agent_benchmarks.py and
scripts/agent_bench.py (which writes plans/agent-v2/benchmarks/*.json).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from termx.agent.manager import AgentManager
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore

TERMINAL = {"completed", "failed", "cancelled"}


class FakeComputer:
    def __init__(self, screenshot: bytes = b"jpeg" * 64) -> None:
        self.actions: list[list[dict[str, Any]]] = []
        self.screenshot = screenshot
        self.released = 0

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
        self.actions.append(actions)
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        return self.screenshot

    async def release_all(self) -> None:
        self.released += 1


class BenchAdapter:
    """Scripted provider: each entry in `script` is one turn's call list."""

    def __init__(self, script: list[list[ProviderCall]], *, turn_delay_s: float = 0.0,
                 scripts_by_prompt: dict[str, list[list[ProviderCall]]] | None = None) -> None:
        self.script = [list(calls) for calls in script]
        self.scripts_by_prompt = {prompt: [list(calls) for calls in turns]
                                  for prompt, turns in (scripts_by_prompt or {}).items()}
        self.turn_delay_s = turn_delay_s
        self.turns = 0
        self.plan_calls = 0

    async def test(self) -> str:
        return "OK"

    async def plan(self, prompt: str, cwd: str, manifest: dict[str, Any]):
        self.plan_calls += 1
        return (
            {"summary": prompt, "steps": ["Inspect", "Act", "Verify"], "tools": ["shell"], "risks": []},
            "plan-response",
        )

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
        allow_subagents: bool = True,
    ) -> ProviderTurn:
        self.turns += 1
        turn_number = self.turns
        if self.turn_delay_s:
            await asyncio.sleep(self.turn_delay_s)
        script = next((turns for key, turns in self.scripts_by_prompt.items()
                       if prompt == key or prompt.startswith(key + '\n\n')), self.script)
        calls = script.pop(0) if script else []
        text = "" if calls else "Done."
        return ProviderTurn(
            response_id=f"response-{turn_number}",
            text=text,
            calls=list(calls),
            usage={"input_tokens": 8, "output_tokens": 4},
            output_items=[
                {
                    "type": "function_call",
                    "call_id": c.call_id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in calls
            ]
            or ([{"type": "message", "content": [{"type": "output_text", "text": text}]}] if text else []),
        )


def fn(call_id: str, name: str, **arguments: Any) -> ProviderCall:
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments)


def computer(call_id: str, *actions: dict[str, Any]) -> ProviderCall:
    return ProviderCall(type="computer", call_id=call_id, actions=list(actions))


def make_files(root: Path, count: int = 10) -> list[str]:
    names = []
    for index in range(count):
        name = f"file_{index:02d}.txt"
        (root / name).write_text(f"contents of {name}\n" * 20, encoding="utf-8")
        names.append(name)
    return names


def build_manager(tmp_path: Path, adapter: BenchAdapter) -> tuple[AgentManager, AgentStore]:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    manager = AgentManager(store, CredentialStore(memory={}), desktop=None, adapter_factory=lambda _p, _k: adapter)
    manager._computer = FakeComputer()  # type: ignore[assignment]
    manager.save_provider(
        provider_id="bench",
        kind="openai-compatible",
        name="Bench",
        base_url="http://127.0.0.1:9/v1",
        model="bench-model",
        capabilities=["shell", "computer"],
        api_key="bench-key",
    )
    return manager, store


async def run_scenario(
    tmp_path: Path,
    adapter: BenchAdapter,
    *,
    prompt: str = "Benchmark task",
    mode: str = "agent",
    auto_approve: bool = True,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    manager, store = build_manager(tmp_path, adapter)
    started = time.monotonic()
    task = await manager.create_task(prompt=prompt, cwd=str(tmp_path), provider_id="bench", mode=mode)
    task_id = task["id"]
    deadline = started + timeout_s
    try:
        while True:
            current = store.get_task(task_id, include_events=True)
            if current["status"] in TERMINAL:
                task = current
                break
            if auto_approve:
                for approval in current.get("approvals") or []:
                    if approval["status"] == "pending":
                        await manager.resolve_approval(task_id, approval["id"], "approved")
                        break
                else:
                    await asyncio.sleep(0.005)
                if time.monotonic() > deadline:
                    raise TimeoutError(f"scenario timed out at status {current['status']}")
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("scenario timed out")
            await asyncio.sleep(0.005)
        wall_ms = (time.monotonic() - started) * 1000
        events = task.get("events") or []
        histogram: dict[str, int] = {}
        for event in events:
            histogram[event["type"]] = histogram.get(event["type"], 0) + 1
        tool_calls = histogram.get("tool.started", 0)
        process_events = sum(v for k, v in histogram.items() if k.startswith("process."))
        return {
            "status": task["status"],
            "error": task.get("error"),
            "wall_ms": round(wall_ms, 2),
            "provider_turns": adapter.turns,
            "tool_calls": tool_calls,
            "process_events": process_events,
            "events": histogram,
            "event_count": len(events),
            "artifacts": len(task.get("artifacts") or []),
            "approvals": len(task.get("approvals") or []),
        }
    finally:
        await manager.close()
        store.close()


async def scenario_ask_simple(tmp_path: Path) -> dict[str, Any]:
    return await run_scenario(tmp_path / "ask", BenchAdapter([[]]), mode="ask")


async def scenario_file_inspection_shell(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "files-shell"
    root.mkdir(parents=True, exist_ok=True)
    names = make_files(root)
    calls = [fn(f"s{i}", "run_shell", command=f"cat {name}", purpose="read file") for i, name in enumerate(names)]
    return await run_scenario(root, BenchAdapter([calls]), prompt="Read every file")


async def scenario_file_inspection_structured(tmp_path: Path) -> dict[str, Any]:
    """Same 10-file inspection via structured read_file calls (parallel-safe)."""
    root = tmp_path / "files-struct"
    root.mkdir(parents=True, exist_ok=True)
    names = make_files(root)
    calls = [fn(f"r{i}", "read_file", path=name) for i, name in enumerate(names)]
    return await run_scenario(root, BenchAdapter([calls]), prompt="Read every file")


async def scenario_check_command(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "check"
    root.mkdir(parents=True, exist_ok=True)
    command = "i=0; while [ $i -lt 400 ]; do echo line-$i; i=$((i+1)); done"
    calls = [fn("c1", "run_check", kind="test", command=command, timeout_s=60)]
    result = await run_scenario(root, BenchAdapter([calls]), prompt="Run the test suite")
    if result["status"] != "completed":
        # Pre-v2 fallback: run_check does not exist yet; use the shell path.
        calls = [fn("c1", "run_shell", command=command, purpose="run tests", timeout_s=60)]
        result = await run_scenario(root, BenchAdapter([calls]), prompt="Run the test suite")
        result["tool"] = "run_shell"
    else:
        result["tool"] = "run_check"
    return result


async def scenario_subagents_two(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "subs"
    root.mkdir(parents=True, exist_ok=True)
    # Parallel tasks have independent provider histories. A global FIFO can
    # accidentally give the parent's final response to a child (or vice versa)
    # when isolation/setup timing differs across platforms.
    scripts = {
        "Fan out two readers": [
            [fn("p1", "spawn_subagent", task="Summarize file_a", agent="reader"),
             fn("p2", "spawn_subagent", task="Summarize file_b", agent="reader")],
            [fn("wait", "await_subagents")],
            [],
        ],
        "Summarize file_a": [[fn("c1", "run_shell", command="echo child1", purpose="work")], []],
        "Summarize file_b": [[fn("c2", "run_shell", command="echo child2", purpose="work")], []],
    }
    return await run_scenario(root, BenchAdapter([], turn_delay_s=0.05, scripts_by_prompt=scripts),
                              prompt="Fan out two readers")


async def scenario_computer_loop(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "computer"
    root.mkdir(parents=True, exist_ok=True)
    script = [
        [computer("k1", {"type": "screenshot"})],
        [computer("k2", {"type": "click", "x": 10, "y": 10})],
        [computer("k3", {"type": "type", "text": "hi"})],
        [],
    ]
    return await run_scenario(root, BenchAdapter(script), prompt="Drive the desktop")


SCENARIOS = {
    "ask_simple": scenario_ask_simple,
    "file_inspection_shell": scenario_file_inspection_shell,
    "file_inspection_structured": scenario_file_inspection_structured,
    "check_command": scenario_check_command,
    "subagents_two": scenario_subagents_two,
    "computer_loop": scenario_computer_loop,
}


async def run_all(base: Path, names: list[str] | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, fn_ in SCENARIOS.items():
        if names and name not in names:
            continue
        try:
            results[name] = await fn_(base / name)
            results[name]["ok"] = True
        except Exception as exc:  # scenarios may exercise not-yet-built tools
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return results
