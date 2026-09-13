import json

from fastapi.testclient import TestClient

from termx import notify
from termx.app import AppState, create_app
from termx.desktop import paths as paths_module
from termx.net import qr_svg


def test_notify_is_noop_outside_desktop(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TERMX_DESKTOP", raising=False)
    notify.notify("hello", "world")
    assert capsys.readouterr().out == ""


def test_notify_emits_json_event_in_desktop_mode(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TERMX_DESKTOP", "1")
    notify.notify("hello", "world", kind="success", url="http://127.0.0.1:8787")
    line = capsys.readouterr().out.strip()
    event = json.loads(line)
    assert event == {
        "termx": "notify",
        "title": "hello",
        "body": "world",
        "kind": "success",
        "url": "http://127.0.0.1:8787",
    }


def test_ready_emits_json_event(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TERMX_DESKTOP", "1")
    notify.ready(8787, ["http://127.0.0.1:8787"], None)
    event = json.loads(capsys.readouterr().out.strip())
    assert event["termx"] == "ready"
    assert event["port"] == 8787
    assert event["urls"] == ["http://127.0.0.1:8787"]


def test_health_identifies_termx() -> None:
    client = TestClient(create_app(AppState(passcode=None), web_dir=None))
    assert client.get("/api/health").json()["app"] == "termx"


def test_connect_info_requires_auth_and_reports_qr() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert client.get("/api/connect").status_code == 401
    res = client.get("/api/connect", headers={"X-Termx-Passcode": "secret"})
    assert res.status_code == 200
    body = res.json()
    assert body["passcode"] == "secret"
    assert any(url.startswith("http://127.0.0.1") for url in body["urls"])
    assert "k=secret" in body["connect_url"]
    qr = client.get("/api/connect/qr.svg", headers={"X-Termx-Passcode": "secret"})
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/svg+xml")
    assert qr.text.lstrip().startswith("<?xml") or "<svg" in qr.text


def test_connect_page_is_served() -> None:
    client = TestClient(create_app(AppState(), web_dir=None))
    res = client.get("/_/connect.html")
    assert res.status_code == 200
    assert "Termx" in res.text


def test_notify_endpoint_requires_auth_and_forwards(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []

    def fake_notify(title: str, body: str = "", **_: object) -> None:
        captured.append((title, body))

    monkeypatch.setattr("termx.app.notify.notify", fake_notify)
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert client.post("/api/notify", json={"title": "hi"}).status_code == 401
    res = client.post(
        "/api/notify",
        json={"title": "hi", "body": "there"},
        headers={"X-Termx-Passcode": "secret"},
    )
    assert res.status_code == 200
    assert captured == [("hi", "there")]


def test_shutdown_endpoint_is_hidden_outside_desktop(monkeypatch) -> None:
    monkeypatch.delenv("TERMX_DESKTOP", raising=False)
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    res = client.post("/api/shutdown", headers={"X-Termx-Passcode": "secret"})
    assert res.status_code == 404


def test_shutdown_endpoint_requires_loopback(monkeypatch) -> None:
    monkeypatch.setenv("TERMX_DESKTOP", "1")
    state = AppState(passcode="secret")
    calls: list[str] = []
    state.request_shutdown = lambda: calls.append("stop")
    client = TestClient(create_app(state, web_dir=None))
    res = client.post("/api/shutdown", headers={"X-Termx-Passcode": "secret"})
    assert res.status_code == 403
    assert calls == []


def test_permissions_endpoint_roundtrip() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    assert client.get("/api/permissions").status_code == 401
    res = client.get("/api/permissions", headers={"X-Termx-Passcode": "secret"})
    assert res.status_code == 200
    assert isinstance(res.json(), dict)
    requested = client.post(
        "/api/permissions/request",
        json={"which": ["screen_recording"]},
        headers={"X-Termx-Passcode": "secret"},
    )
    assert requested.status_code == 200


def test_desktop_display_selection_over_socket() -> None:
    from termx.desktop.capture import close_capture

    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    try:
        with client.websocket_connect("/api/desktop/session?k=secret") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["displays"]
            target = hello["selected_display"]
            assert target
            ws.send_json({"type": "display", "id": target})
            ack = None
            for _ in range(40):
                message = ws.receive()
                if message.get("text"):
                    payload = json.loads(message["text"])
                    if payload.get("type") == "display":
                        ack = payload
                        break
            assert ack is not None
            assert ack["id"] == target
            assert ack["selected_display"] == target
    finally:
        close_capture()


def test_qr_svg_renders_markup() -> None:
    svg = qr_svg("http://127.0.0.1:8787/?k=abc")
    assert "svg" in svg.lower()
    assert len(svg) > 100


def test_helpers_bin_dir_prefers_bundled(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "helpers" / "macos" / "bin"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(paths_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths_module.helpers_bin_dir() == bundled


def test_augment_path_prepends_missing_dirs(monkeypatch, tmp_path) -> None:
    import termx.hostenv as hostenv

    extra = tmp_path / "tools"
    extra.mkdir()
    monkeypatch.setattr(hostenv, "_windows_candidates", lambda: [str(extra)])
    monkeypatch.setattr(hostenv, "_darwin_candidates", lambda: [str(extra)])
    if hostenv.os.name != "nt":
        monkeypatch.setattr(hostenv.sys, "platform", "darwin")
    monkeypatch.setenv("PATH", "/usr/bin")
    hostenv.augment_path()
    assert hostenv.os.environ["PATH"].split(hostenv.os.pathsep)[0] == str(extra)


def test_start_server_falls_back_when_port_taken() -> None:
    import asyncio
    import socket

    from termx.app import AppState, create_app
    from termx.cli import _start_server

    async def inner() -> None:
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        started = await _start_server(create_app(AppState(), web_dir=None), "127.0.0.1", port)
        try:
            assert started is not None
            server, task, actual = started
            assert actual != port
            server.should_exit = True
            await task
        finally:
            blocker.close()

    asyncio.run(inner())
