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
    assert "hostname" in body
    assert body["capabilities"]["saved_commands"] is True
    assert body["capabilities"]["dynamic_tunnels"] is True
    assert "cloudflare" in body["capabilities"]["providers"]


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
