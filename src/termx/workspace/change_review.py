"""Read-only change review for an already-authorized conversation checkout."""
from __future__ import annotations

import difflib
import os
import stat
from pathlib import Path

from fastapi import HTTPException
from termx import git_ops

MAX_FILES = 2000
MAX_PREVIEW_BYTES = 1024 * 1024


def _git(root: str, *args: str):
    # A git-read capability must not execute a repository-configured fsmonitor.
    return git_ops._git(root, '--no-optional-locks', '-c', 'core.fsmonitor=false', *args)


def _is_repo(root: str) -> bool:
    result = _git(root, 'rev-parse', '--is-inside-work-tree')
    return result.returncode == 0 and result.stdout.strip() == 'true'


def _path(root: str, value: str) -> Path:
    base = Path(root).resolve()
    relative = Path(value)
    if not value or relative.is_absolute() or '..' in relative.parts or not (base / relative).resolve().is_relative_to(base):
        raise HTTPException(403, 'Review files must stay inside the conversation checkout')
    return base / relative


def _head(root: str) -> str | None:
    result = _git(root, 'rev-parse', '--verify', 'HEAD')
    return result.stdout.strip() if result.returncode == 0 else None


def _files(root: str) -> list[dict]:
    repository = Path(git_ops._require_ok(_git(root, 'rev-parse', '--show-toplevel')).rstrip('\n')).resolve()
    relative = Path(root).resolve().relative_to(repository).as_posix()
    prefix = relative + '/' if relative != '.' else ''
    raw = git_ops._require_ok(_git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.'))
    records = iter(raw.split('\0'))
    files = {}
    for entry in records:
        if not entry:
            continue
        index, worktree, path = entry[0], entry[1], entry[3:]
        previous = next(records, None) if 'R' in entry[:2] or 'C' in entry[:2] else None
        boundary_status = None
        if prefix:
            if path.startswith(prefix):
                path = path[len(prefix):]
                if previous and previous.startswith(prefix):previous = previous[len(prefix):]
                elif previous:previous, boundary_status = None, 'added'
            elif previous and previous.startswith(prefix):
                path, previous, boundary_status = previous[len(prefix):], None, 'deleted'
            else:continue
        status = 'conflict' if 'U' in entry[:2] or entry[:2] in {'AA', 'DD'} else 'renamed' if previous else 'deleted' if 'D' in entry[:2] else 'added' if index in {'?', 'A'} else 'modified'
        files[path] = {'path': path, 'status': boundary_status or status, 'index': index, 'worktree': worktree,
                       'staged': index not in {' ', '?'}, 'untracked': index == '?',
                       **({'previous_path': previous} if previous else {})}
    return sorted(files.values(), key=lambda item: item['path'])


def _stats(root: str, head: str) -> dict[str, tuple[int | None, int | None]]:
    result = _git(root, 'diff', '--numstat', '--no-ext-diff', '--no-textconv', '--relative', '-z', head, '--', '.')
    stats = {}
    entries = iter(git_ops._require_ok(result).split('\0'))
    for entry in entries:
        if not entry:
            continue
        added, deleted, path = entry.split('\t', 2)
        if not path:
            next(entries, None)  # A -z rename carries old and new names separately.
            path = next(entries, '')
        stats[path] = (None, None) if added == '-' else (int(added), int(deleted))
    return stats


def _new_content(root: str, value: str) -> tuple[str | None, bool, bool]:
    path = _path(root, value)
    if not path.is_file() and not path.is_symlink():
        return None, False, False
    # Anchor every POSIX directory lookup so a concurrent symlink swap cannot
    # turn an authorized untracked file preview into a read outside the checkout.
    if os.name == 'posix':
        directory = os.open(Path(root).resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in Path(value).parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = following
            name = Path(value).name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                return os.readlink(name, dir_fd=directory) + '\n', False, False
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
    else:
        if path.is_symlink():
            return os.readlink(path) + '\n', False, False
        prior = path.stat(follow_symlinks=False)
        handle = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        info = os.fstat(handle)
        if (prior.st_dev, prior.st_ino) != (info.st_dev, info.st_ino) or not path.resolve().is_relative_to(Path(root).resolve()):
            os.close(handle)
            raise HTTPException(409, 'File changed while reading. Refresh the review.')
    with os.fdopen(handle, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise HTTPException(400, 'Only regular files can be previewed')
        content = source.read(MAX_PREVIEW_BYTES + 1)
    if len(content) > MAX_PREVIEW_BYTES:
        return None, False, True
    if b'\0' in content:
        return None, True, False
    try:
        return content.decode('utf-8'), False, False
    except UnicodeDecodeError:
        return None, True, False


def snapshot(root: str) -> dict:
    if not _is_repo(root):
        return {'repo': False, 'branch': None, 'head': None, 'files': [], 'additions': 0, 'deletions': 0,
                'counts_complete': True, 'truncated': False}
    head = _head(root)
    branch_result = _git(root, 'symbolic-ref', '--short', '-q', 'HEAD')
    branch = branch_result.stdout.strip() or (head[:8] if head else None)
    rows = _files(root)
    truncated = len(rows) > MAX_FILES
    rows = rows[:MAX_FILES]
    stats = _stats(root, head) if head else {}
    remaining = 4 * MAX_PREVIEW_BYTES
    for item in rows:
        item.update(binary=False, truncated=False, reviewable=True)
        try:
            _path(root, item['path'])
        except HTTPException:
            item.update(additions=None, deletions=None, reviewable=False)
            continue
        if item['untracked'] or head is None:
            size = (Path(root) / item['path']).stat().st_size if (Path(root) / item['path']).is_file() else 0
            if size > remaining:
                item.update(additions=None, deletions=None)
                continue
            remaining -= min(size, MAX_PREVIEW_BYTES)
            content, binary, large = _new_content(root, item['path'])
            item.update(additions=len(content.splitlines()) if content is not None else None,
                        deletions=0 if content is not None else None, binary=binary, truncated=large)
        else:
            added, deleted = stats.get(item['path'], (0, 0))
            if item.get('previous_path'):
                old_added, old_deleted = stats.get(item['previous_path'], (0, 0))
                added = None if added is None or old_added is None else added + old_added
                deleted = None if deleted is None or old_deleted is None else deleted + old_deleted
            item.update(additions=added, deletions=deleted, binary=added is None)
    return {'repo': True, 'branch': branch, 'head': head, 'files': rows,
            'additions': sum(item['additions'] or 0 for item in rows), 'deletions': sum(item['deletions'] or 0 for item in rows),
            'counts_complete': not truncated and all(item['additions'] is not None for item in rows), 'truncated': truncated}


def diff(root: str, value: str) -> dict:
    _path(root, value)
    if not _is_repo(root):
        raise HTTPException(409, 'This conversation folder is not a Git repository')
    item = next((item for item in _files(root) if item['path'] == value), None)
    if item is None:
        raise HTTPException(409, 'This file no longer has uncommitted changes. Refresh the review.')
    head = _head(root)
    if item['untracked'] or head is None:
        content, binary, truncated = _new_content(root, value)
        patch = ''.join(difflib.unified_diff([], content.splitlines(keepends=True), fromfile='/dev/null', tofile='b/' + value)) if content is not None else ''
        added, deleted = (len(content.splitlines()), 0) if content is not None else (None, None)
    else:
        paths = [item['previous_path'], value] if item.get('previous_path') else [value]
        try:
            patch = git_ops._require_ok(_git(root, '-c', 'core.quotePath=false', 'diff', '--no-color', '--no-ext-diff', '--no-textconv', head, '--', *[':(literal)' + path for path in paths]))
            binary = 'Binary files ' in patch or 'GIT binary patch' in patch
        except UnicodeDecodeError:
            patch, binary = '', True
        encoded = patch.encode('utf-8')
        truncated = len(encoded) > MAX_PREVIEW_BYTES
        if truncated:
            patch = encoded[:MAX_PREVIEW_BYTES].decode('utf-8', 'ignore').rsplit('\n', 1)[0] + '\n'
        added = deleted = 0
        in_hunk = False
        for line in patch.splitlines():
            if line.startswith('@@'):in_hunk = True
            elif line.startswith('diff --git '):in_hunk = False
            elif in_hunk and line.startswith('+'):added += 1
            elif in_hunk and line.startswith('-'):deleted += 1
        if binary or truncated:added = deleted = None
    encoded = patch.encode('utf-8')
    if len(encoded) > MAX_PREVIEW_BYTES:
        truncated = True
        patch = encoded[:MAX_PREVIEW_BYTES].decode('utf-8', 'ignore').rsplit('\n', 1)[0] + '\n'
    return {'path': value, 'diff': patch, 'binary': binary, 'truncated': truncated, 'additions': added, 'deletions': deleted}
