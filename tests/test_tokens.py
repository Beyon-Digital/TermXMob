from __future__ import annotations

import json
import stat

from termx.config import config_dir
from termx.tokens import SCOPES, TokenStore


def test_issue_check_revoke() -> None:
    store = TokenStore()
    raw = store.issue()
    assert len(raw) >= 32
    assert store.check(raw) == list(SCOPES)
    assert store.check("not-a-token") is None
    assert store.check(None) is None
    public = store.list_public()
    assert len(public) == 1
    assert set(public[0]) == {"id", "scopes", "created_at"}
    assert "hash" not in public[0]
    assert raw not in json.dumps(public)
    path = config_dir() / "tokens.json"
    on_disk = path.read_text(encoding="utf-8")
    assert raw not in on_disk
    assert json.loads(on_disk)["tokens"][0]["hash"] not in public[0].values()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    token_id = public[0]["id"]
    assert store.revoke(token_id) is True
    assert store.check(raw) is None
    assert store.list_public() == []
    assert store.revoke(token_id) is False


def test_custom_scopes_and_wrong_token() -> None:
    store = TokenStore()
    raw = store.issue(["terminal", "tunnel-admin", "unknown"])
    assert store.check(raw) == ["terminal", "tunnel-admin"]
    other = store.issue(["settings-admin"])
    assert store.check(other) == ["settings-admin"]
    assert store.check(raw + "x") is None
