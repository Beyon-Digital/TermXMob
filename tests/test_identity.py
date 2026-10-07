from __future__ import annotations

import asyncio
import json
import secrets
from time import time

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from _gql import data, err_status
from termx.app import AppState, create_app
from termx.auth import Auth
from termx.identity import AuthenticationError, AuthenticationService, Identity
from termx.graphql.context import TermxContext

PASSWORD = "test-only-password-123"
ORIGIN = {"Origin": "https://localhost"}


def owner(service):
    return service.setup_owner("owner", PASSWORD)


def login(service, **kw):
    return asyncio.run(service.login("local-password", {"username": "owner", "password": PASSWORD}, peer="127.0.0.1", **kw))


def test_passwords_refresh_secrets_and_key_permissions(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    owner(service)
    credentials = login(service)
    assert service.resolve(credentials.access_token).principal.display_name == "owner"
    contents = service.path.read_bytes()
    assert PASSWORD.encode() not in contents
    assert credentials.refresh_token.encode() not in contents
    assert credentials.csrf_token.encode() not in contents
    assert service.path.stat().st_mode & 0o777 == 0o600
    recovered = AuthenticationService(service.path)
    assert recovered.resolve(credentials.access_token)
    assert recovered.host_id == service.host_id


def test_refresh_rotation_and_replay_revokes_family(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    owner(service)
    old = login(service)
    new = AuthenticationService(service.path).refresh(old.refresh_token)
    assert new.refresh_token != old.refresh_token
    assert service.resolve(new.access_token)
    with pytest.raises(AuthenticationError, match="reuse"):
        service.refresh(old.refresh_token)
    assert service.resolve(new.access_token) is None
    with pytest.raises(AuthenticationError):
        service.refresh(new.refresh_token)
    assert AuthenticationService(service.path).resolve(old.access_token) is None


def test_authority_is_live_and_context_does_not_cache(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    principal = owner(service)
    credentials = login(service)
    state = AppState(identity=service)
    ctx = TermxContext(state, credentials.access_token)
    assert "host-admin" in ctx.scopes
    service.set_scopes(principal.id, ["machine-view"])
    assert ctx.scopes == ["machine-view"]
    service.disable(principal.id)
    assert ctx.scopes is None


@pytest.mark.parametrize("changes", [
    {"iss": "wrong"}, {"aud": "wrong"}, {"aud": ["extra", "placeholder"]},
    {"token_type": "id"}, {"sid": "wrong"}, {"sub": "wrong"}, {"exp": 1}, {"iat": int(time()) + 1000},
])
def test_token_contract_rejects_wrong_claims(tmp_path, changes):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    owner(service)
    credentials = login(service)
    header = jwt.get_unverified_header(credentials.access_token)
    claims = jwt.decode(credentials.access_token, options={"verify_signature": False})
    claims.update(changes)
    with service._db() as db:
        secret = db.execute("SELECT secret FROM signing_keys WHERE id=?", (header["kid"],)).fetchone()[0]
    bad = jwt.encode(claims, secret, algorithm="HS256", headers=header)
    assert service.resolve(bad) is None


def test_wrong_algorithm_type_key_and_cross_host(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    owner(service)
    credentials = login(service)
    header = jwt.get_unverified_header(credentials.access_token)
    claims = jwt.decode(credentials.access_token, options={"verify_signature": False})
    for alg, typ in [("HS384", "at+jwt"), ("HS256", "JWT")]:
        bad = jwt.encode(claims, secrets.token_urlsafe(48), algorithm=alg, headers={"kid": header["kid"], "typ": typ})
        assert service.resolve(bad) is None
    assert AuthenticationService(tmp_path / "other.sqlite3").resolve(credentials.access_token) is None
    service.rotate_signing_key()
    assert service.resolve(credentials.access_token)
    assert jwt.get_unverified_header(login(service).access_token)["kid"] != header["kid"]


def test_expiry_revocation_and_session_isolation(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    principal = owner(service)
    one, two = login(service), login(service)
    assert not service.revoke(one.session_id, "another-user")
    assert service.revoke(one.session_id, principal.id)
    assert service.resolve(one.access_token) is None
    assert service.resolve(two.access_token)
    with service._db() as db:
        db.execute("UPDATE sessions SET last_seen=1 WHERE id=?", (two.session_id,))
    assert service.resolve(two.access_token) is None
    with pytest.raises(AuthenticationError):
        service.refresh(two.refresh_token)


def test_old_active_key_retirement_preserves_recent_tokens(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    owner(service)
    with service._db() as db:
        db.execute("UPDATE signing_keys SET created=?", (time() - 86400,))
    credentials = login(service)
    old_kid = jwt.get_unverified_header(credentials.access_token)["kid"]
    service.rotate_signing_key()
    assert AuthenticationService(service.path).resolve(credentials.access_token)
    with service._db() as db:
        db.execute("UPDATE signing_keys SET retired_after=1 WHERE id=?", (old_kid,))
    service.rotate_signing_key()
    assert service.resolve(credentials.access_token) is None


def test_custom_adapter_uses_explicit_issuer_subject_mapping(tmp_path):
    class CustomAdapter:
        id = "custom"
        label = "Company identity"
        async def authenticate(self, evidence):
            if evidence.get("assertion") != "test-only-proof":
                raise AuthenticationError("invalid credentials")
            return Identity("https://company.example", "immutable-id", "custom")
    service = AuthenticationService(tmp_path / "auth.sqlite3", adapters=(CustomAdapter(),))
    principal = owner(service)
    with pytest.raises(AuthenticationError):
        asyncio.run(service.login("custom", {"assertion": "test-only-proof"}, peer="local"))
    service.map_identity(Identity("https://company.example", "immutable-id", "custom"), principal.id)
    credentials = asyncio.run(service.login("custom", {"assertion": "test-only-proof"}, peer="local"))
    assert service.resolve(credentials.access_token).principal.id == principal.id
    assert any(method["id"] == "custom" for method in service.methods())


def client_and_state():
    state = AppState(passcode="bootstrap")
    return TestClient(create_app(state), base_url="https://localhost"), state


def setup(client, transport="cookie"):
    response = client.post("/auth/setup", headers={**ORIGIN, "X-Termx-Passcode": "bootstrap"},
                           json={"username": "owner", "password": PASSWORD, "transport": transport})
    assert response.status_code == 200, response.text
    return response.json()


def test_cookie_facade_csrf_and_logout():
    client, state = client_and_state()
    result = setup(client)
    assert "access_token" not in result and "refresh_token" not in result
    assert client.cookies.get("termx_csrf") == result["csrf_token"]
    cookie = client.cookies.get("termx_access")
    headers = {**ORIGIN, "X-Termx-Csrf": result["csrf_token"]}
    assert client.get("/auth/me").status_code == 200
    assert client.get("/auth/sessions").json()[0]["id"] == result["session_id"]
    assert client.post("/graphql", json={"query": "{ sessions { id } }"}).status_code == 403
    assert data(client, "{ sessions { id } }", "sessions", headers=headers) == []
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 403
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert state.identity.resolve(cookie) is None
    assert not client.cookies.get("termx_access")
    assert not client.cookies.get("termx_csrf")
    assert client.get("/auth/me").status_code == 401


def test_cookie_refresh_updates_csrf_and_requires_origin():
    client, state = client_and_state()
    result = setup(client)
    assert client.post("/auth/refresh", json={}).status_code == 403
    response = client.post("/auth/refresh", json={}, headers={**ORIGIN, "X-Termx-Csrf": result["csrf_token"]})
    assert response.status_code == 200, response.text
    assert response.json()["csrf_token"] != result["csrf_token"]
    assert client.get("/auth/me").status_code == 200


def test_native_tokens_and_bounded_device_migration():
    client, state = client_and_state()
    device = state.tokens.issue(["machine-view"])
    result = setup(client, transport="bearer")
    assert state.auth.check(device)
    assert not state.auth.check("bootstrap")
    assert not state.auth.check(PASSWORD)
    auth = {"Authorization": f"Bearer {result['access_token']}"}
    assert data(client, "{ sessions { id } }", "sessions", headers=auth) == []
    assert err_status(client, "{ sessions { id } }", headers={"X-Termx-Passcode": "bootstrap"}) == 401
    new = client.post("/auth/refresh", json={"refresh_token": result["refresh_token"]})
    assert new.status_code == 200
    assert "refresh_token" in new.json()
    with state.identity._db() as db:
        db.execute("UPDATE metadata SET value='1' WHERE key='migration_deadline'")
    assert not state.auth.check(device)
    assert state.auth.check(new.json()["access_token"])


def test_unconfigured_remote_closed_and_setup_local_only():
    state = AppState()
    client = TestClient(create_app(state), base_url="https://localhost", client=("203.0.113.20", 50000))
    assert client.post("/graphql", json={"query": "{ sessions { id } }"}).status_code == 403
    assert client.get("/auth/methods").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/auth/setup", headers=ORIGIN, json={"username": "intruder", "password": PASSWORD}).status_code == 403
    local = TestClient(create_app(state), base_url="https://localhost")
    assert local.post("/auth/setup", headers={"Origin": "https://evil.example"}, json={"username": "intruder", "password": PASSWORD}).status_code == 403
    assert not state.identity.configured


def test_rate_limited_unknown_users_and_foreign_origin():
    client, state = client_and_state()
    setup(client)
    body = {"username": "unknown", "password": "wrong", "transport": "bearer"}
    for _ in range(9):
        assert client.post("/auth/login", json=body).status_code == 401
    assert client.post("/auth/login", json=body).status_code == 429
    assert client.post("/auth/login", json=body, headers={"Origin": "https://evil.example"}).status_code == 403


def test_graphql_socket_revokes_while_connected():
    client, state = client_and_state()
    credentials = setup(client, transport="bearer")
    token = credentials["access_token"]
    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
        socket.send_json({"type": "connection_init", "payload": {"authorization": f"Bearer {token}"}})
        assert socket.receive_json()["type"] == "connection_ack"
        socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": "subscription { activity_events { type } }"}})
        state.identity.revoke(credentials["session_id"], state.identity.resolve(token).principal.id)
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
        assert exc.value.code == 4401


def test_cookie_socket_origin_and_no_jwt_in_query():
    client, state = client_and_state()
    setup(client)
    token = client.cookies.get("termx_access")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"], headers={"Origin": "https://evil.example", "Cookie": f"termx_access={token}"}):
            pass
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/sessions/absent/pty?k={token}"):
            pass


def test_audit_excludes_credentials():
    client, state = client_and_state()
    credentials = setup(client, transport="bearer")
    audit = (state.identity.path.parent / "audit.jsonl").read_text()
    for secret in (PASSWORD, credentials["access_token"], credentials["refresh_token"], credentials["csrf_token"]):
        assert secret not in audit


def test_custom_signed_assertion_reference_rejects_replay(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from termx.identity_adapters import SignedAssertionAdapter
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    principal = owner(service)
    key = Ed25519PrivateKey.generate()
    adapter = SignedAssertionAdapter(service, adapter_id="custom", label="Custom", issuer="company", audience="termx-login",
                                     public_key=key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode())
    service.register_adapter(adapter)
    service.map_identity(Identity("company", "user-123", "signed-assertion"), principal.id)
    raw = jwt.encode({"iss": "company", "aud": "termx-login", "sub": "user-123", "jti": "unique",
                      "iat": int(time()), "exp": int(time()) + 60}, key, algorithm="EdDSA", headers={"typ": "termx-identity+jwt"})
    credentials = asyncio.run(service.login("custom", {"assertion": raw}, peer="127.0.0.1"))
    assert service.resolve(credentials.access_token).principal.id == principal.id
    with pytest.raises(AuthenticationError, match="replayed"):
        asyncio.run(service.login("custom", {"assertion": raw}, peer="127.0.0.1"))


@pytest.mark.parametrize("failure", [None, "nonce", "issuer", "audience", "state", "binding", "replay"])
def test_oidc_code_pkce_flow_common_session_and_failures(tmp_path, failure):
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    import httpx
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
    from termx.identity_adapters import OidcAdapter, OidcConfig
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    principal = owner(service)
    key = generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="oidc-key", alg="RS256", use="sig")
    shared = {}
    requests = []
    def provider(request):
        requests.append(request.url.path)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": "https://idp.example", "authorization_endpoint": "https://idp.example/authorize",
                "token_endpoint": "https://idp.example/token", "jwks_uri": "https://idp.example/jwks", "code_challenge_methods_supported": ["S256"]})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            assert challenge == shared["code_challenge"][0]
            assert form["redirect_uri"] == ["https://localhost/auth/oidc/company/callback"]
            claims = {"iss": "wrong" if failure == "issuer" else "https://idp.example",
                      "aud": "wrong" if failure == "audience" else "client-id", "sub": "subject-123",
                      "iat": int(time()), "exp": int(time()) + 60,
                      "nonce": "wrong" if failure == "nonce" else shared["nonce"][0]}
            raw = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "oidc-key"})
            return httpx.Response(200, json={"id_token": raw})
        raise AssertionError(request.url)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            adapter = OidcAdapter(service, OidcConfig("company", "Company SSO", "https://idp.example", "client-id", "https://localhost/auth/oidc/company/callback"), client=http)
            service.register_adapter(adapter)
            service.map_identity(Identity("https://idp.example", "subject-123", "oidc"), principal.id)
            url, binding = await adapter.begin()
            shared.update(parse_qs(urlsplit(url).query))
            evidence = {"code": "code-123", "state": "wrong" if failure == "state" else shared["state"][0],
                        "binding": "wrong" if failure == "binding" else binding}
            if failure in {"nonce", "issuer", "audience", "state", "binding"}:
                with pytest.raises(AuthenticationError):
                    await service.login("company", evidence, peer="127.0.0.1")
                if failure in {"state", "binding"}:
                    assert "/token" not in requests
            else:
                credentials = await service.login("company", evidence, peer="127.0.0.1")
                assert service.resolve(credentials.access_token).principal.id == principal.id
                if failure is None:
                    # Exercise the actual browser entry endpoints, cookie
                    # binding, redirect and post-login GraphQL authority.
                    browser = TestClient(create_app(AppState(identity=service)), base_url="https://localhost")
                    started = browser.post("/auth/oidc/company/begin", headers=ORIGIN)
                    assert started.status_code == 200, started.text
                    shared.clear()
                    shared.update(parse_qs(urlsplit(started.json()["authorization_url"]).query))
                    callback = browser.get("/auth/oidc/company/callback",
                                           params={"code": "code-123", "state": shared["state"][0]}, follow_redirects=False)
                    assert callback.status_code == 303, callback.text
                    assert callback.headers["location"] == "/"
                    assert browser.get("/auth/me").json()["principal"]["id"] == principal.id
                    assert browser.cookies.get("termx_csrf")
                    assert data(browser, "{ sessions { id } }", "sessions",
                                headers={**ORIGIN, "X-Termx-CSRF": browser.cookies.get("termx_csrf")}) == []
                    replay = browser.get("/auth/oidc/company/callback",
                                         params={"code": "code-123", "state": shared["state"][0]}, follow_redirects=False)
                    assert replay.status_code == 401
                # Provider ID tokens are never generic TermX API credentials.
                if failure == "replay":
                    with pytest.raises(AuthenticationError):
                        await service.login("company", evidence, peer="127.0.0.1")
    asyncio.run(run())


def test_remote_transport_requires_tls_even_for_legacy():
    state = AppState(passcode="bootstrap")
    client = TestClient(create_app(state), base_url="http://host.example", client=("203.0.113.20", 50000))
    response = client.post("/graphql", headers={"X-Termx-Passcode": "bootstrap"}, json={"query": "{ sessions { id } }"})
    assert response.status_code == 403
    assert client.post("/auth/login", json={"transport": "bearer", "username": "owner", "password": PASSWORD}).status_code == 403
    secure = TestClient(create_app(state), base_url="https://host.example", client=("203.0.113.20", 50000))
    assert data(secure, "{ sessions { id } }", "sessions", headers={"X-Termx-Passcode": "bootstrap"}) == []


def test_cookie_socket_with_correct_origin_and_live_policy_reduction():
    client, state = client_and_state()
    setup(client)
    token = client.cookies.get("termx_access")
    with client.websocket_connect("wss://localhost/graphql", subprotocols=["graphql-transport-ws"], headers=ORIGIN) as socket:
        socket.send_json({"type": "connection_init", "payload": {}})
        assert socket.receive_json()["type"] == "connection_ack"
        principal = state.identity.resolve(token).principal
        state.identity.set_scopes(principal.id, ["machine-view"])
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
        assert exc.value.code == 4401


def test_configuration_activation_atomic_and_requires_admin_file(tmp_path):
    from termx.identity_adapters import load_configured_adapters
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    principal = owner(service)
    path = tmp_path / "adapters.json"
    config = {"version": 1, "adapters": [{"id": "company", "kind": "oidc", "label": "Company SSO",
            "issuer": "https://idp.example", "client_id": "client-id", "redirect_uri": "https://localhost/auth/oidc/company/callback"}],
            "bindings": [{"issuer": "https://idp.example", "subject": "immutable-123", "principal_id": principal.id}]}
    path.write_text(json.dumps(config)); path.chmod(0o600)
    load_configured_adapters(service, path)
    assert service.principal_for(Identity("https://idp.example", "immutable-123", "oidc")).id == principal.id
    assert any(method["flow"] == "redirect" for method in service.methods())
    other = AuthenticationService(tmp_path / "other.sqlite3")
    owner(other)
    with pytest.raises(ValueError, match="unknown principal"):
        load_configured_adapters(other, path)
    assert "company" not in other.adapters
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-readable"):
        load_configured_adapters(other, path)


def test_authentication_adapter_failure_does_not_fall_back(tmp_path):
    class Broken:
        id = "broken"
        label = "Enterprise"
        async def authenticate(self, evidence):
            raise RuntimeError("provider crashed with private diagnostic")
    service = AuthenticationService(tmp_path / "auth.sqlite3", adapters=(Broken(),))
    owner(service)
    with pytest.raises(AuthenticationError, match="unavailable"):
        asyncio.run(service.login("broken", {"username": "owner", "password": PASSWORD}, peer="127.0.0.1"))
    assert not service.list_sessions(service.principal_for(Identity("termx:local", "owner", "password")).id)


def test_managed_connect_links_and_http_queries_do_not_expose_credentials():
    client, state = client_and_state()
    result = setup(client, transport="bearer")
    token = result["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    connection = data(client, "{ connect_info { passcode connect_url } }", "connect_info", headers=auth)
    assert connection["passcode"] == ""
    assert "bootstrap" not in connection["connect_url"] and "k=" not in connection["connect_url"]
    assert client.get(f"/api/connect/qr.svg?k={token}").status_code == 401
    assert client.get("/api/connect/qr.svg", headers=auth).status_code == 200


def test_raw_pty_socket_revalidates_before_input(monkeypatch):
    client, state = client_and_state()
    result = setup(client, transport="bearer")
    writes = []
    class FakeSession:
        exited = False
        cols, rows = 80, 24
        def attach(self, loop): pass
        def subscribe(self, socket): return b""
        def unsubscribe(self, socket): pass
        def write(self, value): writes.append(value)
    monkeypatch.setattr(state.sessions, "get", lambda sid: FakeSession())
    token = result["access_token"]
    principal = state.identity.resolve(token).principal
    with client.websocket_connect("/api/sessions/test/pty", headers={"Authorization": f"Bearer {token}"}) as socket:
        state.identity.revoke(result["session_id"], principal.id)
        socket.send_json({"type": "input", "data": "must-not-execute"})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_bytes()
    assert writes == []
