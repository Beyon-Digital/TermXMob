from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from time import time

import pytest
from fastapi import HTTPException

from test_durable_workspace import workspace
from test_workspace_quick_files import setup
from termx.development.delivery import DeliveryService
from termx.workspace.service import WorkspaceService
from termx.workspace.session_lifecycle import ensure_checkout
from termx.workspace.store import Conflict


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout


def coding(workspace, **kwargs):
    service, owner, root, selected, existing, client, _ = setup(workspace)
    row = service.create_session(owner, project_id='project-a', mode='agent', provider_id='fixture', model='fixture', **kwargs)
    return service, owner, root, selected, row, client


@pytest.fixture
def coding_workspace(workspace):
    return coding(workspace)


@pytest.mark.anyio
async def test_automatic_checkout_is_lazy_preserves_dirty_source_and_reuses_after_restart(coding_workspace):
    service, owner, root, _, row, _ = coding_workspace
    count = len(service.state.delivery.worktrees('project-a'))
    assert row['checkout_state'] == 'pending' and not row['worktree_id'] and row['title_origin'] == 'pending'
    (root / 'base.txt').write_text('staged original\n')
    git(root, 'add', 'base.txt')
    (root / 'base.txt').write_text('unstaged original\n')
    (root / '.env').write_text('LOCAL_SECRET=do-not-copy\n')
    before = git(root, 'status', '--porcelain=v1', '-z')
    index = (root / '.git/index').read_bytes()
    original_head = git(root, 'rev-parse', 'HEAD').strip()
    seen = []
    original = service.state.agent.create_task
    async def capture(**kwargs):
        seen.append(kwargs)
        return await original(**kwargs)
    service.state.agent.create_task = capture
    a, b = await asyncio.gather(*[service.send(owner, row['id'], prompt='Fix the checkout lifecycle', request_id='first-coding-turn') for _ in range(2)])
    assert a['id'] == b['id'] and service.state.agent.calls == 1
    current = service.session(owner, row['id'])
    checkout = Path(current['cwd'])
    assert current['worktree_id'] and checkout != root and current['checkout_state'] == 'isolated'
    assert current['title'] == 'Fix the checkout lifecycle' and current['title_origin'] == 'auto'
    assert current['source_cwd'] == str(root) and current['source_dirty'] is True
    assert current['worktree_base_ref'] == original_head and 'not copied' in current['checkout_notice']
    assert (checkout / 'base.txt').read_text().startswith('Needle')
    assert not (checkout / '.env').exists() and not (checkout / 'main-only.txt').exists()
    assert git(root, 'status', '--porcelain=v1', '-z') == before and (root / '.git/index').read_bytes() == index
    assert (root / 'base.txt').read_text() == 'unstaged original\n'
    assert seen[0]['cwd'] == str(checkout) and seen[0]['isolated'] is True
    assert 'execution_mode' not in seen[0] and service.agents.task_worktree(a['id']) is None
    assert len(service.state.delivery.worktrees('project-a')) == count + 1
    service.agents.update_task(a['id'], status='completed')
    restarted = WorkspaceService(service.state, service.store.path, project_check=lambda *args: None)
    try:
        task = await restarted.send(owner, row['id'], prompt='Continue this work', request_id='continued-after-restart')
        assert task['cwd'] == str(checkout)
        assert restarted.session(owner, row['id'])['title'] == 'Fix the checkout lifecycle'
        assert len(service.state.delivery.worktrees('project-a')) == count + 1
    finally:
        restarted.store.close()


@pytest.mark.anyio
async def test_git_failure_never_dispatches_in_source_and_retry_reconciles_same_intent(coding_workspace, monkeypatch):
    service, owner, root, _, row, _ = coding_workspace
    delivery = service.state.delivery
    original = delivery._add_worktree
    count = len(delivery.worktrees('project-a'))
    def fail(*args):
        raise HTTPException(409, 'fixture permission denied')
    monkeypatch.setattr(delivery, '_add_worktree', fail)
    with pytest.raises(ValueError, match='could not be prepared'):
        await service.send(owner, row['id'], prompt='Implement safely', request_id='failed-checkout')
    assert service.state.agent.calls == 0 and len(delivery.worktrees('project-a')) == count
    assert service.session(owner, row['id'])['cwd'] == str(root)
    intent = service.store.get('conversation', row['id'])['worktree_intent']
    monkeypatch.setattr(delivery, '_add_worktree', original)
    task = await service.send(owner, row['id'], prompt='Implement safely', request_id='failed-checkout')
    current = service.session(owner, row['id'])
    assert task['cwd'] == current['cwd'] != str(root)
    assert service.store.get('conversation', row['id'])['worktree_intent'] == intent


@pytest.mark.anyio
async def test_permissions_are_checked_before_checkout_effect_and_live_before_bind(coding_workspace, monkeypatch):
    service, owner, root, _, row, _ = coding_workspace
    delivery = service.state.delivery
    count = len(delivery.worktrees('project-a'))
    service.state.identity.set_scopes(owner.id, sorted(set(owner.scopes) - {'git-write'}))
    with pytest.raises(PermissionError):
        await service.send(owner, row['id'], prompt='Implement', request_id='missing-git-write')
    assert len(delivery.worktrees('project-a')) == count and service.state.agent.calls == 0
    service.state.identity.set_scopes(owner.id, sorted(owner.scopes))
    original = delivery.session_worktree
    def revoke(*args):
        tree = original(*args)
        service.state.identity.set_scopes(owner.id, sorted(set(owner.scopes) - {'git-write'}))
        return tree
    monkeypatch.setattr(delivery, 'session_worktree', revoke)
    with pytest.raises(PermissionError):
        await service.send(owner, row['id'], prompt='Implement', request_id='revoke-during-git')
    assert service.state.agent.calls == 0 and service.store.get('conversation', row['id'])['cwd'] == str(root)
    # The prepared checkout is retained. Restoring authority reuses it.
    service.state.identity.set_scopes(owner.id, sorted(owner.scopes))
    monkeypatch.setattr(delivery, 'session_worktree', original)
    await service.send(owner, row['id'], prompt='Implement', request_id='revoke-during-git')
    assert len(delivery.worktrees('project-a')) == count + 1


@pytest.mark.anyio
async def test_different_concurrent_first_sends_create_only_one_checkout_and_task(coding_workspace):
    service, owner, _, _, row, _ = coding_workspace
    count = len(service.state.delivery.worktrees('project-a'))
    results = await asyncio.gather(service.send(owner, row['id'], prompt='First action', request_id='concurrent-first'),
                                   service.send(owner, row['id'], prompt='Second action', request_id='concurrent-second'), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, Conflict) for item in results) == 1
    assert service.state.agent.calls == 1 and len(service.state.delivery.worktrees('project-a')) == count + 1


@pytest.mark.anyio
async def test_read_only_scratch_non_git_browser_and_remote_are_truthful_without_worktrees(coding_workspace):
    service, owner, root, _, row, _ = coding_workspace
    count = len(service.state.delivery.worktrees('project-a'))
    for mode in ('ask',):
        readonly = service.create_session(owner, project_id='project-a', mode=mode, provider_id='fixture', model='fixture')
        task = await service.send(owner, readonly['id'], prompt='Inspect this folder', request_id='readonly-' + mode)
        assert task['cwd'] == str(root) and service.session(owner, readonly['id'])['checkout_state'] == 'read-only'
    scratch = service.create_session(owner, mode='agent', provider_id='fixture', model='fixture')
    task = await service.send(owner, scratch['id'], prompt='Create a scratch note', request_id='scratch-coding')
    assert task['cwd'] == scratch['cwd'] and service.session(owner, scratch['id'])['checkout_state'] == 'scratch'
    plain = root.parent / 'plain'; plain.mkdir()
    original_project = service.state.projects.project
    service.state.projects.project = lambda identifier: {'id': 'plain', 'name': 'Plain', 'path': str(plain)} if identifier == 'plain' else original_project(identifier)
    service.state.projects.projects = lambda: [original_project('project-a'), service.state.projects.project('plain')]
    nongit = service.create_session(owner, project_id='plain', mode='agent', provider_id='fixture', model='fixture')
    task = await service.send(owner, nongit['id'], prompt='Work in plain folder', request_id='plain-coding')
    assert task['cwd'] == str(plain) and service.session(owner, nongit['id'])['checkout_state'] == 'not-git'
    service.state.engines.engines = lambda: ['claude', 'fixture-acp']
    browser = service.create_session(owner, project_id='project-a', engine='claude', workflow='browser', mode='agent')
    assert ensure_checkout(service, owner, browser)['cwd'] == str(root) and browser['checkout_state'] == 'browser'
    service.state.runners = SimpleNamespace(row=lambda *args: {'status': 'ready', 'expires': time() + 1000, 'project': 'project-a', 'configuration': {'kind': 'fixture', 'network': 'none'}})
    remote = service.create_session(owner, project_id='project-a', mode='agent', runner_id='remote', runner_credential_ref='fixture', provider_id='fixture', model='fixture')
    assert ensure_checkout(service, owner, remote)['cwd'] == str(root) and remote['checkout_state'] == 'remote'
    assert len(service.state.delivery.worktrees('project-a')) == count


@pytest.mark.anyio
async def test_explicit_enrolled_worktree_is_retained_and_active_target_cannot_change(coding_workspace):
    service, owner, root, tree, _, _ = coding_workspace
    row = service.create_session(owner, project_id='project-a', mode='agent', worktree_id=tree['id'], provider_id='fixture', model='fixture')
    count = len(service.state.delivery.worktrees('project-a'))
    task = await service.send(owner, row['id'], prompt='Use chosen checkout', request_id='selected-checkout')
    assert task['cwd'] == tree['path'] and service.session(owner, row['id'])['checkout_policy'] == 'selected'
    current = service.session(owner, row['id'])
    with pytest.raises(Conflict, match='active turn'):
        service.update_session(owner, row['id'], revision=current['revision'], changes={'worktree_id': None})
    assert len(service.state.delivery.worktrees('project-a')) == count


@pytest.mark.anyio
async def test_project_subfolder_maps_to_same_subfolder_and_cannot_authorize_siblings(coding_workspace):
    service, owner, root, _, _, _ = coding_workspace
    nested = root / 'nested'; nested.mkdir(); (nested / 'inside.txt').write_text('inside\n')
    git(root, 'add', 'nested'); git(root, 'commit', '-m', 'Nested fixture')
    row = service.create_session(owner, project_id='project-a', cwd=str(nested), mode='agent', provider_id='fixture', model='fixture')
    task = await service.send(owner, row['id'], prompt='Edit nested folder', request_id='nested-coding')
    current = service.session(owner, row['id'])
    target = service.worktree_target('project-a', current['worktree_id'])
    assert task['cwd'] == str(Path(target['path']) / 'nested')
    assert any(item['id'] == row['id'] for item in service.sessions(owner))
    with pytest.raises(PermissionError):
        service.require(owner, 'files-read', 'project-a', str(Path(target['path']) / 'base.txt'), resource_kind='conversation', resource_id=row['id'])


@pytest.mark.anyio
async def test_native_session_with_prior_turn_cannot_silently_move_to_new_checkout(coding_workspace):
    service, owner, root, _, _, _ = coding_workspace
    row = service.create_session(owner, project_id='project-a', mode='ask', engine='fixture-acp', model='fixture')
    task = await service.send(owner, row['id'], prompt='Inspect original', request_id='native-original')
    service.agents.update_task(task['id'], status='completed')
    row = service.session(owner, row['id'])
    row = service.update_session(owner, row['id'], revision=row['revision'], changes={'mode': 'agent'})
    with pytest.raises(Conflict, match='create a new coding conversation'):
        await service.send(owner, row['id'], prompt='Now edit', request_id='native-cannot-retarget')
    assert service.session(owner, row['id'])['cwd'] == str(root)


@pytest.mark.anyio
async def test_manual_names_and_rename_during_first_dispatch_always_win(coding_workspace):
    service, owner, root, _, row, _ = coding_workspace
    entered = asyncio.Event(); release = asyncio.Event()
    original = service.state.agent.create_task
    async def wait(**kwargs):
        entered.set(); await release.wait()
        return await original(**kwargs)
    service.state.agent.create_task = wait
    running = asyncio.create_task(service.send(owner, row['id'], prompt='Automatic title would use this', request_id='manual-title-race'))
    await entered.wait()
    current = service.session(owner, row['id'])
    service.update_session(owner, row['id'], revision=current['revision'], changes={'title': 'My chosen session'})
    release.set(); task = await running
    current = service.session(owner, row['id'])
    assert current['title'] == 'My chosen session' and current['title_origin'] == 'manual'
    assert service.store.get('conversation', row['id'])['title_origin'] == 'manual'
    service.agents.update_task(task['id'], status='completed')
    await service.send(owner, row['id'], prompt='Different prompt', request_id='manual-title-later')
    assert service.session(owner, row['id'])['title'] == 'My chosen session'
    explicit = service.create_session(owner, title='Existing meaningful name', cwd=str(root), provider_id='fixture', model='fixture')
    await service.send(owner, explicit['id'], prompt='Do not replace explicit name', request_id='explicit-name')
    assert service.session(owner, explicit['id'])['title_origin'] == 'manual'
    assert service.session(owner, explicit['id'])['title'] == 'Existing meaningful name'


def test_delivery_reconciles_lost_enrollment_without_recreating_or_removing_dirty_worktree(coding_workspace):
    service, owner, root, _, row, _ = coding_workspace
    delivery = service.state.delivery
    base = git(root, 'rev-parse', 'HEAD').strip()
    tree = delivery.session_worktree('project-a', str(root), row['id'], base)
    (Path(tree['path']) / 'retained.txt').write_text('user work\n')
    with delivery.db() as db:
        db.execute('DELETE FROM worktrees WHERE id=?', (tree['id'],))
    other = DeliveryService(delivery.directory)
    recovered = other.session_worktree('project-a', str(root), row['id'], base)
    assert recovered['id'] == tree['id'] and recovered['path'] == tree['path']
    assert (Path(recovered['path']) / 'retained.txt').read_text() == 'user work\n'
    assert len([item for item in delivery.worktrees('project-a') if item['id'] == tree['id']]) == 1


@pytest.mark.anyio
async def test_completed_history_and_task_tree_expose_bounded_authorized_artifacts_without_paths(coding_workspace):
    service, owner, root, _, row, client = coding_workspace
    task = await service.send(owner, row['id'], prompt='Render output', request_id='artifact-projection')
    artifact = service.agents.save_artifact(task['id'], 'generated', 'image/png', b'fixture-image')
    service.agents.update_task(task['id'], status='completed')
    session_url = '/api/workspace/sessions/' + row['id']
    response = client.get(session_url)
    assert response.status_code == 200, response.text
    saved = response.json()['turns'][0]['task']['artifacts'][0]
    assert saved == {key: artifact[key] for key in ('id', 'task_id', 'kind', 'mime', 'size')}
    assert 'path' not in saved and str(service.agents.artifact_dir) not in json.dumps(saved)
    tree = client.get('/api/workspace/tasks/' + task['id'])
    assert tree.status_code == 200 and tree.json()['artifacts'] == [saved]
    download = '/api/workspace/tasks/' + task['id'] + '/artifacts/' + artifact['id']
    assert client.get(download).content == b'fixture-image'
    for index in range(35):
        service.agents.save_artifact(task['id'], 'generated', 'image/png', str(index).encode())
    response = client.get(session_url).json()['turns'][0]['task']
    assert len(response['artifacts']) == 32 and response['artifacts_truncated'] is True
    foreign = service.agents.create_task(prompt='Foreign', cwd=str(root), provider_id='fixture', model='fixture', mode='ask', limits={})
    service.store.create('task', 'someone-else', {'conversation_id': row['id'], 'cwd': str(root)}, 'project-a', foreign['id'])
    foreign_artifact = service.agents.save_artifact(foreign['id'], 'generated', 'image/png', b'private-foreign-image')
    service.agents.add_conversation_turn(row['id'], prompt='Forged foreign link', task_id=foreign['id'])
    history = client.get(session_url).json()
    assert history['turns'][-1]['task']['artifacts'] == []
    assert client.get('/api/workspace/tasks/' + foreign['id'] + '/artifacts/' + foreign_artifact['id']).status_code in {403, 404}
    assert client.get('/api/workspace/tasks/' + task['id'] + '/artifacts/' + foreign_artifact['id']).status_code == 404


@pytest.mark.anyio
async def test_partial_checkout_binding_repairs_canonical_cwd_without_source_dispatch(coding_workspace, monkeypatch):
    service, owner, root, _, row, _ = coding_workspace
    original = service.agents.update_conversation
    failures = []
    def interrupted(identifier, **changes):
        if changes.get('cwd') and changes['cwd'] != str(root) and not failures:
            failures.append(True)
            raise RuntimeError('fixture interrupted canonical write')
        return original(identifier, **changes)
    monkeypatch.setattr(service.agents, 'update_conversation', interrupted)
    with pytest.raises(RuntimeError, match='interrupted canonical'):
        await service.send(owner, row['id'], prompt='Recover checkout binding', request_id='partial-binding')
    assert service.state.agent.calls == 0
    bound = service.store.get('conversation', row['id'])
    assert bound['worktree_id'] and bound['cwd'] != str(root)
    assert service.agents.get_conversation(row['id'])['cwd'] == str(root)
    assert service.session(owner, row['id'])['cwd'] == bound['cwd']
    assert any(item['id'] == row['id'] for item in service.sessions(owner))
    task = await service.send(owner, row['id'], prompt='Recover checkout binding', request_id='partial-binding')
    assert task['cwd'] == bound['cwd'] and service.agents.get_conversation(row['id'])['cwd'] == bound['cwd']


@pytest.mark.anyio
async def test_first_queued_coding_submission_binds_checkout_and_titles_at_dispatch(coding_workspace):
    from termx.workspace.prompt_queue import PromptQueue
    service, owner, root, _, row, client = coding_workspace
    origin = service.state.identity.resolve(client.headers['authorization'].split(' ', 1)[1])
    queue = PromptQueue(service)
    queued = queue.enqueue(origin.principal, row['id'], sid=origin.session_id, prompt='Queued first coding change', request_id='queued-first-coding')
    bound = service.session(owner, row['id'])
    assert bound['worktree_id'] and bound['cwd'] != str(root) and bound['title_origin'] == 'pending'
    assert queued['target']['cwd'] == bound['cwd']
    await queue.tick()
    saved = service.store.get('prompt_queue', queued['id'])
    assert saved['status'] == 'dispatched'
    assert service.agents.get_task(saved['task_id'])['cwd'] == bound['cwd']
    assert service.session(owner, row['id'])['title'] == 'Queued first coding change'
    assert queue.enqueue(origin.principal, row['id'], sid=origin.session_id, prompt='Queued first coding change', request_id='queued-first-coding')['id'] == queued['id']


def test_scheduled_grant_binds_actual_automatic_checkout_before_consent_snapshot(coding_workspace):
    from termx.workspace.automation import AutomationService
    service, owner, root, _, row, _ = coding_workspace
    automation = AutomationService(service, principal_lookup=service.principal_lookup)
    goal = automation.goal(owner, conversation_id=row['id'], success_criteria='A reviewed output', max_runs=1, limits=None)
    grant = automation.grant(owner, goal_id=goal['id'], expires_at=time() + 3600, max_runs=1, limits=None)
    bound = service.session(owner, row['id'])
    assert bound['worktree_id'] and bound['cwd'] != str(root)
    assert grant['execution_target']['cwd'] == bound['cwd'] and grant['execution_target']['worktree_id'] == bound['worktree_id']
    assert service.state.agent.calls == 0 and bound['title_origin'] == 'pending'


@pytest.mark.anyio
async def test_unborn_or_corrupt_git_stops_coding_without_checkout_fallback(coding_workspace):
    service, owner, root, _, _, _ = coding_workspace
    original_project = service.state.projects.project
    projects = []
    for name in ('unborn', 'corrupt'):
        folder = root.parent / name; folder.mkdir()
        if name == 'unborn':
            git(folder, 'init', '-b', 'main')
        else:
            (folder / '.git').write_text('invalid git metadata')
        projects.append({'id': name, 'name': name, 'path': str(folder)})
    service.state.projects.project = lambda identifier: next((item for item in projects if item['id'] == identifier), None) or original_project(identifier)
    service.state.projects.projects = lambda: [original_project('project-a'), *projects]
    count = len(service.state.delivery.worktrees('project-a'))
    for project in projects:
        row = service.create_session(owner, project_id=project['id'], mode='agent', provider_id='fixture', model='fixture')
        with pytest.raises(ValueError, match='Git|isolation'):
            await service.send(owner, row['id'], prompt='Edit this project', request_id='invalid-' + project['id'])
        assert service.session(owner, row['id'])['cwd'] == project['path']
    assert service.state.agent.calls == 0 and len(service.state.delivery.worktrees('project-a')) == count


@pytest.mark.anyio
async def test_registered_subproject_cannot_create_checkout_of_unauthorized_parent_repository(coding_workspace):
    service, owner, root, _, _, _ = coding_workspace
    nested = root / 'registered-child'; nested.mkdir(); (nested / 'child.txt').write_text('child only')
    git(root, 'add', 'registered-child'); git(root, 'commit', '-m', 'Registered child fixture')
    original_project = service.state.projects.project
    service.state.projects.project = lambda identifier: {'id':'child', 'name':'Child', 'path':str(nested)} if identifier == 'child' else original_project(identifier)
    service.state.projects.projects = lambda: [original_project('project-a'), service.state.projects.project('child')]
    row = service.create_session(owner, project_id='child', mode='agent', provider_id='fixture', model='fixture')
    refs = git(root, 'show-ref')
    count = len(service.state.delivery.worktrees('project-a'))
    with pytest.raises(ValueError, match='registered Git repository root'):
        await service.send(owner, row['id'], prompt='Edit this registered child', request_id='registered-child-boundary')
    assert service.state.agent.calls == 0 and git(root, 'show-ref') == refs
    assert service.state.delivery.worktrees('child') == [] and len(service.state.delivery.worktrees('project-a')) == count


@pytest.mark.anyio
async def test_two_services_cannot_dispatch_different_first_requests_concurrently(coding_workspace):
    service, owner, root, _, row, _ = coding_workspace
    other = WorkspaceService(service.state, service.store.path, project_check=lambda *args: None)
    entered = asyncio.Event(); release = asyncio.Event()
    original = service.state.agent.create_task
    async def delayed(**kwargs):
        entered.set(); await release.wait()
        return await original(**kwargs)
    service.state.agent.create_task = delayed
    first = asyncio.create_task(service.send(owner, row['id'], prompt='First cross-service request', request_id='service-one-request'))
    try:
        await entered.wait()
        with pytest.raises(Conflict, match='preparing|active turn'):
            await other.send(owner, row['id'], prompt='Second cross-service request', request_id='service-two-request')
        current = other.session(owner, row['id'])
        with pytest.raises(Conflict, match='active turn'):
            other.update_session(owner, row['id'], revision=current['revision'], changes={'model':'retargeted-model'})
        release.set(); task = await first
        assert service.state.agent.calls == 1 and len(service.agents.workspace_turns_page(row['id'])) == 1
        assert service.store.conversation_dispatch_pending(row['id']) is False
        assert (await other.send(owner, row['id'], prompt='First cross-service request', request_id='service-one-request'))['id'] == task['id']
    finally:
        release.set()
        await first
        other.store.close()


def test_cross_service_title_ownership_is_serialized_and_successful_manual_rename_wins(coding_workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from termx.workspace.session_lifecycle import apply_title
    service, owner, root, _, row, _ = coding_workspace
    other = WorkspaceService(service.state, service.store.path, project_check=lambda *args: None)
    entered = Event(); release = Event()
    original = service.agents.get_conversation
    def paused(identifier, **kwargs):
        value = original(identifier, **kwargs)
        if identifier == row['id'] and not release.is_set():
            entered.set(); assert release.wait(3)
        return value
    monkeypatch.setattr(service.agents, 'get_conversation', paused)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            automatic = executor.submit(apply_title, service, row['id'], 'Automatic title')
            assert entered.wait(2)
            manual = executor.submit(other.update_session, owner, row['id'], revision=row['revision'], changes={'title':'Manual winner'})
            assert not manual.done()
            release.set(); automatic.result(timeout=3)
            with pytest.raises(Conflict):
                manual.result(timeout=3)
        current = other.session(owner, row['id'])
        other.update_session(owner, row['id'], revision=current['revision'], changes={'title':'Manual winner'})
        apply_title(service, row['id'], 'Later automatic content')
        assert service.session(owner, row['id'])['title'] == 'Manual winner'
        assert service.store.get('conversation', row['id'])['title_origin'] == 'manual'
    finally:
        release.set(); other.store.close()


def test_real_plan_exploration_and_approved_execution_share_automatic_checkout(coding_workspace, monkeypatch):
    from functools import partial
    from test_plan_mode_api import _client
    from test_plan_mode import _call
    from test_agent import wait_for_pending_approval, wait_for_status
    service, owner, root, _, _, _ = coding_workspace
    runtime = root.parent / 'plan-runtime'; runtime.mkdir()
    script = [('', [_call('read_file', 'read-baseline', path='base.txt')]),
              ('', [_call('exit_plan', 'review-plan', plan_markdown='Write a reviewed output in this checkout')]),
              ('', [_call('run_shell', 'write-plan-output', command='printf isolated-plan > plan-output.txt', purpose='Write approved output')]),
              ('Implemented.', [])]
    state, adapter, client = _client(runtime, monkeypatch, script)
    actor = state.identity.setup_owner('plan-owner', 'plan-fixture-password-123')
    credentials = asyncio.run(state.identity.login('local-password', {'username':'plan-owner','password':'plan-fixture-password-123'}, peer='fixture'))
    headers = {'Authorization':'Bearer '+credentials.access_token}
    project = state.projects.register(str(root), name='Plan fixture')
    row = state.workspace.create_session(actor, project_id=project['id'], mode='plan', provider_id='fake', model='fake-model')
    (root / 'base.txt').write_text('Dirty source remains private to source checkout\n')
    original = (root / 'base.txt').read_bytes()
    with client:
        task = client.portal.call(partial(state.workspace.send, actor, row['id'], prompt='Plan reviewed output', request_id='automatic-plan-turn'))
        _, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task['id'], 'plan')
        bound = state.workspace.session(actor, row['id'])
        assert bound['worktree_id'] and task['cwd'] == bound['cwd'] != str(root)
        assert adapter.read_only_flags[:2] == [True, True]
        assert (Path(bound['cwd']) / 'base.txt').read_text().startswith('Needle')
        result = client.post('/api/workspace/tasks/'+task['id']+'/approvals/'+approval['id']+'/resolve', headers=headers, json={'decision':'approved'})
        assert result.status_code == 200, result.text
        done = client.portal.call(wait_for_status, state.agent_store, task['id'], 'completed')
        assert done['cwd'] == bound['cwd'] and done['mode'] == 'agent'
        assert (Path(bound['cwd']) / 'plan-output.txt').read_text() == 'isolated-plan'
        assert not (root / 'plan-output.txt').exists() and (root / 'base.txt').read_bytes() == original


def test_existing_unisolated_git_plan_is_blocked_before_approval_is_consumed(coding_workspace, monkeypatch):
    from functools import partial
    from test_plan_mode_api import _client, _RESOLVE
    from test_plan_mode import _call
    from test_agent import wait_for_pending_approval
    service, owner, root, _, _, _ = coding_workspace
    runtime = root.parent / 'legacy-plan-runtime'; runtime.mkdir()
    state, adapter, client = _client(runtime, monkeypatch, [('', [_call('exit_plan', 'review-legacy-plan', plan_markdown='Plan from original folder')])])
    actor = state.identity.setup_owner('legacy-plan-owner', 'plan-fixture-password-123')
    credentials = asyncio.run(state.identity.login('local-password', {'username':'legacy-plan-owner','password':'plan-fixture-password-123'}, peer='fixture'))
    headers = {'Authorization':'Bearer '+credentials.access_token}
    project = state.projects.register(str(root), name='Legacy plan fixture')
    row = state.workspace.create_session(actor, project_id=project['id'], mode='plan', provider_id='fake', model='fake-model')
    metadata = state.workspace.store.get('conversation', row['id'])
    state.workspace.store.update('conversation', row['id'], {**metadata, 'checkout_policy':'direct'})
    with client:
        task = client.portal.call(partial(state.workspace.send, actor, row['id'], prompt='Original baseline plan', request_id='legacy-plan-turn'))
        _, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task['id'], 'plan')
        url = '/api/workspace/tasks/'+task['id']+'/approvals/'+approval['id']+'/resolve'
        response = client.post(url, headers=headers, json={'decision':'approved'})
        assert response.status_code == 409 and 'original project folder' in response.text
        gql = client.post('/graphql', headers=headers, json={'query':_RESOLVE, 'variables':{'task':task['id'],'approval':approval['id'],'input':{'decision':'approved'}}}).json()
        assert gql.get('errors') and 'original project folder' in json.dumps(gql)
        assert state.agent_store.get_approval(approval['id'])['status'] == 'pending'
        assert state.agent_store.get_task(task['id'])['mode'] == 'plan' and task['cwd'] == str(root)


@pytest.mark.anyio
async def test_explicit_placeholder_name_can_remain_manual_and_generated_titles_redact_secrets(coding_workspace):
    service, owner, root, _, _, _ = coding_workspace
    named = service.create_session(owner, title='New conversation', title_auto=False, cwd=str(root), provider_id='fixture', model='fixture')
    await service.send(owner, named['id'], prompt='A different title request', request_id='explicit-placeholder-title')
    assert service.session(owner, named['id'])['title'] == 'New conversation'
    assert service.session(owner, named['id'])['title_origin'] == 'manual'
    automatic = service.create_session(owner, title='New conversation', cwd=str(root), provider_id='fixture', model='fixture')
    await service.send(owner, automatic['id'], prompt='Review password=fixture-secret-value before fixing the requested checkout lifecycle details and adding durable continuation support', request_id='redacted-generated-title')
    title = service.session(owner, automatic['id'])['title']
    assert 'fixture-secret-value' not in title and len(title) <= 72


@pytest.mark.anyio
async def test_successful_cross_service_manual_rename_during_checkout_is_never_overwritten(coding_workspace, monkeypatch):
    from threading import Event
    service, owner, root, _, row, _ = coding_workspace
    other = WorkspaceService(service.state, service.store.path, project_check=lambda *args: None)
    entered = Event(); release = Event()
    original = service.store.update
    def paused(kind, identifier, body, revision=None):
        if kind == 'conversation' and identifier == row['id'] and body.get('worktree_id') and not release.is_set():
            entered.set(); assert release.wait(3)
        return original(kind, identifier, body, revision)
    monkeypatch.setattr(service.store, 'update', paused)
    sending = asyncio.create_task(service.send(owner, row['id'], prompt='Automatic proposed title', request_id='rename-during-checkout'))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        current = other.session(owner, row['id'])
        renamed = other.update_session(owner, row['id'], revision=current['revision'], changes={'title':'Manual during checkout'})
        assert renamed['title_origin'] == 'manual'
        release.set()
        with pytest.raises(Conflict):
            await sending
        assert other.session(owner, row['id'])['title'] == 'Manual during checkout'
        task = await service.send(owner, row['id'], prompt='Automatic proposed title', request_id='rename-during-checkout')
        assert task['cwd'] != str(root)
        assert service.session(owner, row['id'])['title'] == 'Manual during checkout'
        assert service.session(owner, row['id'])['title_origin'] == 'manual'
    finally:
        release.set()
        if not sending.done():
            await sending
        other.store.close()
