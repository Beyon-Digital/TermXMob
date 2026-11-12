"""Copilot transport fixtures only: no paid inference or real host profiles."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from termx.agent.providers import OpenAIChatAdapter, OpenAIResponsesAdapter
from termx.agent.secrets import CredentialStore
from termx.data.copilot import DataCopilot, build_copilot_router
from termx.data.service import DataService


class InferenceHost:
    def __init__(self, handler, wire="responses"):
        self.provider = {"id": "fixture-provider", "name": "Fixture inference", "kind": "openai-compatible",
                         "base_url": "https://inference.invalid/v1", "model": "fixture-model", "models": ["fixture-model"],
                         "capabilities": ["functions", "shell"], "wire": wire, "limits": {}, "updated_at": "1"}
        self.store = SimpleNamespace(get_provider=lambda pid: self.provider if pid == self.provider["id"] else None)
        self.credentials = CredentialStore(memory={"fixture-provider": "fixture-private-key-value"})
        self.handler = handler
        self.clients = []
        self.payloads = []

    def list_providers(self):
        return [self.provider]

    def _adapter(self, provider):
        def transport(request):
            self.payloads.append(json.loads(request.content))
            return self.handler(request, len(self.payloads))
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.clients.append(client)
        cls = OpenAIChatAdapter if provider["wire"] == "chat" else OpenAIResponsesAdapter
        return cls(base_url=provider["base_url"], model=provider["model"], api_key=self.credentials.get(provider["id"]),
                   capabilities=provider["capabilities"], client=client)


def response(text="Observed fixture answer", calls=None):
    return httpx.Response(200, json={"id": "fixture-response", "status": "completed", "output":
        ([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}] if text else []) +
        [{"type": "function_call", "call_id": f"call-{i}", "name": name, "arguments": json.dumps(args)}
         for i, (name, args) in enumerate(calls or [])]})


def setup(tmp_path, handler=lambda _r, _n: response(), wire="responses"):
    def principal(pid):
        return SimpleNamespace(id=pid, display_name=pid.title(), scopes=("files-read",), policy_version=1, authority_session_id=None)
    owner, editor, other = [principal(pid) for pid in ("owner", "editor", "other")]
    principals = {p.id: p for p in (owner, editor, other)}
    host = InferenceHost(handler, wire)
    roles = {"owner": "owner", "other": "operator", "editor": "operator"}
    state = SimpleNamespace(agent=host, credentials=host.credentials,
        identity=SimpleNamespace(principal_by_id=lambda pid: principals.get(pid), current_principal=lambda p: principals.get(p.id)),
        authorization=SimpleNamespace(role=lambda pid: roles.get(pid), require_principal=lambda *_a, **_kw: None))
    service = DataService(state, tmp_path / "metadata")
    workspace = service.create_workspace(owner, "Data fixture")
    wid = workspace["id"]
    service.store.create("member", wid, owner.id, {"principal_id": editor.id, "role": "editor"},
                         identifier=service.membership_id(wid, editor.id))
    service.store.create("member", wid, owner.id, {"principal_id": other.id, "role": "owner"},
                         identifier=service.membership_id(wid, other.id))
    copilot = service.copilot
    return SimpleNamespace(service=service, copilot=copilot, owner=owner, editor=editor, other=other,
                           wid=wid, host=host, roles=roles, principals=principals)


def configure(env, **extra):
    return env.copilot.save_settings(env.owner, env.wid, {"revision": 0, "provider_id": "fixture-provider",
        "model": "fixture-model", **extra})


async def finish(env, request=None, actor=None):
    actor = actor or env.owner
    run = await env.copilot.send(actor, env.wid, {"request_id": "fixture-request", "message": "Help me understand this data", **(request or {})})
    worker = env.copilot._workers.get(run["id"])
    if worker:
        await worker
    return env.copilot.get_run(actor, env.wid, run["id"])


async def cleanup(env):
    await env.copilot.close()
    if env.service._runtime is not None:
        await env.service.runtime.close()
    await asyncio.gather(*(client.aclose() for client in env.host.clients))


def test_no_inference_until_send_and_explicit_host_binding(tmp_path):
    env = setup(tmp_path)
    initial = env.copilot.snapshot(env.owner, env.wid)
    assert not initial["settings"]["configured"] and not env.host.payloads
    assert initial["providers"] == [{"id": "fixture-provider", "name": "Fixture inference", "model": "fixture-model", "models": ["fixture-model"]}]
    with pytest.raises(HTTPException) as denied:
        env.copilot.save_settings(env.other, env.wid, {"revision": 0, "provider_id": "fixture-provider", "model": "fixture-model"})
    assert denied.value.status_code == 403
    settings = configure(env)
    assert settings["configured"] and not settings["share_with_team"]
    assert not env.host.payloads
    with pytest.raises(HTTPException) as denied:
        asyncio.run(env.copilot.send(env.editor, env.wid, {"request_id": "x", "message": "Read data"}))
    assert denied.value.status_code == 403


@pytest.mark.parametrize("wire", ["responses", "chat"])
def test_real_adapter_loop_scoped_tools_and_secret_free_payload(tmp_path, wire):
    def handler(_request, turn):
        if wire == "chat":
            if turn == 1:
                return httpx.Response(200, json={"id": "chat-1", "choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                        "name": "list_connections", "arguments": "{}"}}]}}]})
            return httpx.Response(200, json={"id": "chat-2", "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "No authorized connections were found"}}]})
        return response(text="", calls=[("list_connections", {})]) if turn == 1 else response("No authorized connections were found")
    async def scenario():
        env = setup(tmp_path, handler, wire)
        configure(env)
        run = await finish(env, {"message": "fixture-private-key-value"})
        assert run["status"] == "completed" and len(env.host.payloads) == 2
        encoded = json.dumps(env.host.payloads)
        assert "fixture-private-key-value" not in encoded
        for payload in env.host.payloads:
            tools = payload["tools"]
            names = {tool.get("name", tool.get("function", {}).get("name")) for tool in tools}
            assert names == {"list_connections", "propose_query", "propose_report", "propose_query_spec", "propose_pivot", "propose_segment"}
            assert "max_output_tokens" not in payload and "max_tokens" not in payload
            assert "/Users/" not in json.dumps(payload)
        assert any(e["kind"] == "tool.completed" for e in run["events"])
        assert "fixture-private-key-value" not in json.dumps(run)
        await cleanup(env)
    asyncio.run(scenario())


def test_provider_failure_is_actual_failure_and_request_receipt_is_idempotent(tmp_path):
    async def scenario():
        env = setup(tmp_path, lambda _r, _n: httpx.Response(401, text="bad fixture-private-key-value credentials"))
        configure(env)
        run = await finish(env)
        assert run["status"] == "failed" and run["answer"] is None
        assert "401" in run["error"] and "fixture-private-key-value" not in json.dumps(run)
        again = await finish(env)
        assert again["id"] == run["id"] and len(env.host.payloads) == 1
        with pytest.raises(HTTPException) as conflict:
            await env.copilot.send(env.owner, env.wid, {"request_id": "fixture-request", "message": "different"})
        assert conflict.value.status_code == 409
        await cleanup(env)
    asyncio.run(scenario())


def test_runs_private_live_membership_and_interrupted_requests_not_replayed(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env, share_with_team=True)
        run = await finish(env)
        assert env.copilot.snapshot(env.editor, env.wid)["runs"] == []
        with pytest.raises(HTTPException) as denied:
            env.copilot.get_run(env.editor, env.wid, run["id"])
        assert denied.value.status_code == 404
        stored = env.service.store.get("copilot_run", run["id"])
        env.service.store.update("copilot_run", run["id"], {"status": "running"}, stored["revision"])
        recovered = DataCopilot(env.service, env.service.state)
        assert recovered.get_run(env.owner, env.wid, run["id"])["status"] == "unknown"
        assert len(env.host.payloads) == 1
        member = env.service.store.get("member", env.service.membership_id(env.wid, env.owner.id))
        env.service.store.archive("member", member["id"], member["revision"])
        with pytest.raises(HTTPException):
            recovered.get_run(env.owner, env.wid, run["id"])
        await cleanup(env)
    asyncio.run(scenario())


def test_context_consent_enforced_even_when_model_calls_hidden_tool(tmp_path):
    async def scenario():
        env = setup(tmp_path, lambda _r, n: response(text="", calls=[("read_schema", {"connection_id": "guessed"})]) if n == 1 else response("Please enable schema context"))
        configure(env)
        run = await finish(env)
        assert run["status"] == "completed"
        event = next(e for e in run["events"] if e["kind"] == "tool.completed")
        assert "not authorized" in event["result"]["error"]
        assert env.service._runtime is None
        await cleanup(env)
    asyncio.run(scenario())


def test_cancellation_interrupts_inflight_provider_without_answer(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env)
        entered = asyncio.Event()
        class WaitingAdapter:
            data_only = True
            async def turn(self, **_kwargs):
                entered.set()
                await asyncio.Event().wait()
        env.host._adapter = lambda _provider: WaitingAdapter()
        run = await env.copilot.send(env.owner, env.wid, {"request_id": "cancel", "message": "Wait"})
        await entered.wait()
        result = await env.copilot.cancel(env.owner, env.wid, run["id"])
        assert result["status"] == "cancelled" and not result["answer"]
        assert any(e["kind"] == "cancelled" for e in result["events"])
        await cleanup(env)
    asyncio.run(scenario())


def test_source_change_during_inference_blocks_answer_and_history(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env)
        connection = env.service.store.create("connection", env.wid, env.owner.id,
            {"name": "Sensitive source", "engine": "sqlite", "source_generation": 1, "credential_generation": 1})
        def change_source(_request, _turn):
            env.service.store.update("connection", connection["id"], {"source_generation": 2}, connection["revision"])
            return response("This answer must never be published")
        env.host.handler = change_source
        run = await env.copilot.send(env.owner, env.wid, {"request_id": "source-change", "message": "Read", "connection_id": connection["id"]})
        await env.copilot._workers[run["id"]]
        stored = env.service.store.get("copilot_run", run["id"])
        assert stored["status"] == "failed" and stored["answer"] is None
        assert "source changed" in stored["error"]
        with pytest.raises(HTTPException) as stale:
            env.copilot.get_run(env.owner, env.wid, run["id"])
        assert stale.value.status_code == 409
        assert env.copilot.snapshot(env.owner, env.wid)["runs"] == []
        await cleanup(env)
    asyncio.run(scenario())


def test_team_grant_revoked_between_model_turns_stops_next_inference(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env, share_with_team=True)
        def revoke(_request, _turn):
            settings = env.copilot._settings(env.wid)
            env.copilot.save_settings(env.owner, env.wid, {"revision": settings["revision"], "provider_id": "fixture-provider", "model": "fixture-model", "share_with_team": False})
            return response(text="", calls=[("list_connections", {})])
        env.host.handler = revoke
        run = await finish(env, actor=env.editor)
        assert run["status"] == "failed" and len(env.host.payloads) == 1
        assert not any(e["kind"] == "tool.started" for e in run["events"])
        await cleanup(env)
    asyncio.run(scenario())


def test_sqlite_tool_reads_and_typed_proposals_are_real_without_silent_saves(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env)
        source = tmp_path / "business.sqlite3"
        with sqlite3.connect(source) as db:
            db.execute('CREATE TABLE sales(region TEXT, amount INTEGER, password TEXT)')
            db.executemany('INSERT INTO sales VALUES (?,?,?)', [("East", 10, "private-database-password"), ("West", 20, "other-secret")])
        connection = env.service.runtime.create_connection(env.owner, env.wid,
            {"name": "Synthetic sales", "engine": "sqlite", "config": {"path": str(source)}})
        query = env.service.runtime.create_query(env.owner, env.wid,
            {"name": "Saved sales", "connection_id": connection["id"], "sql": "SELECT region, amount FROM sales"})
        calls = [
            ("read_schema", {"connection_id": connection["id"]}),
            ("read_query", {"connection_id": connection["id"], "sql": "SELECT region, amount, password FROM sales ORDER BY region"}),
            ("propose_query", {"name": "Total by region", "connection_id": connection["id"], "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"}),
            ("propose_report", {"name": "Sales chart", "query_id": query["id"], "visual": {"kind": "bar", "x": "region", "y": "amount", "aggregate": "sum"}}),
            ("read_query", {"connection_id": connection["id"], "sql": "DELETE FROM sales"}),
            ("shell", {"command": "echo unauthorized"}),
        ]
        env.host.handler = lambda _r, n: response(text="", calls=[calls[n-1]]) if n <= len(calls) else response("Observed two rows; East 10 and West 20. Review the proposed query and report.")
        run = await finish(env, {"connection_id": connection["id"], "query_id": query["id"], "include_schema": True, "include_results": True})
        assert run["status"] == "completed", run
        assert [p["type"] for p in run["proposals"]] == ["query", "report"]
        assert run["proposals"][0]["definition"]["connection_id"] == connection["id"]
        assert run["proposals"][1]["definition"]["visual"] == {"kind": "bar", "x": "region", "y": "amount", "aggregate": "sum"}
        result = next(e["result"] for e in run["events"] if e["kind"] == "tool.completed" and e["title"] == "read_query" and "job_id" in e.get("result", {}))
        assert result["result"]["rows"] == [["East", 10, "[redacted]"], ["West", 20, "[redacted]"]]
        assert result["result"]["row_count"] == 2 and not result["result"]["truncated"]
        assert "private-database-password" not in json.dumps(env.host.payloads)
        assert str(source) not in json.dumps(env.host.payloads)
        assert len(env.service.store.list("query", env.wid)) == 1
        assert env.service.store.list("report", env.wid) == []
        assert len(env.service.store.list("job", env.wid)) == 1
        with sqlite3.connect(source) as db:
            assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 2
        await cleanup(env)
    asyncio.run(scenario())


def test_history_rechecks_delegated_local_file_authority(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env)
        source = tmp_path / "business.sqlite3"
        with sqlite3.connect(source) as db:
            db.execute('CREATE TABLE sales(amount INTEGER)')
        connection = env.service.runtime.create_connection(env.owner, env.wid,
            {"name": "Synthetic sales", "engine": "sqlite", "config": {"path": str(source)}})
        run = await finish(env, {"connection_id": connection["id"]})
        assert run["status"] == "completed"
        env.owner.scopes = ()
        with pytest.raises(HTTPException) as denied:
            env.copilot.get_run(env.owner, env.wid, run["id"])
        assert denied.value.status_code == 403
        await cleanup(env)
    asyncio.run(scenario())


def test_no_fixed_tool_round_limit_and_context_remains_bounded(tmp_path):
    async def scenario():
        env = setup(tmp_path, lambda _r, n: response(text="", calls=[("list_connections", {})]) if n <= 40 else response("Finished forty tool rounds"))
        configure(env)
        run = await finish(env)
        assert run["status"] == "completed" and len(env.host.payloads) == 41
        assert max(len(json.dumps(p)) for p in env.host.payloads) < 110_000
        await cleanup(env)
    asyncio.run(scenario())


def test_canonical_router_prefix(tmp_path):
    env = setup(tmp_path)
    router = build_copilot_router(env.service, lambda _request: env.owner)
    assert all(route.path.startswith("/api/data/workspaces/") for route in router.routes)
