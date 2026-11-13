"""Rooted shell paths must keep the selected worktree boundary on each host."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from termx.agent import policy


@pytest.mark.parametrize(
    ("command", "candidates"),
    [
        ("type /", ["/"]),
        ("type /etc/hosts", ["/etc/hosts"]),
        (r"type \etc\hosts", [r"\etc\hosts"]),
        (r"type \outside.txt", [r"\outside.txt"]),
        ('type "/outside.txt"', ["/outside.txt"]),
        ("type '/outside.txt'", ["/outside.txt"]),
        ('type "/Program Files/notes.txt"', ["/Program Files/notes.txt"]),
        (r"type C:\worktree\notes.txt", [r"C:\worktree\notes.txt"]),
        (r"type \\server\share\notes.txt", [r"\\server\share\notes.txt"]),
        (r"type ~\notes.txt", [r"~\notes.txt"]),
        ("whoami /groups", []),
        ("net user /add", []),
    ],
)
def test_windows_rooted_path_candidates(command, candidates):
    # Raw Windows parsing is checked on every CI host, without changing os.name.
    assert policy._windows_candidates(command) == candidates


@pytest.mark.parametrize("target", ["root", "child", "sibling", "parent", "drive_root"])
def test_native_rooted_paths_respect_worktree_boundary(tmp_path, target):
    root = (tmp_path / "worktree").resolve()
    paths = {
        "root": root,
        "child": root / "notes.txt",
        "sibling": root.with_name("worktree-other") / "notes.txt",
        "parent": root.parent,
        "drive_root": Path(root.anchor),
    }
    path = paths[target]
    # On Windows this deliberately omits the drive: /a/b and \a\b are
    # rooted on the selected cwd's drive. On POSIX it remains an absolute path.
    candidate = path.as_posix()[len(path.drive):]
    representations = [candidate]
    if os.name == "nt":
        representations.append(candidate.replace("/", "\\"))
    for value in representations:
        outside = policy._outside_paths(f'type "{value}"', root)
        assert bool(outside) is (target not in {"root", "child"}), value
    # Drive-qualified paths keep the same containment result on Windows.
    assert bool(policy._outside_paths(f'type "{path}"', root)) is (
        target not in {"root", "child"}
    )


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows path semantics")
def test_windows_rooted_path_uses_selected_worktree_drive_before_resolution(monkeypatch):
    process_drive = Path.cwd().drive.casefold()
    other_drive = "D:" if process_drive != "d:" else "C:"
    root = Path(f"{other_drive}/termx-policy-worktree")
    # Check the path handed to native resolution without requiring a second
    # mounted drive. The tmp_path cases above exercise real filesystem resolution.
    resolved = []

    def resolve(path, *, strict=False):
        resolved.append(path)
        return path

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not policy._outside_paths("type /termx-policy-worktree/notes.txt", root)
    assert not policy._outside_paths(r"type \termx-policy-worktree\notes.txt", root)
    assert policy._outside_paths("type /termx-policy-worktree-other/notes.txt", root)
    assert resolved == [
        root / "notes.txt", root / "notes.txt", root.with_name("termx-policy-worktree-other") / "notes.txt",
    ]


@pytest.mark.parametrize(
    "candidate",
    [
        "/bin/termx-policy-fixture", "/usr/bin/termx-policy-fixture",
        "/opt/homebrew/bin/termx-policy-fixture", "/dev/null",
    ],
)
def test_windows_rooted_paths_do_not_use_posix_exemptions(tmp_path, monkeypatch, candidate):
    monkeypatch.setattr(policy, "_WINDOWS", True)
    assert policy._outside_paths(f'type "{candidate}"', tmp_path.resolve())


@pytest.mark.parametrize("candidate", ["/bin/echo", "/usr/bin/env", "/opt/homebrew/bin/tool", "/dev/null"])
def test_posix_executable_and_pseudo_path_exemptions_remain(tmp_path, monkeypatch, candidate):
    monkeypatch.setattr(policy, "_WINDOWS", False)
    assert policy._outside_paths(f'"{candidate}"', tmp_path.resolve()) == []
