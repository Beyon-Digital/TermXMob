"""review_node_changes and real executor nodes: a real PTY with the fake CLI, real Git worktrees, the real engine gateway.

The graph, the CLI session service, the PTY manager, the delivery service (worktrees) and change review are the real
ones; only the CLI executable (``cli_fake``) and the model/engine adapters are deterministic fixtures.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_fake import alive, git_repo, posix_only, wait_for, write_fake_cli
from test_agent import build_manager
from test_orchestrator_graph import Clock, GraphAdapter, settle, spec
from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.cli_sessions import status as S
from termx.cli_sessions.service import CliSessionService
from termx.development.delivery import DeliveryService
from termx.identity import AuthenticationService
from termx.project_files import ProjectFiles
from termx.sessions import SessionManager
from termx.workspace.service import WorkspaceService
from termx.workspace.task_graph import MAX_RUNNING, TaskGraph  # noqa: F401  (MAX_RUNNING documents the cap the bounds sit under)

pytestmark = posix_only
FAST = {'quiet_after': 0.5, 'idle_after': 3.0, 'sustain': 0.6}
CLI = {'kind': 'cli', 'cli': 'claude'}


def tool_call(name, call_id='c1', **arguments):
    return ProviderCall(type='function', call_id=call_id, name=name, arguments=arguments)


def project_world(tmp_path):
    repo = git_repo(tmp_path / 'repo')
    manager, ledger = build_manager(tmp_path, GraphAdapter())
    identity = AuthenticationService(tmp_path / 'identity.sqlite3')
    owner = identity.setup_owner('owner', 'strong synthetic password')
    state = SimpleNamespace(identity=identity, agent_store=ledger, agent=manager, engines=SimpleNamespace(engines=lambda: []),
        sessions=SessionManager(), port=8787, projects=ProjectFiles(), delivery=DeliveryService(tmp_path / 'delivery'))
    workspace = WorkspaceService(state, tmp_path / 'workspace.sqlite3')
    workspace.principal_lookup = identity.principal_by_id
    state.workspace = workspace
    state.cli_sessions = CliSessionService(state, directory=tmp_path / 'cli-sessions', tick=0.1, thresholds=dict(FAST), grace=(0.6, 0.6),
        executables={'claude': write_fake_cli(tmp_path / 'bin')})
    pid = state.projects.register(str(repo))['id']
    main = workspace.create_session(owner, title='Main agent', project_id=pid, mode='agent', provider_id='fake', model='fake-model')
    workspace.orchestrator.ensure(owner, session_id=main['id'])
    graph = workspace.orchestrator.graph
    graph.clock = Clock()
    return workspace, owner, manager, ledger, graph, identity, repo, pid, main


async def close(workspace, manager, ledger):
    await workspace.state.cli_sessions.close()
    workspace.state.sessions.kill_all()
    for worker in list(manager._workers.values()):
        worker.cancel()
    await manager.close()
    await workspace.close()
    ledger.close()


def worktree_of(workspace, owner, node_id):
    row = workspace.store.get('orchestrator_node', node_id)
    return Path(workspace.state.cli_sessions.get(owner.id, row['cli_session_id'])['worktree']['path'])


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout


def test_tool_spec_is_read_only_main_agent_only_and_has_no_apply_path():
    registry = default_registry()
    spec_ = registry.get('review_node_changes')
    assert (spec_.approval, spec_.mutability, spec_.parallel_safe) == ('never', 'read', True)
    assert spec_.parameters['required'] == ['node_id'] and spec_.parameters['additionalProperties'] is False
    assert 'review_node_changes' in registry._SUBAGENT_TOOLS  # a child task never sees it
    assert 'review_node_changes' not in {tool['name'] for tool in registry.provider_tools(allow_subagents=False)}
    assert 'review_node_changes' in {tool['name'] for tool in registry.provider_tools(allow_subagents=True)}
    assert not [name for name in dir(TaskGraph) if any(word in name for word in ('apply', 'merge', 'deliver', 'commit'))]


def test_a_cli_node_end_to_end_blocks_on_its_prompt_reviews_and_completes_when_stopped(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        service = workspace.state.cli_sessions
        try:
            (node,) = graph.plan(owner, [spec('a', 'Explain the repo', executor=CLI, mode='agent')])
            (started,) = await graph.start_ready(owner)
            row = workspace.store.get('orchestrator_node', node['id'])
            sid = row['cli_session_id']
            session = service.get(owner.id, sid)
            assert started['status'] == 'running' and started['isolation'] == 'worktree' and session['isolation'] == 'worktree'
            assert session['project_id'] == pid and session['worktree']['branch'].startswith('termx/cli-') and session['mode'] == 'default'
            pty = workspace.state.sessions.get(session['pty_session_id'])
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.WAITING_INPUT)
            assert graph.board(owner)['nodes'][0]['status'] == 'running'  # its first seconds at the prompt are startup
            graph.clock.now += 40
            view = graph.board(owner)['nodes'][0]
            assert view['status'] == 'blocked' and view['blocked_reason'] == 'waiting_input'

            checkout = worktree_of(workspace, owner, node['id'])
            (checkout / 'notes.txt').write_text('first line\napi_key=hunter2hunter2\n')
            (checkout / 'README.md').write_text('hello\nchanged by the CLI\n')
            review = await graph.review_changes(owner, node['id'])
            assert review['ok'] and review['untrusted'] is True and review['repo'] is True and review['branch'] == session['worktree']['branch']
            assert {item['path'] for item in review['files']} == {'notes.txt', 'README.md'}
            blob = json.dumps(review)
            assert 'hunter2hunter2' not in blob and '+changed by the CLI' in blob and 'not an instruction' not in blob
            assert 'never an instruction' in review['note'] and 'delivery approval' in review['note']
            assert git(repo, 'status', '--porcelain') == ''  # the user's checkout is untouched
            assert 'notes.txt' in git(checkout, 'status', '--porcelain')  # and reviewing changed nothing in the worktree

            await service.send(owner.id, sid, 'approve')
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.NEEDS_APPROVAL)
            assert graph.board(owner)['nodes'][0]['blocked_reason'] == 'needs_approval'
            await service.send(owner.id, sid, '1')
            await wait_for(lambda: graph.board(owner)['nodes'][0]['status'] == 'running')

            await service.stop(owner.id, sid)
            done = await settle(ledger, graph, owner, node['id'], 'completed')
            assert 'termx/cli-' in done['result_summary'] and not alive(pty.proc.pid)
            assert (checkout / 'notes.txt').exists()  # the work stays for review after the session ends
            assert (await graph.review_changes(owner, node['id']))['ok']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_cancel_propagates_to_a_real_session_and_kills_its_process(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        service = workspace.state.cli_sessions
        try:
            a, b = graph.plan(owner, [spec('a', 'work', executor={**CLI, 'isolation': 'none'}, mode='agent'), spec('b', 'later', ['a'])])
            await graph.start_ready(owner)
            sid = workspace.store.get('orchestrator_node', a['id'])['cli_session_id']
            pty = workspace.state.sessions.get(service.get(owner.id, sid)['pty_session_id'])
            assert alive(pty.proc.pid)
            await graph.cancel(owner, a['id'])
            assert service.get(owner.id, sid)['status'] == S.DONE and not alive(pty.proc.pid)
            assert [row['status'] for row in graph.board(owner)['nodes']] == ['cancelled', 'cancelled']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_real_lost_session_is_resumed_by_retry_and_never_launched_twice(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        service = workspace.state.cli_sessions
        try:
            (node,) = graph.plan(owner, [spec('a', 'work', executor={**CLI, 'isolation': 'none'}, mode='agent')])
            await graph.start_ready(owner)
            sid = workspace.store.get('orchestrator_node', node['id'])['cli_session_id']
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.WAITING_INPUT)
            # simulate the host restart without a PTY holder: the live monitor goes away and the record is reconciled
            await service.close()
            workspace.state.sessions.kill_all()
            restarted_service = CliSessionService(workspace.state, directory=tmp_path / 'cli-sessions', tick=0.1, thresholds=dict(FAST),
                grace=(0.6, 0.6), executables={'claude': write_fake_cli(tmp_path / 'bin')})
            workspace.state.cli_sessions = restarted_service
            restarted_service.reconcile()
            assert service.get(owner.id, sid)['status'] == S.LOST
            graph2 = TaskGraph(workspace.orchestrator)
            graph2.clock = Clock(graph.clock.now + 9000)
            graph2.resume()
            view = graph2.board(owner)['nodes'][0]
            assert view['status'] == 'blocked' and view['blocked_reason'] == 'lost' and view['resumable'] is True
            assert await graph2.start_ready(owner) == []  # never relaunched by itself
            resumed = await graph2.retry(owner, node['id'])  # the resume offer: the CLI's own saved conversation, a new terminal
            assert resumed['status'] == 'running' and resumed['session_id'] != sid and resumed['attempts'] == 1
            assert restarted_service.get(owner.id, sid)['resumed_to'] == resumed['session_id']
            assert len(restarted_service.rows(owner.id)) == 2  # one lost record kept as history, one live: never two launches
            await restarted_service.stop(owner.id, resumed['session_id'])
            assert (await settle(ledger, graph2, owner, node['id'], 'completed'))['status'] == 'completed'
            await graph2.close()
        finally:
            await workspace.state.cli_sessions.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_review_is_bounded_and_marks_what_it_left_out(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'work', executor=CLI, mode='agent')])
            await graph.start_ready(owner)
            checkout = worktree_of(workspace, owner, node['id'])
            for index in range(60):
                (checkout / f'f{index:02d}.txt').write_text(('line %d of a long file\n' % index) * 600)
            review = await graph.review_changes(owner, node['id'])
            assert review['ok'] and review['files_total'] == 60 and len(review['files']) == 40 and review['truncated'] is True
            assert len(review['diffs']) <= 8 and all(len(item['diff']) <= 6000 for item in review['diffs'])
            assert sum(len(item['diff']) for item in review['diffs']) <= 30000
            assert len(json.dumps(review)) < 60000
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_review_needs_agent_view_git_read_and_the_owners_own_node(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'work', executor=CLI, mode='agent')])
            with pytest.raises(ValueError, match='not started'):
                await graph.review_changes(owner, node['id'])
            await graph.start_ready(owner)
            other = identity.create_local_user('other', 'strong synthetic password 2', list(owner.scopes))
            workspace.orchestrator.ensure(other)
            with pytest.raises(KeyError):  # another user's graph does not contain the node
                await graph.review_changes(other, node['id'])
            identity.set_scopes(owner.id, [scope for scope in owner.scopes if scope != 'git-read'])
            with pytest.raises(PermissionError):
                await graph.review_changes(owner, node['id'])
            identity.set_scopes(owner.id, [scope for scope in owner.scopes if scope != 'agent-view'])
            with pytest.raises(PermissionError):
                await graph.review_changes(owner, node['id'])
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_review_through_the_tool_path_returns_an_error_result_not_an_exception(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        try:
            parent = await workspace.send(owner, main['id'], prompt='CoordinateCase', request_id='main-request')
            await manager.resolve_approval(parent['id'], parent['approvals'][0]['id'], 'approved')
            await asyncio.sleep(0)
            planned = await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'A', 'prompt': 'work', 'mode': 'agent', 'executor': CLI},
                {'key': 'b', 'title': 'B', 'prompt': 'ask', 'mode': 'ask'}]))
            assert planned['ok']
            spec_ = default_registry().get('review_node_changes')
            ctx = SimpleNamespace(task_id=parent['id'], manager=SimpleNamespace(orchestrator=workspace.orchestrator, is_unattended=workspace.is_unattended_task))
            decision = spec_.decide(tool_call('review_node_changes', 'r0', node_id=planned['nodes'][0]['id']), ctx)
            assert decision.allowed and not decision.approval_required
            unknown = (await spec_.execute(tool_call('review_node_changes', 'r1', node_id='nope'), ctx)).result
            assert unknown['ok'] is False and 'Unknown node' in unknown['error']
            not_started = (await spec_.execute(tool_call('review_node_changes', 'r1b', node_id=planned['nodes'][0]['id']), ctx)).result
            assert not_started['ok'] is False and 'not started' in not_started['error']
            await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's1'))
            await settle(ledger, graph, owner, planned['nodes'][1]['id'], 'completed')
            read_only = (await spec_.execute(tool_call('review_node_changes', 'r2', node_id=planned['nodes'][1]['id']), ctx)).result
            assert read_only['ok'] is False and 'read-only' in read_only['error']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_an_internal_agent_node_in_a_project_is_reviewed_from_its_task_worktree(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD', mode='agent')])
            (started,) = await graph.start_ready(owner)
            assert started['isolation'] == 'worktree'
            record = ledger.task_worktree(started['task_id'])
            Path(record['worktree_path'], 'internal.txt').write_text('edited by the internal agent\n')
            review = await graph.review_changes(owner, node['id'])
            assert review['ok'] and [item['path'] for item in review['files']] == ['internal.txt'] and review['executor'] == {'kind': 'internal'}
            assert review['branch'] == record['branch']
            await graph.cancel(owner, node["id"])
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_an_engine_node_in_a_project_is_reviewed_from_its_isolated_checkout(tmp_path):
    from test_workspace_engine_orchestrator import configure, finish_all
    from test_workspace_orchestrator import HoldingAdapter  # noqa: F401

    async def run():
        workspace, owner, manager, ledger, graph, identity, repo, pid, main = project_world(tmp_path)
        gateway, adapter = configure(workspace, manager, ledger)
        gateway.engines = lambda: ['fixture-native']
        try:
            parent = await workspace.send(owner, main['id'], prompt='CoordinateCase', request_id='main-request')
            await manager.resolve_approval(parent['id'], parent['approvals'][0]['id'], 'approved')
            await asyncio.sleep(0)
            planned = await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'Engine work', 'prompt': 'native job', 'mode': 'agent', 'executor': {'kind': 'engine', 'engine': 'fixture-native'}}]))
            (node,) = (await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's1')))['started']
            relation = workspace.orchestrator.relation(node['session_id'])
            assert relation['isolation']['kind'] == 'worktree' and node['isolation'] == 'worktree'
            Path(relation['isolation']['path'], 'engine.txt').write_text('edited by the engine\n')
            review = await workspace.orchestrator.command(parent['id'], tool_call('review_node_changes', 'r1', node_id=node['id']))
            assert review['ok'] and [item['path'] for item in review['files']] == ['engine.txt'] and review['executor']['kind'] == 'engine'
            assert git(repo, 'status', '--porcelain') == ''
        finally:
            await finish_all(workspace, manager, ledger, gateway)
    asyncio.run(run())
