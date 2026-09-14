from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from termx.app import AppState, create_app


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


def test_listing_includes_files_when_requested(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.txt").write_text("hello")
    (work / "sub").mkdir()

    dirs_only = client.get("/api/fs", params={"path": str(work), "k": "secret"}).json()
    assert [entry["name"] for entry in dirs_only["entries"]] == ["sub"]
    assert all(entry.get("dir") for entry in dirs_only["entries"])

    with_files = client.get(
        "/api/fs", params={"path": str(work), "files": 1, "k": "secret"}
    ).json()
    names = [entry["name"] for entry in with_files["entries"]]
    assert names == ["sub", "notes.txt"]
    file_entry = with_files["entries"][1]
    assert file_entry["dir"] is False
    assert file_entry["size"] == 5


def test_download_file(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    target = tmp_path / "report.csv"
    target.write_text("a,b\n1,2\n")

    response = client.get("/api/fs/download", params={"path": str(target), "k": "secret"})
    assert response.status_code == 200
    assert response.text == "a,b\n1,2\n"
    assert "report.csv" in response.headers.get("content-disposition", "")

    assert client.get("/api/fs/download", params={"path": str(tmp_path), "k": "secret"}).status_code == 404
    assert client.get("/api/fs/download", params={"path": str(tmp_path / "nope"), "k": "secret"}).status_code == 404
    assert client.get("/api/fs/download", params={"path": str(target)}).status_code == 401


def test_upload_file_streams_to_directory(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    work = tmp_path / "uploads"
    work.mkdir()

    response = client.post(
        "/api/fs/upload",
        params={"dir": str(work), "k": "secret"},
        headers={"x-termx-name": "hello%20world.txt", "content-type": "application/octet-stream"},
        content=b"payload-bytes",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "hello world.txt"
    assert body["size"] == len(b"payload-bytes")
    assert (work / "hello world.txt").read_bytes() == b"payload-bytes"

    # path components are stripped from the name
    second = client.post(
        "/api/fs/upload",
        params={"dir": str(work), "k": "secret"},
        headers={"x-termx-name": "..%2F..%2Fescape.txt"},
        content=b"x",
    )
    assert second.status_code == 200
    assert second.json()["name"] == "escape.txt"
    assert (work / "escape.txt").is_file()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_upload_collision_renames(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    work = tmp_path / "dup"
    work.mkdir()
    (work / "file.txt").write_text("original")

    first = client.post(
        "/api/fs/upload",
        params={"dir": str(work), "k": "secret"},
        headers={"x-termx-name": "file.txt"},
        content=b"one",
    )
    second = client.post(
        "/api/fs/upload",
        params={"dir": str(work), "k": "secret"},
        headers={"x-termx-name": "file.txt"},
        content=b"two",
    )
    assert first.json()["name"] == "file (1).txt"
    assert second.json()["name"] == "file (2).txt"
    assert (work / "file.txt").read_text() == "original"
    assert (work / "file (1).txt").read_bytes() == b"one"
    assert (work / "file (2).txt").read_bytes() == b"two"


def test_upload_requires_name_and_valid_dir(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    assert (
        client.post(
            "/api/fs/upload",
            params={"dir": str(work), "k": "secret"},
            content=b"x",
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/fs/upload",
            params={"dir": str(tmp_path / "missing"), "k": "secret"},
            headers={"x-termx-name": "a.txt"},
            content=b"x",
        ).status_code
        == 400
    )
    assert os.path.isdir(work)
