"""Provider/engine setup defects found by the audit: URL validation, keyless Anthropic, default model, secret-bearing env."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from test_agent import FakeAdapter, build_manager
from termx.agent.discovery import discover_models
from termx.agent.manager import _resolve_model
from termx.agent.providers import ProviderError
from termx.engines.env import strip_host_secrets


def _save(manager, **overrides: Any) -> dict[str, Any]:
    values = dict(
        provider_id="fake", kind="openai-compatible", name="Fake",
        base_url="http://127.0.0.1:9999/v1", model="fake-model", capabilities=["shell"],
    )
    values.update(overrides)
    return manager.save_provider(**values)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://", "include a host"),
        ("http:///v1", "include a host"),
        ("https://user:pw@api.example.com/v1", "username or password"),
        ("https://:pw@api.example.com/v1", "username or password"),
        ("https://api.example.com/v1?key=1", "query string"),
        ("https://api.example.com/v1#frag", "query string"),
        ("https://api.example.com:notaport/v1", "not a valid address"),
        ("ftp://api.example.com/v1", "HTTP or HTTPS"),
        ("api.example.com/v1", "HTTP or HTTPS"),
    ],
)
def test_base_urls_with_no_host_credentials_or_query_are_rejected(tmp_path: Path, url: str, message: str) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    before = manager.store.get_provider("fake")["base_url"]  # build_manager seeds this provider
    with pytest.raises(ValueError, match=message):
        _save(manager, base_url=url)
    assert manager.store.get_provider("fake")["base_url"] == before
    store.close()


def test_a_base_url_is_trimmed_and_local_servers_still_work(tmp_path: Path) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    assert _save(manager, base_url="  http://localhost:11434/v1  ")["base_url"] == "http://localhost:11434/v1"
    assert _save(manager, base_url="http://192.168.1.20:8000/v1")["base_url"] == "http://192.168.1.20:8000/v1"
    store.close()


def test_an_anthropic_account_with_no_key_explains_itself() -> None:
    provider = {"kind": "anthropic", "base_url": "https://api.anthropic.com"}
    with pytest.raises(ProviderError, match="No API key is saved") as raised:
        asyncio.run(discover_models(provider, ""))
    assert raised.value.status_code == 401  # not a bare TypeError that surfaces as just "TypeError"


def test_the_default_model_is_one_that_can_chat() -> None:
    discovered = ["dall-e-3", "text-embedding-3-small", "tts-1", "whisper-1", "gpt-4.1", "gpt-4o-mini"]
    assert _resolve_model({"models": discovered}, None) == "gpt-4.1"
    # an explicit choice is honoured, even of a non-chat model
    assert _resolve_model({"models": discovered}, "tts-1") == "tts-1"
    # nothing chat-capable: fall back to the first rather than failing
    assert _resolve_model({"models": ["whisper-1", "tts-1"]}, None) == "whisper-1"
    # ordinary ids that merely contain a keyword are not mistaken for non-chat models
    assert _resolve_model({"models": ["gpt-4o-audio-preview", "claude-imagine-1"]}, None) == "claude-imagine-1"


def test_engine_children_do_not_inherit_host_secrets() -> None:
    env = {
        "PATH": "/usr/bin", "TERMX_PASSCODE": "p", "TERMX_TOKEN": "t",
        "TERMX_AI_OPENAI_API_KEY": "sk-1", "TERMX_AI_WEB_SEARCH_API_KEY": "sk-2", "termx_ai_x_api_key": "sk-3",
        "TERMX_AI_NOTES": "kept", "OTHER": "kept",
    }
    cleaned = strip_host_secrets(dict(env))
    assert set(cleaned) == {"PATH", "TERMX_AI_NOTES", "OTHER"}


def test_every_engine_spawn_environment_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    from termx.engines.acp import _child_env
    from termx.engines.jsonrpc import platform_spawn_env

    monkeypatch.setenv("TERMX_AI_ANTHROPIC_API_KEY", "sk-live")
    monkeypatch.setenv("TERMX_PASSCODE", "secret")
    for env in (_child_env(), platform_spawn_env()):
        assert not any(name.startswith("TERMX_AI_") and name.endswith("_API_KEY") for name in env)
        assert "TERMX_PASSCODE" not in env
    assert os.environ["TERMX_AI_ANTHROPIC_API_KEY"] == "sk-live"  # the host keeps its own


def test_provider_http_errors_name_the_likely_cause() -> None:
    from termx.agent.providers import _provider_http_error as explain

    assert "rejected the API key (401)" in explain(401, '{"error": "unauthorized"}')
    assert "It said: unauthorized" in explain(401, '{"error": "unauthorized"}')
    assert "belongs to this server" in explain(403, '{"error": {"message": "bad key"}}')
    assert "ends with its API path" in explain(404, "<html>Not Found</html>")
    assert "It said" not in explain(404, "<html>Not Found</html>")  # an HTML page is not quoted back
    assert "rejected the request (400). It said: tools are not supported" in explain(400, '{"error": {"message": "tools are not supported"}}')
    assert "supports them" in explain(400, "")  # no message from the provider: keep the tools hint
    assert explain(500, "") == "Provider request failed (500): The provider returned an error."
    assert "rate limited" in explain(429, "{}")


def _keyless_manager(tmp_path: Path):
    manager, _store = build_manager(tmp_path, FakeAdapter())  # its credentials are an in-memory store
    return manager


def _save_local(manager, **extra):
    return manager.save_provider(
        provider_id="local", kind="openai-compatible", name="Ollama", base_url="http://127.0.0.1:11434/v1",
        model="llama3", capabilities=["shell"], **extra,
    )


def test_a_server_that_needs_no_key_counts_as_connected_without_one(tmp_path: Path) -> None:
    manager = _keyless_manager(tmp_path)
    assert _save_local(manager)["secret_configured"] is False  # no key and not marked keyless: not usable yet
    row = _save_local(manager, key_optional=True)
    assert row["secret_configured"] is True and row["key_saved"] is False and row["key_optional"] is True
    assert manager.list_providers()[-1]["secret_configured"] is True
    assert _save_local(manager)["key_optional"] is True  # a later save that does not mention it keeps the choice
    assert _save_local(manager, key_optional=False)["secret_configured"] is False


def test_only_an_openai_compatible_server_can_skip_the_key(tmp_path: Path) -> None:
    manager = _keyless_manager(tmp_path)
    for kind, url in (("openai", "https://api.openai.com/v1"), ("anthropic", "https://api.anthropic.com")):
        with pytest.raises(ValueError, match="without an API key"):
            manager.save_provider(provider_id="x", kind=kind, name="X", base_url=url, model="m",
                                  capabilities=["shell"], key_optional=True)


def test_a_saved_key_can_be_cleared_and_an_environment_key_is_reported(tmp_path: Path, monkeypatch) -> None:
    manager = _keyless_manager(tmp_path)
    saved = _save_local(manager, api_key="sk-local-1")
    assert saved["secret_configured"] is True and saved["key_source"] == "credential_store"
    cleared = _save_local(manager, clear_api_key=True)
    assert cleared["secret_configured"] is False and cleared["key_source"] is None
    assert manager.credentials.get("local") is None
    _save_local(manager, api_key="sk-local-2")
    monkeypatch.setenv("TERMX_AI_LOCAL_API_KEY", "from-env")
    still = _save_local(manager, clear_api_key=True)
    assert still["secret_configured"] is True and still["key_source"] == "environment"  # the variable wins; the screen says so
    assert manager.credentials._memory.get("local") is None  # the saved copy is gone


def test_changing_the_server_address_says_the_saved_key_was_removed(tmp_path: Path) -> None:
    manager = _keyless_manager(tmp_path)
    _save_local(manager, api_key="sk-local-1")
    moved = manager.save_provider(provider_id="local", kind="openai-compatible", name="Ollama",
                                  base_url="http://10.0.0.5:11434/v1", model="llama3", capabilities=["shell"])
    assert moved["secret_configured"] is False and "key was removed" in moved["notice"]
    again = manager.save_provider(provider_id="local", kind="openai-compatible", name="Ollama",
                                  base_url="http://10.0.0.5:11434/v1", model="llama3", capabilities=["shell"])
    assert "notice" not in again
