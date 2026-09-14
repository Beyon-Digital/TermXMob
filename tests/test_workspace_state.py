from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from termx.app import AppState, create_app
from termx.config import ConfigStore, WorkspaceSession


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


def test_workspace_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    store = ConfigStore()
    assert store.get_workspace().sessions == []

    saved = store.save_workspace(
        [
            WorkspaceSession(title="api", shell="/bin/zsh", cwd=str(tmp_path)),
            WorkspaceSession(title="", shell="", cwd=""),
        ]
    )
    assert len(saved.sessions) == 2
    assert saved.saved_at > 0

    reloaded = ConfigStore()
    entries = reloaded.get_workspace().sessions
    assert [item.title for item in entries] == ["api", ""]
    assert entries[0].shell == "/bin/zsh"
    assert entries[0].cwd == str(tmp_path)

    reloaded.save_workspace([])
    assert ConfigStore().get_workspace().sessions == []


def test_workspace_api_saves_and_restores(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()

    empty = client.get("/api/workspace", params={"k": "secret"}).json()
    assert empty == {"sessions": [], "saved_at": 0}

    saved = client.put(
        "/api/workspace",
        params={"k": "secret"},
        json={"sessions": [{"title": "shell one", "shell": "/bin/sh", "cwd": str(work)}]},
    )
    assert saved.status_code == 200
    assert saved.json()["sessions"][0]["title"] == "shell one"

    restored = client.post("/api/workspace/restore", params={"k": "secret"}).json()
    assert restored["restored"] == 1
    snapshot = restored["sessions"][0]
    assert snapshot["title"] == "shell one"
    assert Path(snapshot["cwd"]).name == "work"

    live = client.get("/api/sessions", params={"k": "secret"}).json()["sessions"]
    assert [item["id"] for item in live] == [snapshot["id"]]
    client.delete(f"/api/sessions/{snapshot['id']}", params={"k": "secret"})

    assert client.get("/api/workspace", params={"k": "secret"}).status_code == 200
    assert client.get("/api/workspace").status_code == 401


def test_restore_falls_back_on_missing_shell_and_cwd(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    client.put(
        "/api/workspace",
        params={"k": "secret"},
        json={
            "sessions": [
                {"title": "gone", "shell": "/nope/missing-shell", "cwd": str(tmp_path / "missing-dir")}
            ]
        },
    )
    restored = client.post("/api/workspace/restore", params={"k": "secret"}).json()
    assert restored["restored"] == 1
    snapshot = restored["sessions"][0]
    assert snapshot["title"] == "gone"
    assert Path(snapshot["cwd"]).is_dir()
    client.delete(f"/api/sessions/{snapshot['id']}", params={"k": "secret"})


def test_workspace_caps_session_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    store = ConfigStore()
    many = [WorkspaceSession(title=f"s{index}") for index in range(50)]
    saved = store.save_workspace(many)
    assert len(saved.sessions) == 20
