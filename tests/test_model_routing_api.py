"""Provider discovery, model routing and task usage through GraphQL."""
from __future__ import annotations

import pytest

from _gql import data, err_status, gql
from test_plan_mode import _call
from test_plan_mode_api import HEADERS, _client
from termx.agent.providers import ProviderError

_PROVIDERS = (
    "{ agent_providers { id models selected_models model_catalog model_overrides "
    "catalog_refreshed_at catalog_error wire } }"
)
_REFRESH = (
    "mutation($id: String!) { refresh_provider_models(provider_id: $id) "
    "{ id models model_catalog catalog_error catalog_refreshed_at } }"
)
_SAVE = (
    "mutation($input: AgentProviderInput!) { save_agent_provider(input: $input) "
    "{ id models selected_models model_overrides wire } }"
)
_SET_ROUTING = "mutation($input: ModelRoutingInput!) { set_model_routing(input: $input) }"
_ROUTING = "{ model_routing }"
_TEST = "mutation($id: String!, $billed: Boolean) { test_agent_provider(provider_id: $id, billed: $billed) }"

CATALOG = {"m-1": {"context_window": 32768, "max_output": None, "price_in": None, "price_out": None,
                   "capabilities": ["tools"], "source": "ollama", "fetched_at": 1.0}}


def test_refresh_stores_the_catalog_and_a_failed_refresh_keeps_it(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    outcomes = [CATALOG, ProviderError("Could not reach provider", network=True)]

    async def fetch(_provider):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    state.agent.catalogue._fetch = fetch
    with client:
        listed = data(client, _PROVIDERS, "agent_providers", None, HEADERS)
        assert listed[0]["models"] == ["fake-model"] and listed[0]["model_catalog"] == {}
        assert listed[0]["catalog_refreshed_at"] is None and listed[0]["wire"] == "responses"
        ok = data(client, _REFRESH, "refresh_provider_models", {"id": "fake"}, HEADERS)
        assert ok["model_catalog"]["m-1"]["context_window"] == 32768
        assert ok["catalog_error"] is None and ok["catalog_refreshed_at"]
        failed = data(client, _REFRESH, "refresh_provider_models", {"id": "fake"}, HEADERS)
        assert "Could not reach" in failed["catalog_error"]
        assert failed["model_catalog"]["m-1"]["context_window"] == 32768  # kept
        assert err_status(client, _REFRESH, {"id": "missing"}, HEADERS) == 404


def test_reads_never_trigger_discovery(tmp_path, monkeypatch):
    state, _adapter, client = _client(tmp_path, monkeypatch, [])
    calls = []

    async def fetch(_provider):
        calls.append(1)
        return {}

    state.agent.catalogue._fetch = fetch
    with client:
        data(client, _PROVIDERS, "agent_providers", None, HEADERS)
        data(client, _ROUTING, "model_routing", None, HEADERS)
    assert calls == []


def test_save_provider_accepts_overrides_and_rejects_bad_ones(tmp_path, monkeypatch):
    _state, _adapter, client = _client(tmp_path, monkeypatch, [])
    base = {"id": "fake", "kind": "openai-compatible", "name": "Fake", "base_url": "http://127.0.0.1:9999/v1",
            "model": "fake-model"}
    with client:
        saved = data(client, _SAVE, "save_agent_provider", {"input": {
            **base, "model_meta": {"fake-model": {"context_window": 4096}}, "wire": "responses"}}, HEADERS)
        assert saved["model_overrides"] == {"fake-model": {"context_window": 4096}}
        for bad in ({"model_meta": {"fake-model": {"context_window": 0}}}, {"wire": "soap"}):
            assert err_status(client, _SAVE, {"input": {**base, **bad}}, HEADERS) == 400
        chat = data(client, _SAVE, "save_agent_provider", {"input": {**base, "wire": "chat"}}, HEADERS)
        assert chat["wire"] == "chat"


def test_set_model_routing_validates_and_model_routing_reads_it_back(tmp_path, monkeypatch):
    _state, _adapter, client = _client(tmp_path, monkeypatch, [])
    routing = {"worker": {"provider_id": "fake", "model": "fake-model", "fallbacks": []}}
    with client:
        assert data(client, _ROUTING, "model_routing", None, HEADERS) == {}
        stored = data(client, _SET_ROUTING, "set_model_routing", {"input": {"routing": routing}}, HEADERS)
        assert stored == routing
        assert data(client, _ROUTING, "model_routing", None, HEADERS) == routing
        bad = {"worker": {"provider_id": "fake", "model": "nope"}}
        assert err_status(client, _SET_ROUTING, {"input": {"routing": bad}}, HEADERS) == 400
        assert data(client, _ROUTING, "model_routing", None, HEADERS) == routing  # unchanged
        # Cleared by saving an empty table.
        assert data(client, _SET_ROUTING, "set_model_routing", {"input": {"routing": {}}}, HEADERS) == {}


def test_provider_test_is_free_by_default_and_billed_on_request(tmp_path, monkeypatch):
    state, adapter, client = _client(tmp_path, monkeypatch, [])
    seen = []

    async def test(*, billed=False):
        seen.append(billed)
        return "OK"

    adapter.test = test
    with client:
        data(client, _TEST, "test_agent_provider", {"id": "fake"}, HEADERS)
        data(client, _TEST, "test_agent_provider", {"id": "fake", "billed": True}, HEADERS)
    assert seen == [False, True]


def test_agent_task_exposes_usage(tmp_path, monkeypatch):
    from test_plan_mode_api import _CREATE, _input
    from test_agent import wait_for_status

    state, _adapter, client = _client(tmp_path, monkeypatch, [("Done.", [])])
    with client:
        created = data(client, _CREATE, "create_agent_task", {"input": _input(tmp_path, mode="ask")}, HEADERS)
        client.portal.call(wait_for_status, state.agent_store, created["id"], "completed")
        usage = data(client, "query($id: String!) { agent_task(task_id: $id) { usage { tokens cost_usd } } }",
                     "agent_task", {"id": created["id"]}, HEADERS)["usage"]
        assert usage == {"tokens": 0, "cost_usd": None}
