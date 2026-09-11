import json
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
    body = listing.json()
    assert body["path"] == str(tmp_path.resolve())
    assert any(item["name"] == "src" for item in body["entries"])
    created = client.post("/api/directories", json={"name": "Src", "path": str(nested)})
    assert created.status_code == 200
    item = created.json()
    listed = client.get("/api/directories").json()
    assert listed["directories"][0]["id"] == item["id"]
    used = client.post(f"/api/directories/{item['id']}/use")
    assert used.status_code == 200
    assert used.json()["cwd"] == str(nested.resolve())
    prefs = client.get("/api/preferences").json()
    assert prefs["cwd"] == str(nested.resolve())
    session = client.post("/api/sessions", json={"cols": 80, "rows": 24, "cwd": str(nested)})
    assert session.status_code == 200
    assert session.json()["cwd"] == str(nested.resolve())
    assert client.delete(f"/api/directories/{item['id']}").status_code == 200
    client.delete(f"/api/sessions/{session.json()['id']}")


def test_pty_websocket_echo() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    created = client.post("/api/sessions", json={"cols": 80, "rows": 24})
    assert created.status_code == 200
    session_id = created.json()["id"]
    assert created.json()["exited"] is False
    try:
        with client.websocket_connect(f"/api/sessions/{session_id}/pty") as ws:
            ws.send_text(json.dumps({"type": "input", "data": "echo HELLO_PTY\n"}))
            deadline = time.monotonic() + 3
            chunks = b""
            while time.monotonic() < deadline:
                message = ws.receive()
                if message.get("bytes"):
                    chunks += message["bytes"]
                elif message.get("text"):
                    try:
                        payload = json.loads(message["text"])
                    except json.JSONDecodeError:
                        chunks += message["text"].encode()
                    else:
                        assert payload.get("type") != "exit"
                if b"HELLO_PTY" in chunks:
                    break
            assert b"HELLO_PTY" in chunks
    finally:
        client.delete(f"/api/sessions/{session_id}")
