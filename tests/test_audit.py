from __future__ import annotations

import json
import stat

from termx.audit import log_event, read_events
from termx.config import config_dir


def test_log_and_drop_pty_fields() -> None:
    log_event(
        "command.run",
        session_id="abc",
        data="secret-pty",
        command_body="rm -rf /",
        pty="output-bytes",
        output="captured",
        command_id="cmd-1",
    )
    log_event("pairing.issue", token_id="tok")
    events = read_events(limit=10)
    assert len(events) == 2
    first, second = events
    assert first["kind"] == "command.run"
    assert first["session_id"] == "abc"
    assert first["command_id"] == "cmd-1"
    assert "ts" in first
    for key in ("data", "command_body", "pty", "output"):
        assert key not in first
    assert second["kind"] == "pairing.issue"
    assert second["token_id"] == "tok"
    path = config_dir() / "audit.jsonl"
    dumped = path.read_text(encoding="utf-8")
    assert "secret-pty" not in dumped
    assert "rm -rf /" not in dumped
    assert "output-bytes" not in dumped
    assert "captured" not in dumped
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in dumped.splitlines() if line]
    assert [item["kind"] for item in lines] == ["command.run", "pairing.issue"]
    assert read_events(limit=1) == [second]
