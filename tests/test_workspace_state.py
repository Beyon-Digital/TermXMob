from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from _gql import data, err_status

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


_WORKSPACE = "{ workspace { saved_at sessions { title shell cwd } } }"
_SAVE = (
    "mutation($sessions: [WorkspaceSessionInput!]!) "
    "{ save_workspace(sessions: $sessions) { sessions { title } } }"
)
_RESTORE = (
    "mutation { restore_workspace { restored already_running sessions { id title cwd } } }"
)
_SESSIONS = "{ sessions { id } }"


def test_workspace_api_saves_and_restores(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    headers = {"X-Termx-Passcode": "secret"}
    work = tmp_path / "work"
    work.mkdir()

    empty = data(client, _WORKSPACE, "workspace", headers=headers)
    assert empty["sessions"] == []

    saved = data(
        client, _SAVE, "save_workspace",
        {"sessions": [{"title": "shell one", "shell": "/bin/sh", "cwd": str(work)}]},
        headers,
    )
    assert saved["sessions"][0]["title"] == "shell one"

    restored = data(client, _RESTORE, "restore_workspace", headers=headers)
    assert restored["restored"] == 1
    snapshot = restored["sessions"][0]
    assert snapshot["title"] == "shell one"
    assert Path(snapshot["cwd"]).name == "work"

    live = data(client, _SESSIONS, "sessions", headers=headers)
    assert [item["id"] for item in live] == [snapshot["id"]]

    # A second client connection must reuse the host-owned live PTY instead of
    # replaying the persisted spec into a duplicate terminal.
    repeated = data(client, _RESTORE, "restore_workspace", headers=headers)
    assert repeated["restored"] == 0
    assert repeated["already_running"] == 1
    assert [item["id"] for item in repeated["sessions"]] == [snapshot["id"]]
    assert len(data(client, _SESSIONS, "sessions", headers=headers)) == 1

    data(
        client,
        'mutation($id: String!) { delete_session(session_id: $id) { ok } }',
        "delete_session", {"id": snapshot["id"]}, headers,
    )

    assert data(client, _WORKSPACE, "workspace", headers=headers) is not None
    assert err_status(client, _WORKSPACE) == 401


def test_restore_falls_back_on_missing_shell_and_cwd(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    headers = {"X-Termx-Passcode": "secret"}
    data(
        client, _SAVE, "save_workspace",
        {"sessions": [{"title": "gone", "shell": "/nope/missing-shell", "cwd": str(tmp_path / "missing-dir")}]},
        headers,
    )
    restored = data(client, _RESTORE, "restore_workspace", headers=headers)
    assert restored["restored"] == 1
    snapshot = restored["sessions"][0]
    assert snapshot["title"] == "gone"
    assert Path(snapshot["cwd"]).is_dir()
    data(
        client,
        'mutation($id: String!) { delete_session(session_id: $id) { ok } }',
        "delete_session", {"id": snapshot["id"]}, headers,
    )


def test_workspace_caps_session_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    store = ConfigStore()
    many = [WorkspaceSession(title=f"s{index}") for index in range(50)]
    saved = store.save_workspace(many)
    assert len(saved.sessions) == 20
