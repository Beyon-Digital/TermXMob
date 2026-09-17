from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termx.app import AppState, create_app


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


def _register(client: TestClient, path: Path) -> str:
    response = client.post("/api/projects", params={"k": "secret"}, json={"path": str(path), "name": "proj"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_project_tree_read_and_revision_checked_save(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (project / "src").mkdir()

    project_id = _register(client, project)

    tree = client.get(f"/api/projects/{project_id}/tree", params={"k": "secret"}).json()
    names = {entry["name"]: entry for entry in tree["entries"]}
    # Directories sort first.
    assert list(names) == ["src", "app.py"]
    assert names["src"]["dir"] is True

    read = client.get(f"/api/projects/{project_id}/file", params={"path": "app.py", "k": "secret"}).json()
    assert read["editable"] is True
    assert read["content"] == (project / "app.py").read_bytes().decode("utf-8")
    revision = read["revision"]

    # Saving with the correct revision succeeds.
    saved = client.put(
        f"/api/projects/{project_id}/file",
        params={"k": "secret"},
        json={"path": "app.py", "content": "print('world')\n", "revision": revision},
    )
    assert saved.status_code == 200, saved.text
    assert (project / "app.py").read_text() == "print('world')\n"

    # Saving again with the stale revision is rejected (409) and the file is unchanged.
    conflict = client.put(
        f"/api/projects/{project_id}/file",
        params={"k": "secret"},
        json={"path": "app.py", "content": "print('stale')\n", "revision": revision},
    )
    assert conflict.status_code == 409
    assert (project / "app.py").read_text() == "print('world')\n"


def test_project_path_traversal_is_rejected(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")
    project_id = _register(client, project)

    # Absolute path and parent traversal both blocked.
    assert client.get(f"/api/projects/{project_id}/file", params={"path": "../secret.txt", "k": "secret"}).status_code == 403
    assert client.get(f"/api/projects/{project_id}/file", params={"path": str(secret), "k": "secret"}).status_code == 403


def test_binary_and_oversized_files_are_not_editable(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "logo.bin").write_bytes(b"\x00\x01\x02\x03binary")
    project_id = _register(client, project)
    read = client.get(f"/api/projects/{project_id}/file", params={"path": "logo.bin", "k": "secret"}).json()
    assert read["editable"] is False
    assert "reason" in read


def test_search_finds_content_matches(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def login():\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("x = 1\n", encoding="utf-8")
    project_id = _register(client, project)
    result = client.post(f"/api/projects/{project_id}/search", params={"k": "secret"}, json={"query": "login"}).json()
    assert any(r["path"] == "a.py" and r["line"] == 1 for r in result["results"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_status_stage_and_commit(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
    (project / "new.txt").write_text("hi\n", encoding="utf-8")
    project_id = _register(client, project)

    status = client.get(f"/api/projects/{project_id}/git/status", params={"k": "secret"}).json()
    assert status["repo"] is True
    assert any(f["path"] == "new.txt" and f["untracked"] for f in status["files"])

    staged = client.post(
        f"/api/projects/{project_id}/git/stage",
        params={"k": "secret"},
        json={"paths": ["new.txt"], "stage": True},
    ).json()
    assert any(f["path"] == "new.txt" and f["staged"] for f in staged["files"])

    committed = client.post(
        f"/api/projects/{project_id}/git/commit",
        params={"k": "secret"},
        json={"message": "add new.txt"},
    ).json()
    assert committed["repo"] is True
    assert not any(f["path"] == "new.txt" for f in committed["files"])

    (project / "new.txt").write_text("hello\n", encoding="utf-8")
    patch = subprocess.run(
        ["git", "-C", str(project), "diff", "--no-color", "--", "new.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    hunk = client.post(
        f"/api/projects/{project_id}/git/hunk",
        params={"k": "secret"},
        json={"patch": patch, "stage": True},
    )
    assert hunk.status_code == 200, hunk.text
    assert any(f["path"] == "new.txt" and f["staged"] for f in hunk.json()["files"])


def test_project_import_download_and_duplicate_protection(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "assets").mkdir()
    project_id = _register(client, project)

    imported = client.post(
        f"/api/projects/{project_id}/upload",
        params={"k": "secret", "path": "assets"},
        headers={"x-termx-name": "hello.txt"},
        content=b"hello\n",
    )
    assert imported.status_code == 200, imported.text
    assert (project / "assets" / "hello.txt").read_bytes() == b"hello\n"

    duplicate = client.post(
        f"/api/projects/{project_id}/upload",
        params={"k": "secret", "path": "assets"},
        headers={"x-termx-name": "hello.txt"},
        content=b"replacement",
    )
    assert duplicate.status_code == 409
    assert (project / "assets" / "hello.txt").read_bytes() == b"hello\n"

    downloaded = client.get(
        f"/api/projects/{project_id}/download",
        params={"k": "secret", "path": "assets/hello.txt"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello\n"


def test_project_preview_crud_and_validation(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    project_id = _register(client, project)

    created = client.post(
        f"/api/projects/{project_id}/previews",
        params={"k": "secret"},
        json={"name": "Local app", "url": "http://127.0.0.1:3000"},
    )
    assert created.status_code == 200, created.text
    preview = created.json()
    assert preview["name"] == "Local app"

    listed = client.get(f"/api/projects/{project_id}/previews", params={"k": "secret"}).json()
    assert listed["previews"] == [preview]

    invalid = client.post(
        f"/api/projects/{project_id}/previews",
        params={"k": "secret"},
        json={"name": "File", "url": "file:///etc/passwd"},
    )
    assert invalid.status_code == 400

    removed = client.delete(
        f"/api/projects/{project_id}/previews/{preview['id']}",
        params={"k": "secret"},
    )
    assert removed.status_code == 200
    assert client.get(f"/api/projects/{project_id}/previews", params={"k": "secret"}).json()["previews"] == []


def test_project_lsp_capabilities_require_auth(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    project_id = _register(client, project)

    assert client.get(f"/api/projects/{project_id}/lsp").status_code == 401
    response = client.get(f"/api/projects/{project_id}/lsp", params={"k": "secret"})
    assert response.status_code == 200
    servers = response.json()["servers"]
    assert {"typescript", "python", "rust"}.issubset(servers)
    assert all("available" in value and "command" in value for value in servers.values())
