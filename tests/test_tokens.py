from __future__ import annotations

import json
import os
import stat
import time

from termx.config import config_dir
from termx.tokens import (
    DEFAULT_DEVICE_SCOPES,
    LEGACY_SCOPE_MAP,
    SCOPES,
    TokenStore,
)


def test_issue_check_revoke() -> None:
    store = TokenStore()
    raw = store.issue()
    assert len(raw) >= 32
    assert store.check(raw) == list(DEFAULT_DEVICE_SCOPES)
    assert "host-admin" not in store.check(raw)
    assert store.check("not-a-token") is None
    assert store.check(None) is None
    public = store.list_public()
    assert len(public) == 1
    assert set(public[0]) == {
        "id",
        "scopes",
        "device_name",
        "created_at",
        "last_seen",
        "expires_at",
        "expired",
        "legacy",
    }
    assert "hash" not in public[0]
    assert raw not in json.dumps(public)
    path = config_dir() / "tokens.json"
    on_disk = path.read_text(encoding="utf-8")
    assert raw not in on_disk
    assert json.loads(on_disk)["tokens"][0]["hash"] not in public[0].values()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    token_id = public[0]["id"]
    assert store.revoke(token_id) is True
    assert store.check(raw) is None
    assert store.list_public() == []
    assert store.revoke(token_id) is False


def test_custom_scopes_and_wrong_token() -> None:
    store = TokenStore()
    raw = store.issue(["files-read", "agent-view", "unknown"])
    assert store.check(raw) == ["files-read", "agent-view"]
    other = store.issue(["terminal-control"])
    assert store.check(other) == ["terminal-control"]
    assert store.check(raw + "x") is None


def test_issue_maps_legacy_scope_names_without_gain() -> None:
    """A v1 client asking for old scope names gets exact v2 equivalents."""
    store = TokenStore()
    raw = store.issue(["terminal", "tunnel-admin", "unknown"])
    scopes = store.check(raw)
    expected = list(LEGACY_SCOPE_MAP["terminal"]) + ["network-manage", "machine-view"]
    assert scopes == expected
    assert "host-admin" not in scopes


def test_v1_store_migrates_scopes_and_flags_legacy() -> None:
    """A tokens.json written by a v1 host migrates on load, preserving authority."""
    path = config_dir() / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "tokens": [
                    {"id": "t1", "hash": "h1", "scopes": ["terminal"]},
                    {"id": "t2", "hash": "h2", "scopes": ["screen-view"]},
                    {"id": "t3", "hash": "h3"},
                ]
            }
        ),
        encoding="utf-8",
    )
    store = TokenStore()
    public = {item["id"]: item for item in store.list_public()}
    assert public["t1"]["scopes"] == list(LEGACY_SCOPE_MAP["terminal"]) + ["machine-view"]
    assert public["t1"]["legacy"] is True
    assert public["t2"]["scopes"] == ["desktop-view", "machine-view"]
    assert public["t2"]["legacy"] is True
    # Empty v1 scopes still gain machine-view (any v1 credential reached it) — not host-admin.
    assert public["t3"]["scopes"] == ["machine-view"]
    for item in public.values():
        assert "host-admin" not in item["scopes"]


def test_token_expiry_and_last_seen() -> None:
    store = TokenStore()
    live = store.issue(["agent-view"])
    assert store.check(live) == ["agent-view"]
    public = store.list_public()
    assert public[0]["last_seen"] is not None
    assert public[0]["expired"] is False

    dying = store.issue(["agent-view"], expires_at=time.time() - 1)
    assert store.check(dying) is None
    entries = {item["id"]: item for item in store.list_public()}
    dead = next(item for item in entries.values() if item["expires_at"] is not None)
    assert dead["expired"] is True


def test_update_scopes_explicit_repair_path() -> None:
    store = TokenStore()
    raw = store.issue(["files-read"], device_name=" phone ")
    token_id = store.list_public()[0]["id"]
    assert store.list_public()[0]["device_name"] == "phone"

    device = store.update_scopes(token_id, ["files-read", "git-read", "bogus"])
    assert device is not None
    assert device["scopes"] == ["files-read", "git-read"]
    assert store.check(raw) == ["files-read", "git-read"]
    assert store.list_public()[0]["legacy"] is False
    assert store.update_scopes("missing", ["files-read"]) is None


def test_update_device_name_and_expiry() -> None:
    store = TokenStore()
    store.issue(device_name="old")
    token_id = store.list_public()[0]["id"]
    device = store.update_device(token_id, device_name="tablet", expires_at=123.0)
    assert device is not None
    assert device["device_name"] == "tablet"
    assert device["expires_at"] == 123.0
    assert store.update_device("missing", device_name="x") is None


def test_default_scopes_cover_everything_but_host_admin() -> None:
    assert set(DEFAULT_DEVICE_SCOPES) == set(SCOPES) - {"host-admin"}
