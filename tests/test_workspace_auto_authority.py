"""Turning Auto on from a workspace turn needs agent-control, not just agent-run."""
from __future__ import annotations

import pytest

from test_durable_workspace import anyio_backend, session, workspace  # noqa: F401


@pytest.mark.anyio
async def test_auto_turn_needs_agent_control(workspace):  # noqa: F811
    service, owner, _path = workspace
    row = session(workspace)
    service.state.identity.set_scopes(owner.id, ["agent-view", "agent-run"])
    with pytest.raises(PermissionError):
        await service.send(owner, row["id"], prompt="go", request_id="auto-denied-1", auto={"enabled": True})
    assert service.state.agent.calls == 0
    # The same principal can still send a turn without Auto.
    await service.send(owner, row["id"], prompt="go", request_id="plain-turn-1")
    assert service.state.agent.calls == 1
