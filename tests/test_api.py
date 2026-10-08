import re

import time

from fastapi.testclient import TestClient

from _gql import data, err_status, gql
from termx.app import AppState, create_app
from termx.config import ConfigStore
from termx.net import http_urls, qr_ascii


def test_health_open() -> None:
    client = TestClient(create_app(AppState(passcode=None), web_dir=None))
    body = data(client, "{ health { ok passcode_required } }", "health")
    assert body["ok"] is True
    assert body["passcode_required"] is False


def test_sessions_require_passcode() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert err_status(client, "{ sessions { id } }") == 401
    assert (
        err_status(client, "{ sessions { id } }", headers={"X-Termx-Passcode": "nope"})
        == 401
    )
    assert (
        data(client, "{ sessions { id } }", "sessions",
             headers={"X-Termx-Passcode": "secret"})
        == []
    )


def test_http_urls_and_qr_are_fast() -> None:
    start = time.monotonic()
    urls = http_urls(8787)
    assert any(u.startswith("http://127.0.0.1:8787") for u in urls)
    qr_ascii(urls[0])
    assert time.monotonic() - start < 2


def test_builtin_ui_served() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    res = client.get("/")
    assert res.status_code in {200, 503}
    assert 'TermX' in res.text
    assert 'termx-ui-contract' in res.text or 'Install the desktop workspace bundle' in res.text
    css = client.get("/_/vendor/xterm.css")
    assert css.status_code == 200


def test_builtin_pages_reference_loadable_assets() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    pattern = re.compile(r'(?:src|href)="(/_/[^"]+)"')
    for page, needs_assets in (("/_/embed.html", True), ("/_/app.html", True), ("/_/connect.html", False)):
        html = client.get(page)
        assert html.status_code == 200, page
        refs = pattern.findall(html.text)
        if needs_assets:
            assert refs, f"{page} references no local assets"
        for ref in refs:
            res = client.get(ref)
            assert res.status_code == 200, f"{page} -> {ref} returned {res.status_code}"


def test_exported_web_ui_served(tmp_path) -> None:
    web_dir = tmp_path / "dist"
    web_dir.mkdir()
    markup = '<html><head><meta name="termx-ui-contract" content="3"></head><body>TermX Workspace</body></html>'
    (web_dir / "index.html").write_text(markup, encoding="utf-8")
    (web_dir / "asset.js").write_text("export {};", encoding="utf-8")
    client = TestClient(create_app(AppState(), web_dir=web_dir))
    assert client.get("/").text == markup
    assert client.get("/asset.js").text == "export {};"
    assert client.get("/workspace").text == markup


def test_incompatible_ui_never_falls_back_to_expo(tmp_path):
    (tmp_path/'index.html').write_text('<html>Old Expo bundle</html>')
    client = TestClient(create_app(AppState(), web_dir=tmp_path))
    assert client.get('/').status_code == 503
    assert 'Old Expo bundle' not in client.get('/').text
    assert client.get('/workspace-version.json').json()['host_contract'] == 3
    assert client.get('/api/does-not-exist').status_code == 404


def test_pair_issues_token() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert err_status(client, "mutation { pair { token } }") == 401
    token = data(
        client, "mutation { pair { token } }", "pair",
        headers={"X-Termx-Passcode": "secret"},
    )["token"]
    assert token
    listed = data(
        client, "{ sessions { id } }", "sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed == []


def test_health_includes_capabilities() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    body = data(client, "{ health { ok capabilities } }", "health")
    assert body["ok"] is True
    assert body["capabilities"]["saved_commands"] is True
    assert body["capabilities"]["dynamic_tunnels"] is True
    assert "cloudflare" in body["capabilities"]["providers"]


def test_launch_at_login_unmanaged() -> None:
    """Without a desktop shell the host cannot manage launch-at-login."""
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    body = data(
        client, "{ launch_at_login { managed enabled } }", "launch_at_login",
        headers={"X-Termx-Passcode": "secret"},
    )
    assert body == {"managed": False, "enabled": None}
    assert (
        err_status(
            client, "mutation { set_launch_at_login(enabled: true) { managed } }",
            headers={"X-Termx-Passcode": "secret"},
        )
        == 503
    )


def test_health_does_not_fingerprint_host() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    body = data(
        client,
        "{ health { ok passcode_required tunnel } }",
        "health",
    )
    assert body["ok"] is True
    assert body["passcode_required"] is True
    # The open-health payload carries no host fingerprint — tunnel stays unset.
    assert body["tunnel"] is None
    machine = data(
        client, "{ machine { hostname os tunnel } }", "machine",
        headers={"X-Termx-Passcode": "secret"},
    )
    assert machine["hostname"]
    assert machine["os"]
    assert "tunnel" in machine


def test_machine_reports_rtc_availability() -> None:
    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    body = data(
        client, "{ machine { capabilities } }", "machine",
        headers={"X-Termx-Passcode": "secret"},
    )
    assert body["capabilities"]["webrtc"] == state.rtc.available()
    health = data(client, "{ health { capabilities } }", "health")
    assert health["capabilities"]["webrtc"] == state.rtc.available()


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/graphql",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_cors_allows_lan_origins() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    for origin in (
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://192.168.1.20:8081",
        "http://10.0.0.5",
        "http://172.16.3.4:9000",
        "http://172.31.255.254",
    ):
        res = _preflight(client, origin)
        assert res.status_code == 200, origin
        assert res.headers["access-control-allow-origin"] == origin


def test_cors_rejects_external_origins() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    for origin in (
        "https://evil.example.com",
        "http://172.32.0.1:8081",
        "http://11.0.0.1",
        "https://localhost.evil.com",
    ):
        assert _preflight(client, origin).status_code == 400, origin


def test_cors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CORS_ORIGINS", "https://console.example.com, https://ops.example.com")
    client = TestClient(create_app(AppState(), web_dir=None))
    res = _preflight(client, "https://ops.example.com")
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "https://ops.example.com"
    assert _preflight(client, "http://192.168.1.20:8081").status_code == 400


def test_commands_and_preferences_roundtrip(tmp_path, monkeypatch) -> None:
    from termx.desktop.webrtc import AiortcBackend
    monkeypatch.setattr(AiortcBackend, "available", False)
    client = TestClient(create_app(AppState(), web_dir=None))
    item = data(
        client,
        'mutation { create_command(input: {name: "Hello", command: "echo hi"}) { id } }',
        "create_command",
    )
    listed = data(client, "{ commands { id } }", "commands")
    assert listed[0]["id"] == item["id"]
    prefs = data(client, "{ preferences { cwd shell } }", "preferences")
    assert prefs["cwd"]
    assert prefs["shell"]
    assert (
        err_status(
            client,
            "mutation($cwd: String!) { set_preferences(input: {cwd: $cwd}) { cwd } }",
            {"cwd": str(tmp_path / "nope")},
        )
        == 400
    )
    tunnels = data(client, "{ tunnels { providers { id } } }", "tunnels")
    assert "providers" in tunnels
    displays = data(client, "{ displays { displays { id } } }", "displays")
    assert "displays" in displays
    virtual_status = err_status(
        client,
        'mutation { create_virtual_display(input: {width: 800, height: 600}) }',
    )
    assert virtual_status in {200, 501}
    assert (
        err_status(
            client,
            'mutation { rtc_offer(input: {offer: {type: "offer", sdp: "v=0"}}) }',
        )
        == 503
    )


def test_directories_and_fs_roundtrip(tmp_path) -> None:
    state = AppState()
    state.store = ConfigStore(tmp_path / "config.json")
    client = TestClient(create_app(state, web_dir=None))
    nested = tmp_path / "src"
    nested.mkdir()
    listing = data(
        client, "query($p: String) { fs(path: $p) { entries } }",
        "fs", {"p": str(tmp_path)},
    )
    assert any(item["name"] == "src" for item in listing["entries"])
    item = data(
        client,
        "mutation($input: DirectoryInput!) { create_directory(input: $input) { id } }",
        "create_directory",
        {"input": {"name": "Src", "path": str(nested)}},
    )
    used = data(
        client,
        "mutation($id: String!) { use_directory(directory_id: $id) { cwd } }",
        "use_directory", {"id": item["id"]},
    )
    assert used["cwd"] == str(nested.resolve())
    session = data(
        client,
        "mutation($input: CreateSessionInput!) { create_session(input: $input) { id cwd } }",
        "create_session",
        {"input": {"cols": 80, "rows": 24, "cwd": str(nested)}},
    )
    assert session["cwd"] == str(nested.resolve())
    assert data(
        client,
        "mutation($id: String!) { delete_directory(directory_id: $id) { ok } }",
        "delete_directory", {"id": item["id"]},
    )["ok"] is True
    data(
        client,
        "mutation($id: String!) { delete_session(session_id: $id) { ok } }",
        "delete_session", {"id": session["id"]},
    )


def test_pair_accepts_device_name_scopes_and_expiry() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    body = data(
        client,
        "mutation($input: PairInput!) { pair(input: $input) { scopes expires_at } }",
        "pair",
        {"input": {"device_name": "pixel", "scopes": ["files-read"], "expires_in_s": 3600}},
        headers={"X-Termx-Passcode": "secret"},
    )
    assert body["scopes"] == ["files-read"]
    assert body["expires_at"] is not None
    devices = data(
        client, "{ devices { device_name scopes } }", "devices",
        headers={"X-Termx-Passcode": "secret"},
    )
    assert devices[0]["device_name"] == "pixel"
    assert devices[0]["scopes"] == ["files-read"]


def test_pair_default_scopes_exclude_host_admin() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    body = data(
        client, "mutation { pair { scopes expires_at } }", "pair",
        headers={"X-Termx-Passcode": "secret"},
    )
    scopes = body["scopes"]
    assert "host-admin" not in scopes
    assert "files-read" in scopes and "terminal-control" in scopes
    assert body["expires_at"] is None


def test_scope_enforcement_matrix() -> None:
    """A narrowly-scoped token gets 403 outside its scope; passcode gets all."""
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    token = data(
        client,
        "mutation($input: PairInput!) { pair(input: $input) { token } }",
        "pair",
        {"input": {"scopes": ["files-read", "machine-view"]}},
        headers={"X-Termx-Passcode": "secret"},
    )["token"]
    auth = {"X-Termx-Passcode": token}
    data(client, "{ projects { id } }", headers=auth)
    data(client, "{ activity { activity { id } } }", headers=auth)
    assert err_status(client, "{ sessions { id } }", headers=auth) == 403
    assert err_status(client, "{ displays { displays { id } } }", headers=auth) == 403
    assert err_status(client, "{ devices { id } }", headers=auth) == 403
    assert err_status(client, "{ tunnels { active } }", headers=auth) == 403
    assert (
        err_status(client, "mutation { create_session { id } }", headers=auth) == 403
    )
    # Bad/unknown credentials still 401.
    assert (
        err_status(client, "{ projects { id } }", headers={"X-Termx-Passcode": "bad"})
        == 401
    )
    admin = {"X-Termx-Passcode": "secret"}
    data(client, "{ sessions { id } }", headers=admin)
    data(client, "{ devices { id } }", headers=admin)
    data(client, "{ displays { displays { id } } }", headers=admin)


def test_device_scopes_endpoint_grants_admin_explicitly() -> None:
    """Explicit re-scope is the documented path to host-admin for old devices."""
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    admin = {"X-Termx-Passcode": "secret"}
    token = data(
        client,
        "mutation($input: PairInput!) { pair(input: $input) { token } }",
        "pair",
        {"input": {"scopes": ["files-read"]}},
        headers=admin,
    )["token"]
    auth = {"X-Termx-Passcode": token}
    assert err_status(client, "{ devices { id } }", headers=auth) == 403

    device_id = data(client, "{ devices { id } }", "devices", headers=admin)[0]["id"]
    # The device cannot escalate itself.
    assert (
        err_status(
            client,
            "mutation($id: String!) { set_device_scopes(device_id: $id, scopes: [\"host-admin\"]) { id } }",
            {"id": device_id},
            auth,
        )
        == 403
    )
    # Admin grants host-admin explicitly.
    device = data(
        client,
        "mutation($id: String!) { set_device_scopes(device_id: $id, scopes: [\"host-admin\", \"files-read\"]) { scopes } }",
        "set_device_scopes",
        {"id": device_id},
        admin,
    )
    assert device["scopes"] == ["host-admin", "files-read"]
    data(client, "{ devices { id } }", headers=auth)
    data(client, "{ audit }", headers=auth)
    assert (
        err_status(
            client,
            "mutation { set_device_scopes(device_id: \"nope\", scopes: [\"files-read\"]) { id } }",
            headers=admin,
        )
        == 404
    )


def test_passcode_holds_every_v2_scope() -> None:
    """Passcode keeps administrative recovery behavior under scopes v2."""
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    admin = {"X-Termx-Passcode": "secret"}
    for query in (
        "{ sessions { id } }",
        "{ projects { id } }",
        "{ devices { id } }",
        "{ audit }",
        "{ displays { displays { id } } }",
        "{ tunnels { active } }",
        "{ machine { hostname } }",
        "{ activity { activity { id } } }",
    ):
        res = gql(client, query, headers=admin)
        body = res.json()
        assert res.status_code == 200 and not body.get("errors"), (query, body)
    body = data(client, "{ health { capabilities } }", "health")
    assert body["capabilities"]["device_scopes_v2"] is True


def test_connect_pairing_surface_requires_admin() -> None:
    """connect_info exposes the raw passcode + QR — only host-admin may read it."""
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    admin = {"X-Termx-Passcode": "secret"}
    token = data(
        client, "mutation { pair { token } }", "pair", headers=admin
    )["token"]
    auth = {"X-Termx-Passcode": token}
    assert err_status(client, "{ connect_info { connect_url } }", headers=auth) == 403
    # The QR svg remains a raw binary route.
    assert client.get("/api/connect/qr.svg", headers=auth).status_code == 403
    body = data(
        client, "{ connect_info { connect_url passcode } }", "connect_info",
        headers=admin,
    )
    assert "passcode" in body
