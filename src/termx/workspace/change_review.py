"""Read-only change review for an already-authorized conversation checkout."""
from __future__ import annotations

import difflib
import os
import shutil
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from fastapi import HTTPException
from termx import git_ops

MAX_FILES = 2000
MAX_PREVIEW_BYTES = 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 8 * MAX_PREVIEW_BYTES
MAX_INDEX_BYTES = 64 * MAX_PREVIEW_BYTES
GIT_TIMEOUT = 30


def _environment(*, configuration=False) -> dict[str, str]:
    # Inherited Git overrides must not reintroduce a config file or retarget the
    # temporary view. Do not copy credentials/configuration into that view.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1')
    if not configuration:
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM='1')
    return env


def _git(root: str, *args: str, env=None, limit=MAX_GIT_OUTPUT_BYTES, truncate=False):
    """Drain both pipes with fixed budgets, including on Windows pipe handles."""
    executable = shutil.which('git')
    if executable is None:
        raise HTTPException(400, 'Git is not installed on this host')
    command = [executable, '-C', root, '--no-optional-locks', '-c', 'core.fsmonitor=false', *args]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env or _environment()) as process:
        output, errors = bytearray(), bytearray()
        exceeded, error_exceeded = Event(), Event()

        def drain(source, target, budget, overflow):
            while chunk := source.read(min(65536, budget + 1 - len(target))):
                target.extend(chunk)
                if len(target) > budget:
                    overflow.set()
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    break

        readers = [Thread(target=drain, args=(process.stdout, output, limit, exceeded), daemon=True),
                   Thread(target=drain, args=(process.stderr, errors, 65536, error_exceeded), daemon=True)]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise HTTPException(504, 'Git command timed out') from exc
        finally:
            for reader in readers:
                reader.join()
        if error_exceeded.is_set() or (exceeded.is_set() and not truncate):
            raise HTTPException(413, 'Git review output exceeds the review limit')
        result = subprocess.CompletedProcess(command, 0 if exceeded.is_set() else process.returncode,
                                             bytes(output).decode('utf-8', 'ignore' if exceeded.is_set() else 'strict'),
                                             bytes(errors).decode('utf-8', 'replace'))
        result.truncated = exceeded.is_set()
        return result


def _copy_metadata(source: Path, destination: Path):
    with source.open('rb') as incoming, destination.open('wb') as outgoing:
        metadata = os.fstat(incoming.fileno())
        remaining = MAX_INDEX_BYTES
        while chunk := incoming.read(min(65536, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise HTTPException(413, 'Git review metadata exceeds the review limit')
            outgoing.write(chunk)
    # Git compares worktree timestamps with the index's own timestamp to detect
    # racy-clean entries. Giving the copy a fresh mtime can hide same-size edits.
    os.utime(destination, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


@contextmanager
def _repository(root: str):
    """Snapshot inert Git metadata without loading executable repository config.

    Disabling known filters by name is racy: another driver can be configured
    before status/diff reads the live config. A separate Git directory has no
    filter drivers or info/attributes, even when worktree attributes change.
    """
    repository = git_ops._require_ok(_git(root, 'rev-parse', '--show-toplevel')).rstrip('\n')
    index = Path(git_ops._require_ok(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index')).strip())
    objects = git_ops._require_ok(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')).strip()
    shared = git_ops._require_ok(_git(root, 'rev-parse', '--shared-index-path')).strip()
    head = _head(root)
    branch_result = _git(root, 'symbolic-ref', '--short', '-q', 'HEAD')
    branch = branch_result.stdout.strip() or (head[:8] if head else None)
    object_format = git_ops._require_ok(_git(root, 'rev-parse', '--show-object-format')).strip()
    if object_format not in {'sha1', 'sha256'}:
        raise HTTPException(409, 'Unsupported Git object format')
    config = ['[core]', 'repositoryformatversion = ' + ('1' if object_format == 'sha256' else '0'),
              'bare = false', 'fsmonitor = false', 'attributesFile = ' + os.devnull]
    # Read only selected inert values from the original effective config. Config
    # queries do not run filters; the commands operating on files use the view.
    config_env = _environment(configuration=True)
    for name in ('filemode', 'ignorecase', 'symlinks', 'precomposeunicode'):
        value = _git(root, 'config', '--bool', '--get', 'core.' + name, env=config_env)
        if value.returncode == 0 and value.stdout.strip() in {'true', 'false'}:
            config.append(name + ' = ' + value.stdout.strip())
    for name, permitted in (('autocrlf', {'true', 'false', 'input'}),
                            ('eol', {'lf', 'crlf', 'native'}),
                            ('safecrlf', {'true', 'false', 'warn'})):
        value = _git(root, 'config', '--get', 'core.' + name, env=config_env)
        if value.returncode == 0 and value.stdout.strip() in permitted:
            config.append(name + ' = ' + value.stdout.strip())
    excludes = _git(root, 'config', '--path', '--get', 'core.excludesFile', env=config_env)
    # This is an inert file path, passed as one argv value rather than loaded as
    # Git configuration. Preserve an explicit empty value as well as a path.
    exclude_options = ['-c', 'core.excludesFile=' + excludes.stdout.rstrip('\n')] if excludes.returncode == 0 else []
    if object_format == 'sha256':
        config.extend(['[extensions]', 'objectFormat = sha256'])
    with TemporaryDirectory(prefix='termx-review-') as temporary:
        directory = Path(temporary)
        (directory / 'objects').mkdir()
        (directory / 'refs').mkdir()
        (directory / 'config').write_text('\n'.join(config) + '\n', encoding='utf-8')
        (directory / 'HEAD').write_text(head + '\n' if head else 'ref: refs/heads/review-unborn\n', encoding='ascii')
        if index.is_file():
            _copy_metadata(index, directory / 'index')
        if shared:
            shared_path = Path(shared)
            if not shared_path.is_absolute():
                shared_path = Path(root) / shared_path
            _copy_metadata(shared_path, directory / shared_path.name)
        # Preserve repository-local ignore rules without importing attributes.
        exclude = Path(git_ops._require_ok(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'info/exclude')).strip())
        if exclude.is_file():
            (directory / 'info').mkdir()
            _copy_metadata(exclude, directory / 'info' / 'exclude')
        env = _environment()
        env.update(GIT_DIR=str(directory), GIT_WORK_TREE=repository, GIT_OBJECT_DIRECTORY=objects)

        def run(*args, **kwargs):
            return _git(root, *exclude_options, *args, env=env, **kwargs)

        yield run, head, branch


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


def _files(root: str, git) -> list[dict]:
    repository = Path(git_ops._require_ok(git('rev-parse', '--show-toplevel')).rstrip('\n')).resolve()
    relative = Path(root).resolve().relative_to(repository).as_posix()
    prefix = relative + '/' if relative != '.' else ''
    raw = git_ops._require_ok(git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=dirty', '--', '.'))
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


def _stats(git, head: str) -> dict[str, tuple[int | None, int | None]]:
    result = git('diff', '--numstat', '--no-ext-diff', '--no-textconv', '--ignore-submodules=dirty', '--relative', '-z', head, '--', '.')
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
    with _repository(root) as (git, head, branch):
        return _snapshot(root, git, head, branch)


def _snapshot(root, git, head, branch):
    rows = _files(root, git)
    truncated = len(rows) > MAX_FILES
    rows = rows[:MAX_FILES]
    stats = _stats(git, head) if head else {}
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
    with _repository(root) as (git, head, _):
        return _diff(root, value, git, head)


def _diff(root, value, git, head):
    item = next((item for item in _files(root, git) if item['path'] == value), None)
    if item is None:
        raise HTTPException(409, 'This file no longer has uncommitted changes. Refresh the review.')
    if item['untracked'] or head is None:
        content, binary, truncated = _new_content(root, value)
        patch = ''.join(difflib.unified_diff([], content.splitlines(keepends=True), fromfile='/dev/null', tofile='b/' + value)) if content is not None else ''
        added, deleted = (len(content.splitlines()), 0) if content is not None else (None, None)
    else:
        paths = [item['previous_path'], value] if item.get('previous_path') else [value]
        truncated = False
        try:
            result = git('-c', 'core.quotePath=false', 'diff', '--no-color', '--no-ext-diff', '--no-textconv',
                         '--ignore-submodules=dirty', head, '--', *[':(literal)' + path for path in paths],
                         limit=MAX_PREVIEW_BYTES, truncate=True)
            patch = git_ops._require_ok(result)
            truncated = result.truncated
            binary = 'Binary files ' in patch or 'GIT binary patch' in patch
        except UnicodeDecodeError:
            patch, binary = '', True
        encoded = patch.encode('utf-8')
        truncated |= len(encoded) > MAX_PREVIEW_BYTES
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
