"""Settings control for the web_search API key: status, set, clear, scope gating and that the key is never returned."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status
from termx.agent.secrets import WEB_SEARCH_CREDENTIAL, CredentialStore
from termx.agent.tools import web
from termx.app import AppState, create_app

STATUS = "{ web_search_status { available provider source store_available custom_endpoint error } }"
SET = "mutation($k: String!) { set_web_search_key(key: $k) { available provider source error } }"
CLEAR = "mutation { clear_web_search_key { available source } }"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("TERMX_WEB_SEARCH_PROVIDER", "TERMX_WEB_SEARCH_URL", "TERMX_AI_WEB_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    web.use_credentials(None)
    yield
    web.use_credentials(None)


def boot():
    memory: dict[str, str] = {}
    state = AppState(passcode="host-secret", credentials=CredentialStore(memory=memory))
    return state, create_app(state), memory


def headers(token):
    return {"x-termx-passcode": token}


def test_status_set_and_clear_round_trip_without_returning_the_key():
    state, app, memory = boot()
    with TestClient(app, base_url="http://localhost") as client:
        first = data(client, STATUS, "web_search_status", headers=headers("host-secret"))
        assert first["available"] is False and first["source"] is None and first["provider"] == "brave"
        assert first["store_available"] is True and first["custom_endpoint"] is False
        saved = data(client, SET, "set_web_search_key", {"k": "BSA-secret-value"}, headers=headers("host-secret"))
        assert saved == {"available": True, "provider": "brave", "source": "credential_store", "error": None}
        assert memory[WEB_SEARCH_CREDENTIAL] == "BSA-secret-value"
        assert "BSA-secret-value" not in str(data(client, STATUS, headers=headers("host-secret")))
        cleared = data(client, CLEAR, "clear_web_search_key", headers=headers("host-secret"))
        assert cleared == {"available": False, "source": None}
        assert WEB_SEARCH_CREDENTIAL not in memory


def test_a_key_from_the_host_environment_is_reported_and_survives_clearing(monkeypatch):
    state, app, memory = boot()
    monkeypatch.setenv("TERMX_AI_WEB_SEARCH_API_KEY", "from-env")
    with TestClient(app, base_url="http://localhost") as client:
        assert data(client, STATUS, "web_search_status", headers=headers("host-secret"))["source"] == "environment"
        after = data(client, CLEAR, "clear_web_search_key", headers=headers("host-secret"))
        assert after == {"available": True, "source": "environment"}


def test_an_unknown_search_provider_is_not_available_and_says_why(monkeypatch):
    state, app, memory = boot()
    monkeypatch.setenv("TERMX_WEB_SEARCH_PROVIDER", "no-such-engine")
    memory[WEB_SEARCH_CREDENTIAL] = "key"
    with TestClient(app, base_url="http://localhost") as client:
        status = data(client, STATUS, "web_search_status", headers=headers("host-secret"))
    assert status["available"] is False and "no-such-engine" in status["error"]


@pytest.mark.parametrize("key", ["", "   ", "two words", "line\nbreak", "x" * 600])
def test_a_malformed_key_is_refused_and_nothing_is_stored(key):
    state, app, memory = boot()
    with TestClient(app, base_url="http://localhost") as client:
        assert err_status(client, SET, {"k": key}, headers=headers("host-secret")) == 400
    assert WEB_SEARCH_CREDENTIAL not in memory


def test_the_key_can_only_be_managed_with_the_ai_settings_scope():
    state, app, memory = boot()
    viewer = state.tokens.issue(["machine-view", "agent-view"], device_name="watch")
    with TestClient(app, base_url="http://localhost") as client:
        for query, variables in ((STATUS, None), (SET, {"k": "abc"}), (CLEAR, None)):
            assert err_status(client, query, variables, headers={}) == 401
            assert err_status(client, query, variables, headers=headers(viewer)) == 403
    assert WEB_SEARCH_CREDENTIAL not in memory


def test_saving_a_key_makes_the_search_tool_available_at_once():
    state, app, memory = boot()
    web.use_credentials(state.agent.credentials)
    assert web.search_available() is False
    with TestClient(app, base_url="http://localhost") as client:
        data(client, SET, "set_web_search_key", {"k": "key-1"}, headers=headers("host-secret"))
        assert web.search_available() is True
        data(client, CLEAR, "clear_web_search_key", headers=headers("host-secret"))
        assert web.search_available() is False
