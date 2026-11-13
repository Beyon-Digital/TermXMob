"""New-file writes preserve the supplied bytes and exclusive-create lifecycle."""
from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from termx.agent.providers import ProviderCall
from termx.agent.tools import filesystem
from termx.project_files import ProjectFiles


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    files = ProjectFiles()
    identifier = files.register(str(root))["id"]
    try:
        yield root, SimpleNamespace(read_only=False, project_files=files, project_id=identifier)
    finally:
        files.close()


@pytest.fixture(params=["native", "windows-default"])
def text_newline_host(request, monkeypatch):
    if request.param == "windows-default":
        original = os.fdopen

        def windows_fdopen(fd, mode="r", *args, **kwargs):
            # Exercise Windows' default text translation on every host, while
            # leaving callers' explicit newline choices and binary modes alone.
            if mode == "w" and "newline" not in kwargs:
                kwargs["newline"] = "\r\n"
            return original(fd, mode, *args, **kwargs)

        monkeypatch.setattr(os, "fdopen", windows_fdopen)


def _call(tool, **arguments):
    return ProviderCall(type="function", call_id="exact-write", name=tool, arguments=arguments)


@pytest.mark.parametrize(
    "content",
    ["Líne one\nLine two\n", "Líne one\r\nLine two\r\n", "α\nβ\r\nγ\rδ"],
    ids=["lf", "crlf", "mixed-without-final-newline"],
)
def test_write_file_preserves_explicit_utf8_payload(project, text_newline_host, content):
    root, ctx = project
    result = asyncio.run(filesystem._write_file(
        _call("write_file", path="nested/created.txt", content=content), ctx,
    )).result
    data = content.encode("utf-8")
    assert result["ok"] is True and result["created"] is True
    assert (root / "nested" / "created.txt").read_bytes() == data
    assert result["size"] == len(data)
    assert result["revision"] == hashlib.sha256(data).hexdigest()
    inspected = asyncio.run(filesystem._read_file(
        _call("read_file", path="nested/created.txt"), ctx,
    )).result
    assert inspected["ok"] is True and inspected["content"] == content
    assert inspected["revision"] == result["revision"]


def test_create_exclusive_preserves_existing_target(tmp_path):
    path = tmp_path / "existing.txt"
    original = b"Keep these exact bytes\r\n"
    path.write_bytes(original)
    with pytest.raises(FileExistsError):
        filesystem._create_exclusive(path, "Replacement\n")
    assert path.read_bytes() == original


def test_write_file_losing_exclusive_create_race_preserves_winner(project, monkeypatch):
    root, ctx = project
    create = filesystem._create_exclusive
    original = b"Concurrent creator\r\n"

    def concurrent_create(path, content):
        path.write_bytes(original)
        create(path, content)

    monkeypatch.setattr(filesystem, "_create_exclusive", concurrent_create)
    result = asyncio.run(filesystem._write_file(
        _call("write_file", path="raced.txt", content="Replacement\n"), ctx,
    )).result
    assert result["ok"] is False and result["conflict"] is True
    assert (root / "raced.txt").read_bytes() == original


@pytest.mark.parametrize("failure", ["write", "close"])
def test_create_exclusive_removes_partial_target_and_closes_descriptor(tmp_path, monkeypatch, failure):
    path = tmp_path / "nested" / "failed.txt"
    original_fdopen = os.fdopen
    handles = []
    descriptors = []

    @contextmanager
    def failing_fdopen(fd, *args, **kwargs):
        descriptors.append(fd)
        with original_fdopen(fd, *args, **kwargs) as handle:
            handles.append(handle)
            if failure == "write":
                class PartialWrite:
                    def write(self, content):
                        handle.write(content[:3])
                        handle.flush()
                        raise OSError("Injected write failure")

                yield PartialWrite()
            else:
                yield handle
        if failure == "close":
            raise OSError("Injected close failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fdopen", failing_fdopen)
        with pytest.raises(OSError, match=f"Injected {failure} failure"):
            filesystem._create_exclusive(path, "Attempted content\n")
    assert handles[0].closed
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not path.exists()
    filesystem._create_exclusive(path, "Retry succeeds")
    assert path.read_bytes() == b"Retry succeeds"
