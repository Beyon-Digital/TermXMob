"""Provider API keys: validated on save and never forwarded to a different server."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_agent import FakeAdapter, build_manager


def _save(manager, **overrides: Any) -> dict[str, Any]:
    values = dict(
        provider_id="fake", kind="openai-compatible", name="Fake",
        base_url="http://127.0.0.1:9999/v1", model="fake-model", capabilities=["shell"],
    )
    values.update(overrides)
    return manager.save_provider(**values)


def test_api_keys_are_trimmed_and_control_characters_rejected(tmp_path: Path) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    _save(manager, api_key="  sk-rotated\n")
    assert manager.credentials.get("fake") == "sk-rotated"
    for bad in ("sk-a\nb", "sk-a b", "sk-a\x00b", "sk-\tb"):
        with pytest.raises(ValueError, match="API key"):
            _save(manager, api_key=bad)
    assert manager.credentials.get("fake") == "sk-rotated"
    store.close()


def test_changing_the_server_drops_the_stored_key_unless_a_new_one_is_given(tmp_path: Path) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    # Same origin, new path: the key stays.
    provider = _save(manager, base_url="http://127.0.0.1:9999/v2")
    assert provider["secret_configured"] is True and manager.credentials.get("fake") == "test-key"
    # New origin without a new key: the key must not travel to the new host.
    provider = _save(manager, base_url="http://attacker.example/v1")
    assert provider["secret_configured"] is False
    assert not manager.credentials.get("fake")
    # New origin with a new key: the new key is kept.
    provider = _save(manager, base_url="http://other.example/v1", api_key="sk-new")
    assert provider["secret_configured"] is True and manager.credentials.get("fake") == "sk-new"
    store.close()
