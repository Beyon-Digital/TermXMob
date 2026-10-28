from pathlib import Path

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
