"""Live Agent-mode smoke: write tools + approval flow against OpenRouter.

Creates a bounded Agent task that must use write_file + run_check, auto-approves
approvals, and dumps the event histogram + metrics + written files.
Never prints credentials.

Usage: OPEN_ROUTER=<key> uv run python scripts/agent_smoke_agent_mode.py
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
    tmp = Path(tempfile.mkdtemp(prefix="termx-smoke-agent-"))
    root = tmp / "project"
    root.mkdir()
    (root / "README.md").write_text("# demo\n", encoding="utf-8")

    store = AgentStore(tmp / "agent.sqlite3", tmp / "artifacts")
    manager = AgentManager(store, CredentialStore(memory={}), None, project_files=ProjectFiles())
    approvals_seen = 0
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
                "Create a file called notes.txt containing the text 'agent wrote this' "
                "using the write_file tool, then run a check that the file exists "
                "using the run_check tool with command 'test -f notes.txt && echo OK'. "
                "Finally reply with what you did."
            ),
            cwd=str(root),
            provider_id="smoke",
            mode="agent",
        )
        task_id = task["id"]
        deadline = time.monotonic() + 240
        while True:
            current = store.get_task(task_id)
            if current["status"] in TERMINAL:
                task = store.get_task(task_id, include_events=True)
                break
            for approval in store.approvals(task_id):
                if approval["status"] == "pending":
                    approvals_seen += 1
                    print("AUTO-APPROVING:", approval["payload"].get("title"), "|",
                          (approval["payload"].get("call") or {}).get("name"))
                    await manager.resolve_approval(task_id, approval["id"], "approved")
                    break
            else:
                await asyncio.sleep(0.5)
            if time.monotonic() > deadline:
                print("TIMEOUT; status:", store.get_task(task_id)["status"])
                return 1
        events = task.get("events") or []
        histogram: dict[str, int] = {}
        for event in events:
            histogram[event["type"]] = histogram.get(event["type"], 0) + 1
        tool_names = [
            (e["payload"].get("call") or {}).get("name")
            for e in events if e["type"] == "tool.started"
        ]
        report = {
            "status": task["status"],
            "result": (task.get("result") or "")[:300],
            "error": task.get("error"),
            "approvals_auto_approved": approvals_seen,
            "tools_used": tool_names,
            "notes_txt_exists": (root / "notes.txt").exists(),
            "notes_txt_content": (root / "notes.txt").read_text()[:100] if (root / "notes.txt").exists() else None,
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
