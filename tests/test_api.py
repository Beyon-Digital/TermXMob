import re

import time

from fastapi.testclient import TestClient

from termx.app import AppState, create_app
from termx.config import ConfigStore
from termx.net import http_urls, qr_ascii


def test_health_open() -> None:
    client = TestClient(create_app(AppState(passcode=None), web_dir=None))
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["passcode_required"] is False


def test_sessions_require_passcode() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"X-Termx-Passcode": "nope"}).status_code == 401
    ok = client.get("/api/sessions", headers={"X-Termx-Passcode": "secret"})
    assert ok.status_code == 200
    assert ok.json()["sessions"] == []


def test_http_urls_and_qr_are_fast() -> None:
    start = time.monotonic()
    urls = http_urls(8787)
    assert any(u.startswith("http://127.0.0.1:8787") for u in urls)
    qr_ascii(urls[0])
    assert time.monotonic() - start < 2


def test_builtin_ui_served() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    res = client.get("/")
    assert res.status_code == 200
    assert "Termx" in res.text
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
    (web_dir / "index.html").write_text("<html><body>Exported Termx</body></html>", encoding="utf-8")
    (web_dir / "asset.js").write_text("export {};", encoding="utf-8")
    client = TestClient(create_app(AppState(), web_dir=web_dir))
    assert client.get("/").text == "<html><body>Exported Termx</body></html>"
    assert client.get("/asset.js").text == "export {};"
    assert client.get("/workspace").text == "<html><body>Exported Termx</body></html>"


def test_pair_issues_token() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert client.post("/api/pair").status_code == 401
    res = client.post("/api/pair", headers={"X-Termx-Passcode": "secret"})
    assert res.status_code == 200
    token = res.json()["token"]
    assert token
    listed = client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200


def test_health_includes_capabilities() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["capabilities"]["saved_commands"] is True
    assert body["capabilities"]["dynamic_tunnels"] is True
    assert "cloudflare" in body["capabilities"]["providers"]


def test_health_does_not_fingerprint_host() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["passcode_required"] is True
    for field in ("hostname", "os", "tunnel"):
        assert field not in body
    machine = client.get("/api/machine", headers={"X-Termx-Passcode": "secret"}).json()
    assert machine["hostname"]
    assert machine["os"]
    assert "tunnel" in machine


def test_machine_reports_rtc_availability() -> None:
    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    body = client.get("/api/machine", headers={"X-Termx-Passcode": "secret"}).json()
    assert body["capabilities"]["webrtc"] == state.rtc.available()
    health = client.get("/api/health").json()
    assert health["capabilities"]["webrtc"] == state.rtc.available()


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
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


def test_commands_and_preferences_roundtrip(tmp_path) -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    created = client.post("/api/commands", json={"name": "Hello", "command": "echo hi"})
    assert created.status_code == 200
    item = created.json()
    listed = client.get("/api/commands").json()["commands"]
    assert listed[0]["id"] == item["id"]
    prefs = client.get("/api/preferences").json()
    assert prefs["cwd"]
    assert prefs["shell"]
    bad = client.put("/api/preferences", json={"cwd": str(tmp_path / "nope")})
    assert bad.status_code == 400
    tunnels = client.get("/api/tunnels").json()
    assert "providers" in tunnels
    displays = client.get("/api/displays").json()
    assert "displays" in displays
    virtual = client.post("/api/displays/virtual", json={"width": 800, "height": 600})
    assert virtual.status_code in {200, 501}
    rtc = client.post("/api/desktop/rtc/offer", json={"offer": {"type": "offer", "sdp": "v=0"}})
    assert rtc.status_code == 503


def test_directories_and_fs_roundtrip(tmp_path) -> None:
    state = AppState()
    state.store = ConfigStore(tmp_path / "config.json")
    client = TestClient(create_app(state, web_dir=None))
    nested = tmp_path / "src"
    nested.mkdir()
    listing = client.get("/api/fs", params={"path": str(tmp_path)})
    assert listing.status_code == 200
    assert any(item["name"] == "src" for item in listing.json()["entries"])
    created = client.post("/api/directories", json={"name": "Src", "path": str(nested)})
    assert created.status_code == 200
    item = created.json()
    used = client.post(f"/api/directories/{item['id']}/use")
    assert used.status_code == 200
    assert used.json()["cwd"] == str(nested.resolve())
    session = client.post("/api/sessions", json={"cols": 80, "rows": 24, "cwd": str(nested)})
    assert session.status_code == 200
    assert session.json()["cwd"] == str(nested.resolve())
    assert client.delete(f"/api/directories/{item['id']}").status_code == 200
    client.delete(f"/api/sessions/{session.json()['id']}")
