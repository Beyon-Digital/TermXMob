"""Opt-in smoke test against a real agent-desktop. Never part of CI.

Set TERMX_REAL_AGENT_DESKTOP to the absolute path of the pinned 1.2.0 program. It runs only `version`, `status` and
`list-apps`: no snapshot of a private window, no click and no typing.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from termx.desktop import agent_desktop as ad

REAL = os.environ.get("TERMX_REAL_AGENT_DESKTOP")
pytestmark = pytest.mark.skipif(not REAL, reason="set TERMX_REAL_AGENT_DESKTOP to run against the real program")


def test_the_real_program_answers_version_status_and_list_apps(tmp_path: Path) -> None:
    async def run() -> None:
        client = ad.AgentDesktopClient(home_root=tmp_path / "desktop-control", setting=REAL)
        status = await client.status(refresh=True)
        assert status.installed and status.version == ad.PINNED_VERSION, status.issues
        envelope = await client.run(ad.req_list_apps())
        assert envelope.ok and isinstance(envelope.data, dict) and isinstance(envelope.data.get("apps"), list)

    asyncio.run(run())
