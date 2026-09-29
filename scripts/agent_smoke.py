"""Real-provider smoke check for the Agent v2 loop (manual, credentials required).

Runs one read-only Ask-mode task end-to-end against OpenRouter's free model so
the exercise covers the real provider path: plan-free drive, typed tools,
process.* streaming, metrics. Never prints or persists credentials.

Usage:
    OPEN_ROUTER=<key> uv run python scripts/agent_smoke.py [workdir]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from termx.agent.manager import AgentManager
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.project_files import ProjectFiles

TERMINAL = {"completed", "failed", "cancelled"}


async def main() -> int:
    key = os.environ.get("OPEN_ROUTER") or os.environ.get("TERMX_AI_OPENROUTER_API_KEY")
    if not key:
        print("SKIP: OPEN_ROUTER not set")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="termx-smoke-"))
    root = tmp / "project"
    root.mkdir()
    (root / "a.py").write_text("print('hello from smoke')\n", encoding="utf-8")
    (root / "b.md").write_text("# smoke\n", encoding="utf-8")

    store = AgentStore(tmp / "agent.sqlite3", tmp / "artifacts")
    manager = AgentManager(store, CredentialStore(memory={}), None, project_files=ProjectFiles())
    try:
        manager.save_provider(
            provider_id="smoke",
            kind="openai-compatible",
            name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            model="openrouter/free",
            capabilities=["shell", "functions"],
            api_key=key,
        )
        task = await manager.create_task(
            prompt=(
                "Use list_files to see the project, then use read_file on a.py, "
                "and tell me what running it would print."
            ),
            cwd=str(root),
            provider_id="smoke",
            mode="ask",
        )
        task_id = task["id"]
        deadline = time.monotonic() + 120
        while True:
            current = store.get_task(task_id)
            if current["status"] in TERMINAL:
                task = store.get_task(task_id, include_events=True)
                break
            if time.monotonic() > deadline:
                print("TIMEOUT waiting for task; status:", current["status"])
                return 1
            await asyncio.sleep(0.5)
        events = task.get("events") or []
        histogram: dict[str, int] = {}
        for event in events:
            histogram[event["type"]] = histogram.get(event["type"], 0) + 1
        report = {
            "status": task["status"],
            "result": (task.get("result") or "")[:400],
            "error": task.get("error"),
            "metrics": task.get("metrics"),
            "events": histogram,
        }
        print(json.dumps(report, indent=2))
        return 0 if task["status"] == "completed" else 1
    finally:
        await manager.close()
        store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
