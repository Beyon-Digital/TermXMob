from __future__ import annotations

import asyncio
import json
from time import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from _gql import data, err_status
from termx.agent.chatgpt import ChatGPTAccounts, DIRECT_SCOPE, ISSUER, RESOURCE
from termx.agent.providers import ChatGPTResponsesAdapter, ProviderError
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.app import AppState, create_app


class Server:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid="test-key", use="sig", alg="RS256")
        self.requests = []
        self.scopes = f"openid offline_access resource.invoke {DIRECT_SCOPE}"
        self.subject = "user-one"
        self.nonce = ""
        self.audience = "oaiapp_one"
        self.refresh_error = None
        self.revoke_status = 200
        self.refreshes = 0
        self.model_fail = False
        self.bad_signature = False
        self.bad_expiry = False

    def identity(self):
        claims = {"iss": ISSUER, "aud": self.audience, "sub": self.subject,
                  "email": "user@example.test", "iat": time(),
                  "exp": time() - 20 if self.bad_expiry else time() + 3600, "nonce": self.nonce}
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if self.bad_signature else self.key
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    async def handle(self, request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((request.url.path, form, dict(request.headers)))
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks",
                                             "revocation_endpoint": f"{ISSUER}/revoke"})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [self.jwk]})
        if request.url.path == "/revoke":
            return httpx.Response(self.revoke_status)
        if request.url.path.endswith("oauth/token"):
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                await asyncio.sleep(.01)
                if self.refresh_error == "network":
                    raise httpx.ConnectError("Network unavailable", request=request)
                if self.refresh_error:
                    return httpx.Response(400 if self.refresh_error == "invalid_grant" else 503,
                                          json={"error": self.refresh_error})
                return httpx.Response(200, json={"access_token": "access-renewed", "refresh_token": "refresh-new",
                                                 "scope": self.scopes, "expires_in": 3600, "token_type": "Bearer"})
            return httpx.Response(200, json={"access_token": "access-secret", "refresh_token": "refresh-secret",
                                             "id_token": self.identity(), "scope": self.scopes,
                                             "expires_in": 3600, "token_type": "Bearer"})
        if request.url.path == "/v1/models":
            if self.model_fail:
                return httpx.Response(503)
            return httpx.Response(200, json={"models": [
                {"slug": "model-a", "display_name": "Model A", "visibility": "list"},
                {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
                {"slug": "model-b", "display_name": "Model B", "visibility": "list"},
            ]})
        raise AssertionError(request.url)


def service(tmp_path, server):
    store = AgentStore(tmp_path / "agent.db")
    credentials = CredentialStore(memory={})
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    urls = []
    accounts = ChatGPTAccounts(credentials, store, client=client, browser_open=lambda url: urls.append(url) or True)
    return accounts, store, credentials, urls


async def login(accounts, server, *, owner="owner", account_id=None, callback_client_id="oaiapp_one", nonce=None):
    started = await accounts.start(owner, account_id)
    params = {k: v[0] for k, v in parse_qs(urlparse(started["authorization_url"]).query).items()}
    server.nonce = params["nonce"] if nonce is None else nonce
    callback = {"state": params["state"], "code": "test-code"}
    if callback_client_id:
        callback["client_id"] = callback_client_id
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(params["redirect_uri"] + "?" + urlencode(callback))
    await accounts._attempts[started["attempt_id"]]["task"]
    return started, params, accounts.status(started["attempt_id"], owner), response


def test_dynamic_registration_validates_and_keeps_credentials_host_only(tmp_path):
    async def run():
        server = Server()
        accounts, store, credentials, urls = service(tmp_path, server)
        try:
            started, params, status, response = await login(accounts, server)
            assert params["client_id"] == "dynamic_agent_client"
            assert params["agent_name_hint"] == "TermX"
            assert params["resource"] == RESOURCE
            assert params["code_challenge_method"] == "S256"
            assert "id_token_hint" not in params
            assert status["status"] == "completed"
            assert "complete" in response.text
            identity = (await accounts.accounts())[0]
            assert identity["plan_enabled"] and identity["signed_in"]
            assert identity["provider_id"] == status["account_id"]
            assert urls == [started["authorization_url"]]
            on_disk = accounts.path.read_text()
            public = json.dumps(identity) + json.dumps(status) + on_disk
            assert not any(secret in public for secret in ("access-secret", "refresh-secret", "id_token", "test-code"))
            from termx.private_files import private_path_permissions
            assert private_path_permissions(accounts.path)
            exchange = next(form for path, form, _ in server.requests if path.endswith("oauth/token"))
            assert exchange["client_id"] == "oaiapp_one"
            assert exchange["redirect_uri"] == params["redirect_uri"]
            assert exchange["resource"] == RESOURCE and exchange["code_verifier"]
            with pytest.raises(ValueError, match="not found"):
                accounts.status(started["attempt_id"], "different-owner")
            assert not any(k in accounts._attempts[started["attempt_id"]] for k in ("nonce", "verifier", "state"))
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_wrong_path_state_and_replay_cannot_consume_sign_in(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            started = await accounts.start("owner")
            params = {k: v[0] for k, v in parse_qs(urlparse(started["authorization_url"]).query).items()}
            async with httpx.AsyncClient(trust_env=False) as client:
                for url in (params["redirect_uri"] + "?state=wrong&code=x",
                            params["redirect_uri"].replace("/auth/callback", "/elsewhere") + "?" + urlencode({"state": params["state"], "code": "x"})):
                    assert (await client.get(url)).status_code == 400
                assert accounts.status(started["attempt_id"], "owner")["status"] == "pending"
                server.nonce = params["nonce"]
                url = params["redirect_uri"] + "?" + urlencode({"state": params["state"], "code": "ok", "client_id": "oaiapp_one"})
                assert (await client.get(url)).status_code == 200
                with pytest.raises(httpx.ConnectError):
                    await client.get(url)
            assert len([path for path, _, _ in server.requests if path.endswith("oauth/token")]) == 1
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["nonce", "audience", "signature", "expiry", "issued_client"])
def test_invalid_identity_never_creates_account(tmp_path, invalid):
    async def run():
        server = Server(); accounts, store, credentials, _ = service(tmp_path, server)
        if invalid == "audience": server.audience = "different-client"
        if invalid == "signature": server.bad_signature = True
        if invalid == "expiry": server.bad_expiry = True
        try:
            _, _, status, _ = await login(accounts, server, nonce="wrong" if invalid == "nonce" else None,
                                          callback_client_id="dynamic_agent_client" if invalid == "issued_client" else "oaiapp_one")
            assert status["status"] == "failed"
            assert await accounts.accounts() == []
            assert credentials._memory == {}
            assert store.list_providers() == []
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_reauthorization_reuses_host_and_client_and_rejects_different_identity(tmp_path):
    async def run():
        server = Server(); accounts, store, credentials, _ = service(tmp_path, server)
        try:
            _, first, status, _ = await login(accounts, server)
            account_id = status["account_id"]
            original = dict(credentials._memory)
            server.subject = "different-user"
            _, second, failed, _ = await login(accounts, server, account_id=account_id, callback_client_id=None)
            assert failed["status"] == "failed"
            assert second["client_id"] == "oaiapp_one"
            assert "agent_name_hint" not in second
            assert first["ext_agent_host_id"] == second["ext_agent_host_id"]
            assert credentials._memory == original
            assert len(await accounts.accounts()) == 1
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_accounts_same_email_stay_separate_and_models_follow_selected_account(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, first, _ = await login(accounts, server)
            server.audience = "oaiapp_two"
            _, _, second, _ = await login(accounts, server, callback_client_id="oaiapp_two")
            identities = await accounts.accounts()
            assert len(identities) == 2
            assert identities[0]["label"] != identities[1]["label"]
            assert first["account_id"] != second["account_id"]
            models = await accounts.models(first["account_id"])
            assert [m["slug"] for m in models] == ["model-a", "model-b"]
            provider = store.get_provider(first["account_id"])
            assert provider["models"] == ["model-a", "model-b"]
            assert provider["kind"] == "chatgpt" and "computer" not in provider["capabilities"]
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_identity_only_grant_cannot_use_chatgpt_plan(tmp_path):
    async def run():
        server = Server(); server.scopes = "openid email"
        accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            account = (await accounts.accounts())[0]
            assert status["status"] == "completed" and account["signed_in"] and not account["plan_enabled"]
            with pytest.raises(ProviderError, match="not authorized"):
                await accounts.access_token(account["id"])
            assert not store.get_provider(account["id"])["secret_configured"]
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_refresh_is_serialized_rotates_credentials_and_survives_restart(tmp_path):
    async def run():
        server = Server(); accounts, store, credentials, _ = service(tmp_path, server)
        try:
            _, first, status, _ = await login(accounts, server)
            account_id = status["account_id"]
            tokens = await accounts._tokens(account_id); tokens["expires_at"] = time() - 1
            await accounts._save_tokens(account_id, tokens)
            other = ChatGPTAccounts(credentials, store, client=accounts.client, browser_open=lambda _: True)
            assert await asyncio.gather(accounts.access_token(account_id), other.access_token(account_id)) == ["access-renewed"] * 2
            assert server.refreshes == 1
            assert (await accounts._tokens(account_id))["refresh_token"] == "refresh-new"
            assert other._read()["host_id"] == first["ext_agent_host_id"]
            request = next(form for _, form, _ in server.requests if form.get("grant_type") == "refresh_token")
            assert request["client_id"] == "oaiapp_one" and "scope" not in request and request["resource"] == RESOURCE
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


@pytest.mark.parametrize("error", ["invalid_grant", "temporarily_unavailable", "network"])
def test_only_terminal_refresh_error_clears_credentials(tmp_path, error):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            account_id = status["account_id"]
            tokens = await accounts._tokens(account_id); tokens["expires_at"] = time() - 1
            await accounts._save_tokens(account_id, tokens)
            server.refresh_error = error
            with pytest.raises(ProviderError): await accounts.access_token(account_id)
            assert bool(await accounts._tokens(account_id)) == (error != "invalid_grant")
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_sign_out_revokes_and_preserves_registration_and_welcome(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            account_id = status["account_id"]
            await accounts.acknowledge(account_id)
            result = await accounts.sign_out(account_id)
            assert result["revocation_confirmed"]
            revoke = next(form for path, form, _ in server.requests if path == "/revoke")
            assert revoke == {"token": "refresh-secret", "token_type_hint": "refresh_token", "client_id": "oaiapp_one"}
            account = (await accounts.accounts())[0]
            assert not account["signed_in"] and account["welcome_seen"] and account["client_id"] == "oaiapp_one"
            with pytest.raises(ProviderError, match="Sign in"): await accounts.access_token(account_id)
            assert not store.get_provider(account_id)["secret_configured"]
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_sign_out_reports_unconfirmed_revocation_and_clears_local_tokens(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            server.revoke_status = 503
            result = await accounts.sign_out(status["account_id"])
            assert not result["revocation_confirmed"] and "not confirmed" in result["message"]
            assert await accounts._tokens(status["account_id"]) == {}
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_cancel_and_concurrent_start_cleanup_listener(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            attempts = await asyncio.gather(accounts.start("owner"), accounts.start("another-owner"), return_exceptions=True)
            started = next(i for i in attempts if isinstance(i, dict))
            assert len([i for i in attempts if isinstance(i, ValueError)]) == 1
            cancelled = await accounts.cancel(started["attempt_id"], "owner")
            assert cancelled["status"] == "cancelled"
            assert not accounts._attempts[started["attempt_id"]]["server"].is_serving()
            assert not server.requests
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_graphql_account_controls_require_settings_scope(tmp_path):
    state = AppState(passcode="test-only", agent_store=AgentStore(tmp_path / "agent.db"), credentials=CredentialStore(memory={}))
    with TestClient(create_app(state)) as client:
        assert err_status(client, "{ chatgpt_accounts }") == 401
        token = state.tokens.issue(["agent-view"], device_name="viewer")
        assert err_status(client, "{ chatgpt_accounts }", headers={"Authorization": f"Bearer {token}"}) == 403
        assert data(client, "{ chatgpt_accounts }", "chatgpt_accounts", headers={"X-Termx-Passcode": "test-only"}) == []
        mutation = 'mutation { start_chatgpt_sign_in }'
        assert err_status(client, mutation, headers={"Authorization": f"Bearer {token}"}) == 403
        with pytest.raises(ValueError, match="managed"):
            state.agent.save_provider(provider_id="chatgpt-spoof", kind="openai", name="bad", base_url="https://bad.test", model="x", capabilities=["shell"])


def test_subscription_requests_stream_use_local_history_and_namespace_tools():
    class Accounts:
        async def access_token(self, *args, **kwargs): return "oauth-access"
    async def run():
        seen, deltas = [], []
        async def handler(request):
            seen.append((json.loads(request.content), request.headers["authorization"]))
            final = {"id": "response-one", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
            events = [{"type": "response.output_text.delta", "delta": "OK"}, {"type": "response.completed", "response": final}]
            return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = ChatGPTResponsesAdapter(accounts=Accounts(), account_id="one", model="model-a")
            adapter.client = client
            assert await adapter.test() == "OK"
            turn = await adapter.stream_turn(prompt="Inspect", cwd="/tmp", manifest={}, previous_response_id="never-send",
                                             input_items=[{"type": "function_call_output", "call_id": "tool-one", "output": "done"}], on_delta=deltas.append)
            assert turn.text == "OK" and deltas == ["OK"]
            for payload, authorization in seen:
                assert payload["stream"] and payload["store"] is False and isinstance(payload["input"], list)
                assert "max_output_tokens" not in payload and "previous_response_id" not in payload
                assert authorization == "Bearer oauth-access"
            assert seen[-1][0]["input"][-1]["call_id"] == "tool-one"
            assert seen[-1][0]["tools"][0]["type"] == "namespace"
            assert all(t["type"] == "function" for t in seen[-1][0]["tools"][0]["tools"])
    asyncio.run(run())


@pytest.mark.parametrize("http_status", [200, 403, 429])
def test_usage_limit_after_streaming_is_actionable_and_never_success(http_status):
    class Accounts:
        async def access_token(self, *args, **kwargs): return "oauth-access"
    async def run():
        async def handler(request):
            error = {"code": "subscription_sharing_usage_limit_exceeded", "message": "usage exceeded"}
            if http_status != 200: return httpx.Response(http_status, json={"error": error})
            return httpx.Response(200, text=f'data: {json.dumps({"type": "response.failed", "response": {"status": "failed", "error": error}})}\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = ChatGPTResponsesAdapter(accounts=Accounts(), account_id="one", model="model-a"); adapter.client = client
            with pytest.raises(ProviderError, match="ChatGPT usage limit reached.*settings/usage"):
                await adapter.test()
    asyncio.run(run())


def test_pending_sign_in_is_recoverable_after_client_retry(tmp_path):
    async def run():
        server = Server(); accounts, store, _, urls = service(tmp_path, server)
        try:
            first = await accounts.start("owner")
            retry = await accounts.start("owner")
            assert first["attempt_id"] == retry["attempt_id"]
            assert first["authorization_url"] == retry["authorization_url"]
            assert len(urls) == 1
            await accounts.cancel(first["attempt_id"], "owner")
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_cancel_during_refresh_finishes_rotation_before_sign_out(tmp_path):
    async def run():
        server = Server(); accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            account_id = status["account_id"]
            tokens = await accounts._tokens(account_id); tokens["expires_at"] = time() - 1
            await accounts._save_tokens(account_id, tokens)
            worker = asyncio.create_task(accounts.access_token(account_id))
            while server.refreshes == 0: await asyncio.sleep(.001)
            worker.cancel()
            with pytest.raises(asyncio.CancelledError): await worker
            assert (await accounts._tokens(account_id))["refresh_token"] == "refresh-new"
            await accounts.sign_out(account_id)
            revoke = next(form for path, form, _ in server.requests if path == "/revoke")
            assert revoke["token"] == "refresh-new"
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_catalog_failure_keeps_validated_sign_in_and_can_retry_models(tmp_path):
    async def run():
        server = Server(); server.model_fail = True
        accounts, store, _, _ = service(tmp_path, server)
        try:
            _, _, status, _ = await login(accounts, server)
            assert status["status"] == "completed" and "Refresh models" in status["message"]
            assert (await accounts.accounts())[0]["signed_in"]
            server.model_fail = False
            assert len(await accounts.models(status["account_id"])) == 2
        finally:
            await accounts.close(); await accounts.client.aclose(); store.close()
    asyncio.run(run())


def test_graphql_real_account_flow_and_sign_out_stops_paused_tasks(tmp_path):
    server = Server()
    state = AppState(passcode="test-only", agent_store=AgentStore(tmp_path / "agent.db"), credentials=CredentialStore(memory={}))
    asyncio.run(state.chatgpt.client.aclose())
    state.chatgpt.client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    state.chatgpt.browser_open = lambda _: False
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        start = data(client, "mutation { start_chatgpt_sign_in }", "start_chatgpt_sign_in", headers=headers)
        params = {k: v[0] for k, v in parse_qs(urlparse(start["authorization_url"]).query).items()}
        server.nonce = params["nonce"]
        response = httpx.get(params["redirect_uri"] + "?" + urlencode({"state": params["state"], "code": "test-code", "client_id": "oaiapp_one"}), trust_env=False)
        assert "complete" in response.text
        account = data(client, "{ chatgpt_accounts }", "chatgpt_accounts", headers=headers)[0]
        account_id = account["id"]
        assert account["signed_in"] and account["plan_enabled"]
        models = data(client, 'mutation($id: String!) { refresh_chatgpt_models(account_id: $id) }', "refresh_chatgpt_models", {"id": account_id}, headers)
        assert [m["slug"] for m in models] == ["model-a", "model-b"]
        task = state.agent_store.create_task(prompt="Test", cwd=str(tmp_path), provider_id=account_id, model="model-a", limits={})
        state.agent_store.update_task(task["id"], status="awaiting_approval")
        result = data(client, 'mutation($id: String!) { sign_out_chatgpt(account_id: $id) }', "sign_out_chatgpt", {"id": account_id}, headers)
        assert result["signed_out"] and result["revocation_confirmed"]
        assert state.agent_store.get_task(task["id"])["status"] == "cancelled"
        assert not state.agent_store.get_provider(account_id)["secret_configured"]
