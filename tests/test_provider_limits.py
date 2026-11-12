"""Optional provider controls persist without imposing request defaults."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from test_authorization import client_fixture
from termx.agent.provider_limits import clean_provider_limits
from termx.agent.providers import OpenAIChatAdapter, OpenAIResponsesAdapter
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore


UNLIMITED = {"max_output_tokens": None, "request_timeout_s": None}


def test_existing_provider_database_migrates_without_enabling_limits(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE providers (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            base_url TEXT NOT NULL, model TEXT NOT NULL, capabilities TEXT NOT NULL,
            secret_configured INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            updated_at REAL NOT NULL)""")
        db.execute("INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("existing", "openai-compatible", "Existing", "https://example.invalid/v1", "m", '["shell"]', 1, 1, 1))
    store = AgentStore(path, tmp_path / "artifacts")
    try:
        provider = store.get_provider("existing")
        assert provider["limits"] == UNLIMITED
        assert provider["secret_configured"] is True
        assert provider["model"] == "m"
    finally:
        store.close()


def test_provider_graphql_limits_are_optional_preserved_on_rotation_and_clearable(tmp_path: Path) -> None:
    _, state, client, owner = client_fixture(tmp_path)
    state.credentials = CredentialStore(memory={})
    state.agent.credentials = state.credentials

    def save(**fields):
        reply = client.post("/graphql", headers=owner, json={
            "query": "mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id limits model_overrides capabilities}}",
            "variables": {"input": {
                "id": "optional", "kind": "openai-compatible", "name": "Optional",
                "base_url": "https://example.invalid/v1", "model": "m", **fields,
            }},
        })
        assert reply.status_code == 200
        return reply.json()

    created = save(api_key="first-key", model_meta={"m": {"context_window": 8000}})
    assert not created.get("errors"), created
    assert created["data"]["save_agent_provider"]["limits"] == UNLIMITED
    chosen = {"max_output_tokens": 12000, "request_timeout_s": 0.25}
    limited = save(limits=chosen)
    assert limited["data"]["save_agent_provider"]["limits"] == chosen
    rotated = save(api_key="second-key")
    assert rotated["data"]["save_agent_provider"]["limits"] == chosen
    assert rotated["data"]["save_agent_provider"]["model_overrides"] == {"m": {"context_window": 8000}}
    assert state.credentials.get("optional") == "second-key"
    inventory = client.post("/graphql", headers=owner, json={"query": "{agent_providers{id limits}}"}).json()
    assert next(row for row in inventory["data"]["agent_providers"] if row["id"] == "optional")["limits"] == chosen
    rejected = save(limits={"max_output_tokens": 0}, api_key="must-not-be-written")
    assert rejected["errors"][0]["extensions"]["http_status"] == 400
    assert state.credentials.get("optional") == "second-key"
    assert state.agent_store.get_provider("optional")["limits"] == chosen
    cleared = save(limits={})
    assert cleared["data"]["save_agent_provider"]["limits"] == UNLIMITED
    assert state.agent_store.list_tasks() == []


@pytest.mark.parametrize("limits", [
    {"max_output_tokens": True}, {"max_output_tokens": 1.5}, {"max_output_tokens": -1},
    {"request_timeout_s": 0}, {"request_timeout_s": False}, {"request_timeout_s": float("nan")},
    {"request_timeout_s": float("inf")}, {"request_timeout_s": 10**1000}, {"request_timeout_s": "10"}, {"max_steps": 1},
])
def test_provider_limits_reject_invalid_controls(limits) -> None:
    with pytest.raises(ValueError, match="provider"):
        clean_provider_limits(limits)


@pytest.mark.parametrize("adapter_type,limit_key", [
    (OpenAIResponsesAdapter, "max_output_tokens"),
    (OpenAIChatAdapter, "max_completion_tokens"),
])
@pytest.mark.parametrize("output_limit", [None, 50000])
def test_turn_and_plan_send_only_an_explicit_output_limit(adapter_type, limit_key, output_limit) -> None:
    async def run() -> None:
        payloads = []
        def respond(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            if adapter_type is OpenAIChatAdapter:
                return httpx.Response(200, json={"id": "r", "choices": [{"finish_reason": "stop", "message": {"content": "Done"}}]})
            return httpx.Response(200, json={"id": "r", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), timeout=None) as client:
            adapter = adapter_type(base_url="https://api.openai.com/v1", model="m", api_key="fake",
                                   capabilities=["functions"], client=client, max_output_tokens=output_limit)
            assert adapter.timeout_s is None
            await adapter.turn(prompt="Work", cwd="/work", manifest={})
            await adapter.plan("Plan", "/work", {})
        for payload in payloads:
            if output_limit is None:
                assert not {"max_output_tokens", "max_tokens", "max_completion_tokens"}.intersection(payload)
            else:
                assert payload[limit_key] == output_limit
    asyncio.run(run())
