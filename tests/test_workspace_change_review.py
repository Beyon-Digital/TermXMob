from pathlib import Path
import os
import shlex
import sys

import pytest

from test_durable_workspace import workspace
from test_workspace_quick_files import setup
from termx.workspace import change_review


def test_review_uses_enrolled_checkout_and_reads_real_staged_unstaged_and_new_text(workspace):
    service, owner, root, tree, row, client, _ = setup(workspace)
    checkout = Path(tree['path'])
    (checkout / 'base.txt').write_text('isolated edit\n')
    from termx import git_ops
    git_ops.stage(str(checkout), ['base.txt'], True)
    (checkout / 'base.txt').write_text('isolated final edit\n')
    (checkout / 'new space ü.txt').write_text('new file\n')
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    response = client.get(url, params={'worktree_id': tree['id']})
    assert response.status_code == 200, response.text
    data = response.json()
    paths = [item['path'] for item in data['files']]
    assert 'base.txt' in paths and 'new space ü.txt' in paths and 'main-only.txt' not in paths
    selected = next(item for item in data['files'] if item['path'] == 'base.txt')
    assert selected['staged'] and selected['additions'] == 1 and selected['deletions'] == 1
    diff = client.get(url + '/diff', params={'worktree_id': tree['id'], 'path': 'base.txt'})
    assert diff.status_code == 200 and '+isolated final edit' in diff.json()['diff']
    assert 'isolated edit\n' not in diff.json()['diff']
    new = client.get(url + '/diff', params={'worktree_id': tree['id'], 'path': 'new space ü.txt'})
    assert new.status_code == 200 and '+new file' in new.json()['diff'] and new.json()['additions'] == 1
    assert (root / 'base.txt').read_text().startswith('Needle')
    assert client.get(url).status_code == 409


def test_review_rejects_path_escape_outside_links_cloud_and_revalidates_permission(workspace, monkeypatch):
    service, owner, root, tree, row, client, _ = setup(workspace)
    checkout = Path(tree['path'])
    secret = root.parent / 'private-review.txt'; secret.write_text('private content')
    (checkout / 'outside.txt').symlink_to(secret)
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    args = {'worktree_id': tree['id']}
    for path in ('../private-review.txt', str(secret), 'outside.txt'):
        response = client.get(url + '/diff', params={**args, 'path': path})
        assert response.status_code == 403 and 'private content' not in response.text
    original = change_review.snapshot
    def revoke(root):
        result = original(root)
        service.state.identity.set_scopes(owner.id, ['machine-view'])
        return result
    monkeypatch.setattr(change_review, 'snapshot', revoke)
    response = client.get(url, params=args)
    assert response.status_code == 403 and 'files' not in response.json()


def test_review_rejects_checkout_switch_during_read_and_removed_enrollment(workspace, monkeypatch):
    service, owner, root, tree, row, client, _ = setup(workspace)
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    original = change_review.snapshot
    def switch(root):
        result = original(root)
        current = service.session(owner, row['id'])
        service.update_session(owner, row['id'], revision=current['revision'], changes={'worktree_id': None})
        return result
    monkeypatch.setattr(change_review, 'snapshot', switch)
    response = client.get(url, params={'worktree_id': tree['id']})
    assert response.status_code == 409 and 'files' not in response.json()
    current = service.session(owner, row['id'])
    service.update_session(owner, row['id'], revision=current['revision'], changes={'worktree_id': tree['id']})
    with service.state.delivery.db() as db:
        db.execute('DELETE FROM worktrees WHERE id=?', (tree['id'],))
    assert client.get(url, params={'worktree_id': tree['id']}).status_code == 403


def test_binary_and_large_new_files_have_explicit_preview_limits(workspace):
    _, _, root, tree, row, client, _ = setup(workspace)
    checkout = Path(tree['path'])
    (checkout / 'binary.bin').write_bytes(b'abc\0def')
    (checkout / 'large.txt').write_text('x' * (change_review.MAX_PREVIEW_BYTES + 1))
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    args = {'worktree_id': tree['id']}
    binary = client.get(url + '/diff', params={**args, 'path': 'binary.bin'}).json()
    assert binary['binary'] and binary['diff'] == ''
    (checkout / 'base.txt').write_bytes(b'unsupported encoding: \xe9\n')
    encoded = client.get(url + '/diff', params={**args, 'path': 'base.txt'}).json()
    assert encoded['binary'] and encoded['diff'] == ''
    large = client.get(url + '/diff', params={**args, 'path': 'large.txt'}).json()
    assert large['truncated'] and large['diff'] == ''
    data = client.get(url, params=args).json()
    assert not data['counts_complete']


def test_cloud_review_never_falls_back_to_the_host_checkout(workspace):
    service, owner, root, tree, row, client, _ = setup(workspace)
    current = service.store.get('conversation', row['id'])
    service.store.update('conversation', row['id'], {**current, 'worktree_id': None, 'worktree_digest': None, 'cwd': str(root), 'runner_id': 'cloud'})
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    response = client.get(url)
    assert response.status_code == 409 and 'Cloud files' in response.json()['detail']
    assert client.get(url + '/diff', params={'path': 'main-only.txt'}).status_code == 409


def test_review_from_a_project_subfolder_never_reports_other_folders(workspace):
    service, owner, root, tree, row, client, _ = setup(workspace)
    folder = root / 'nested'; folder.mkdir(); (folder / 'inside.txt').write_text('inside only\n')
    selected = service.create_session(owner, project_id='project-a', cwd=str(folder))
    url = '/api/workspace/sessions/' + selected['id'] + '/changes'
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert [item['path'] for item in response.json()['files']] == ['inside.txt']
    preview = client.get(url + '/diff', params={'path': 'inside.txt'})
    assert preview.status_code == 200 and '+inside only' in preview.json()['diff']


def test_pure_rename_counts_only_real_line_changes(workspace):
    _, _, root, tree, row, client, _ = setup(workspace)
    from termx import git_ops
    checkout = Path(tree['path'])
    git_ops._require_ok(git_ops._git(str(checkout), 'mv', 'base.txt', 'renamed.txt'))
    url = '/api/workspace/sessions/' + row['id'] + '/changes'
    data = client.get(url, params={'worktree_id': tree['id']}).json()
    renamed = next(item for item in data['files'] if item['path'] == 'renamed.txt')
    assert renamed['status'] == 'renamed' and renamed['previous_path'] == 'base.txt'
    assert renamed['additions'] == 0 and renamed['deletions'] == 0
    preview = client.get(url + '/diff', params={'worktree_id': tree['id'], 'path': 'renamed.txt'}).json()
    assert 'rename from base.txt' in preview['diff'] and preview['additions'] == 0


def test_read_only_review_never_executes_repository_fsmonitor(workspace):
    _, _, root, tree, _, _, _ = setup(workspace)
    import shlex
    from termx import git_ops
    checkout = Path(tree['path'])
    marker = root.parent / 'fsmonitor-executed'
    hook = root.parent / 'fsmonitor-hook.sh'
    hook.write_text('#!/bin/sh\n: > ' + shlex.quote(str(marker)) + "\nprintf 'review-token\\0/\\0'\n")
    hook.chmod(0o700)
    git_ops._require_ok(git_ops._git(str(checkout), 'config', 'core.fsmonitor', str(hook)))
    (checkout / 'base.txt').write_text('review this real change\n')
    # Verify the configured hook is active before checking the read-only port.
    git_ops._require_ok(git_ops._git(str(checkout), 'status', '--porcelain'))
    assert marker.exists()
    marker.unlink()
    snapshot = change_review.snapshot(str(checkout))
    preview = change_review.diff(str(checkout), 'base.txt')
    assert any(item['path'] == 'base.txt' for item in snapshot['files'])
    assert '+review this real change' in preview['diff']
    assert not marker.exists()


@pytest.mark.parametrize('driver', ['clean', 'process'])
def test_review_never_executes_content_filters_even_when_configuration_changes(workspace, monkeypatch, driver):
    _, _, root, tree, _, _, _ = setup(workspace)
    from termx import git_ops
    checkout = Path(tree['path'])
    marker = root.parent / 'content-filter-executed'
    hook = root.parent / 'content-filter.py'
    hook.write_text('from pathlib import Path\nimport sys\n' +
                    f'Path({str(marker)!r}).write_text("invoked")\n' +
                    ('sys.exit(1)\n' if driver == 'process' else 'sys.stdout.buffer.write(sys.stdin.buffer.read())\n'))
    command = shlex.quote(Path(sys.executable).as_posix()) + ' ' + shlex.quote(hook.as_posix())
    (checkout / '.gitattributes').write_text('base.txt filter=review-marker\n')
    git_ops._require_ok(git_ops._git(str(checkout), 'config', 'filter.review-marker.' + driver, command))
    (checkout / 'base.txt').write_text('raw changed content\n')
    # Prove this Git setup really runs the selected driver without the boundary.
    git_ops._git(str(checkout), 'diff', 'HEAD', '--', 'base.txt')
    assert marker.exists()
    marker.unlink()
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'filter.inherited.' + driver)
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', command)
    monkeypatch.setenv('GIT_CONFIG_PARAMETERS', "'filter.parameters.clean=ignored'")
    original = change_review._git
    changed = False

    def change_configuration_after_view_created(*args, **kwargs):
        nonlocal changed
        if kwargs.get('env', {}).get('GIT_DIR') and not changed:
            assert not any(key in kwargs['env'] for key in (
                'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', 'GIT_CONFIG_PARAMETERS'))
            changed = True
            # A newly named driver defeats enumerating live config once and
            # overriding only those old names. The view has no driver config.
            git_ops._require_ok(git_ops._git(str(checkout), 'config', 'filter.raced.' + driver, command))
            (checkout / '.gitattributes').write_text('base.txt filter=raced\n')
        return original(*args, **kwargs)

    monkeypatch.setattr(change_review, '_git', change_configuration_after_view_created)
    snapshot = change_review.snapshot(str(checkout))
    preview = change_review.diff(str(checkout), 'base.txt')
    assert changed and not marker.exists()
    assert any(item['path'] == 'base.txt' for item in snapshot['files'])
    assert '+raw changed content' in preview['diff']


@pytest.mark.parametrize('linked', [False, True])
def test_review_supports_regular_and_linked_worktrees_with_a_split_index(workspace, linked):
    _, _, root, tree, _, _, _ = setup(workspace)
    from termx import git_ops
    checkout = Path(tree['path']) if linked else root
    git_ops._require_ok(git_ops._git(str(checkout), 'update-index', '--split-index'))
    (checkout / 'base.txt').write_text('split index edit\n')
    snapshot = change_review.snapshot(str(checkout))
    expected_branch = git_ops._require_ok(git_ops._git(str(checkout), 'symbolic-ref', '--short', 'HEAD')).strip()
    assert snapshot['branch'] == expected_branch
    assert any(item['path'] == 'base.txt' and item['additions'] == 1 for item in snapshot['files'])
    assert '+split index edit' in change_review.diff(str(checkout), 'base.txt')['diff']


def test_review_keeps_clean_crlf_checkout_clean_with_autocrlf_enabled(workspace):
    _, _, _, tree, _, _, _ = setup(workspace)
    from termx import git_ops
    checkout = Path(tree['path'])
    git_ops._require_ok(git_ops._git(str(checkout), 'config', 'core.autocrlf', 'true'))
    (checkout / 'base.txt').unlink()
    git_ops._require_ok(git_ops._git(str(checkout), 'checkout-index', '--force', '--index', '--', 'base.txt'))
    source = checkout / 'base.txt'
    assert b'\r\n' in source.read_bytes()
    metadata = source.stat()
    # Make Git check the actual CRLF contents rather than trusting cached stat
    # data. The effective autocrlf setting must keep this logically clean.
    os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns - 2_000_000_000))
    assert git_ops._require_ok(git_ops._git(str(checkout), '--no-optional-locks', 'status', '--porcelain', '--', 'base.txt')) == ''
    assert all(item['path'] != 'base.txt' for item in change_review.snapshot(str(checkout))['files'])


def test_review_preserves_a_custom_excludes_file_and_explicit_empty_value(tmp_path):
    from termx import git_ops
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    git_ops._require_ok(git_ops._git(str(checkout), 'init'))
    excludes = tmp_path / 'custom ignore file'
    excludes.write_text('ignored-secret.txt\n')
    (checkout / 'ignored-secret.txt').write_text('private fixture\n')
    (checkout / 'public.txt').write_text('public fixture\n')
    git_ops._require_ok(git_ops._git(str(checkout), 'config', 'core.excludesFile', str(excludes)))
    assert [item['path'] for item in change_review.snapshot(str(checkout))['files']] == ['public.txt']
    git_ops._require_ok(git_ops._git(str(checkout), 'config', 'core.excludesFile', ''))
    assert [item['path'] for item in change_review.snapshot(str(checkout))['files']] == ['ignored-secret.txt', 'public.txt']


def test_oversized_tracked_diff_is_capped_while_draining_the_git_pipe(workspace, monkeypatch):
    _, _, root, tree, _, _, _ = setup(workspace)
    from termx import git_ops
    checkout = Path(tree['path'])
    cap = 16 * 1024
    (checkout / 'base.txt').write_text('old line\n' * cap)
    git_ops._require_ok(git_ops._git(str(checkout), 'add', '--', 'base.txt'))
    git_ops._require_ok(git_ops._git(str(checkout), 'commit', '-m', 'Oversized review fixture'))
    (checkout / 'base.txt').write_text('new line\n' * cap)
    original = change_review.subprocess.Popen
    captured = []

    class RecordedPipe:
        def __init__(self, pipe):
            self.pipe = pipe
            self.bytes_read = 0

        def read(self, size=-1):
            assert 0 < size <= 65536, 'Review transport must never read a pipe without a bound'
            value = self.pipe.read(size)
            self.bytes_read += len(value)
            return value

        def __getattr__(self, name):
            return getattr(self.pipe, name)

    def observe_patch_pipe(command, *args, **kwargs):
        process = original(command, *args, **kwargs)
        if 'diff' in command and '--numstat' not in command:
            process.stdout = RecordedPipe(process.stdout)
            captured.append(process.stdout)
        return process

    monkeypatch.setattr(change_review, 'MAX_PREVIEW_BYTES', cap)
    monkeypatch.setattr(change_review.subprocess, 'Popen', observe_patch_pipe)
    preview = change_review.diff(str(checkout), 'base.txt')
    assert preview['truncated'] and not preview['binary']
    assert preview['additions'] is None and preview['deletions'] is None
    assert len(preview['diff'].encode()) <= cap
    assert len(captured) == 1 and captured[0].bytes_read == cap + 1
