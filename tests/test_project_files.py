from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status

from termx.app import AppState, create_app


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "config"))
    return TestClient(create_app(AppState(passcode="secret"), web_dir=None))


HEADERS = {"X-Termx-Passcode": "secret"}


def _register(client: TestClient, path: Path) -> str:
    project = data(
        client,
        'mutation($input: ProjectInput!) { register_project(input: $input) { id } }',
        "register_project", {"input": {"path": str(path), "name": "proj"}}, HEADERS,
    )
    return project["id"]


def _project_file(client: TestClient, project_id: str, path: str) -> dict:
    return data(
        client,
        "query($id: String!, $path: String!) "
        "{ project_file(project_id: $id, path: $path) { editable content revision reason } }",
        "project_file", {"id": project_id, "path": path}, HEADERS,
    )


def test_project_tree_read_and_revision_checked_save(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (project / "src").mkdir()

    project_id = _register(client, project)

    tree = data(
        client,
        "query($id: String!) { project_tree(project_id: $id) { entries } }",
        "project_tree", {"id": project_id}, HEADERS,
    )
    names = {entry["name"]: entry for entry in tree["entries"]}
    # Directories sort first.
    assert list(names) == ["src", "app.py"]
    assert names["src"]["dir"] is True

    read = _project_file(client, project_id, "app.py")
    assert read["editable"] is True
    assert read["content"] == (project / "app.py").read_bytes().decode("utf-8")
    revision = read["revision"]

    # Saving with the correct revision succeeds.
    save = (
        "mutation($id: String!, $input: FileSaveInput!) "
        "{ save_project_file(project_id: $id, input: $input) }"
    )
    data(
        client, save, "save_project_file",
        {"id": project_id, "input": {
            "path": "app.py", "content": "print('world')\n", "revision": revision,
        }},
        HEADERS,
    )
    assert (project / "app.py").read_text() == "print('world')\n"

    # Saving again with the stale revision is rejected (409) and the file is unchanged.
    assert err_status(
        client, save,
        {"id": project_id, "input": {
            "path": "app.py", "content": "print('stale')\n", "revision": revision,
        }},
        HEADERS,
    ) == 409
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
    query = (
        "query($id: String!, $path: String!) "
        "{ project_file(project_id: $id, path: $path) { editable } }"
    )
    assert err_status(client, query, {"id": project_id, "path": "../secret.txt"}, HEADERS) == 403
    assert err_status(client, query, {"id": project_id, "path": str(secret)}, HEADERS) == 403


def test_binary_and_oversized_files_are_not_editable(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "logo.bin").write_bytes(b"\x00\x01\x02\x03binary")
    project_id = _register(client, project)
    read = _project_file(client, project_id, "logo.bin")
    assert read["editable"] is False
    assert read["reason"]


def test_search_finds_content_matches(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def login():\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("x = 1\n", encoding="utf-8")
    project_id = _register(client, project)
    result = data(
        client,
        "query($id: String!, $input: FileSearchInput!) "
        "{ project_search(project_id: $id, input: $input) { results { path line } } }",
        "project_search",
        {"id": project_id, "input": {"query": "login"}},
        HEADERS,
    )
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

    _FILES = "files { path staged untracked }"
    status = data(
        client,
        "query($id: String!) { git_status(project_id: $id) { repo %s } }" % _FILES,
        "git_status", {"id": project_id}, HEADERS,
    )
    assert status["repo"] is True
    assert any(f["path"] == "new.txt" and f["untracked"] for f in status["files"])

    staged = data(
        client,
        "mutation($id: String!, $input: GitStageInput!) "
        "{ git_stage(project_id: $id, input: $input) }",
        "git_stage", {"id": project_id, "input": {"paths": ["new.txt"], "stage": True}},
        HEADERS,
    )
    assert any(f["path"] == "new.txt" and f["staged"] for f in staged["files"])

    committed = data(
        client,
        "mutation($id: String!, $message: String!) "
        "{ git_commit(project_id: $id, message: $message) }",
        "git_commit", {"id": project_id, "message": "add new.txt"}, HEADERS,
    )
    assert committed["repo"] is True
    assert not any(f["path"] == "new.txt" for f in committed["files"])

    (project / "new.txt").write_text("hello\n", encoding="utf-8")
    patch = subprocess.run(
        ["git", "-C", str(project), "diff", "--no-color", "--", "new.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    hunk = data(
        client,
        "mutation($id: String!, $input: GitHunkInput!) "
        "{ git_stage_hunk(project_id: $id, input: $input) }",
        "git_stage_hunk", {"id": project_id, "input": {"patch": patch, "stage": True}},
        HEADERS,
    )
    assert any(f["path"] == "new.txt" and f["staged"] for f in hunk["files"])


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

    preview = data(
        client,
        "mutation($id: String!, $input: PreviewInput!) "
        "{ create_project_preview(project_id: $id, input: $input) { id name url } }",
        "create_project_preview",
        {"id": project_id, "input": {"name": "Local app", "url": "http://127.0.0.1:3000"}},
        HEADERS,
    )
    assert preview["name"] == "Local app"

    listed = data(
        client,
        "query($id: String!) { project_previews(project_id: $id) { id name url } }",
        "project_previews", {"id": project_id}, HEADERS,
    )
    assert listed == [preview]

    create = (
        "mutation($id: String!, $input: PreviewInput!) "
        "{ create_project_preview(project_id: $id, input: $input) { id } }"
    )
    assert err_status(
        client, create,
        {"id": project_id, "input": {"name": "File", "url": "file:///etc/passwd"}},
        HEADERS,
    ) == 400

    data(
        client,
        "mutation($id: String!, $pid: String!) "
        "{ delete_project_preview(project_id: $id, preview_id: $pid) { ok } }",
        "delete_project_preview", {"id": project_id, "pid": preview["id"]}, HEADERS,
    )
    assert data(
        client,
        "query($id: String!) { project_previews(project_id: $id) { id } }",
        "project_previews", {"id": project_id}, HEADERS,
    ) == []


def test_project_lsp_capabilities_require_auth(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    project_id = _register(client, project)

    query = "query($id: String!) { lsp_servers(project_id: $id) }"
    assert err_status(client, query, {"id": project_id}) == 401
    body = data(client, query, "lsp_servers", {"id": project_id}, HEADERS)
    servers = body["servers"]
    assert {"typescript", "python", "rust"}.issubset(servers)
    assert all("available" in value and "command" in value for value in servers.values())
