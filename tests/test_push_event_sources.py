"""The sources of the new push kinds, against the real services: the task graph finishing, a CLI session asking for
attention, a host restart recovering work, and a CLI permission answer riding the existing ``approval.requested`` push.

Every push body is a host-written status line; the tests plant a distinctive prompt and terminal output and assert none
of it appears in what would be sent.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cli_fake import posix_only, wait_for, write_fake_cli
from test_agent import _FanOutAdapter, _fn, wait_for_pending_approval
from test_orchestrator_graph import setup as graph_setup, settle, spec
from test_workspace_orchestrator import close, running_primary, setup as workspace_setup
from termx.app import AppState, create_app
from termx.cli_sessions import status as S
from termx.cli_sessions.service import CliSessionService
from termx.development.delivery import DeliveryService
from termx.host_status import HostStatusService
from termx.project_files import ProjectFiles
from termx.push import PushDelivery
from termx.sessions import SessionManager
from termx.workspace.task_graph import TaskGraph

TOKEN = 'ExpoPushToken[device_aaaaaaaa]'
FAST = {'quiet_after': 0.5, 'idle_after': 3.0, 'sustain': 0.6}


def push_for(tmp_path, owner, manager, workspace=None, cli=None):
    """A PushDelivery over a real manager/workspace with one device registered for ``owner`` (authority allowed)."""
    session = SimpleNamespace(session_id='s1', principal=SimpleNamespace(id=owner.id))
    state = SimpleNamespace(
        identity=SimpleNamespace(configured=True, resolve=lambda secret: session if secret == 's1' else None, session_by_id=lambda identifier: session if identifier == 's1' else None),
        auth=SimpleNamespace(passcode=None, allows=lambda secret, scope: secret == 's1'),
        authorization=SimpleNamespace(require_principal=lambda principal, scope, **kwargs: None),
        agent=manager, workspace=workspace, cli_sessions=cli)
    service = PushDelivery(state, tmp_path / 'push.db')
    service.register('s1', TOKEN, 'http://localhost:8787')
    return service


def jobs(service, kind):
    return service.db.execute('SELECT task,title,body,audience FROM push_jobs WHERE kind=?', (kind,)).fetchall()


def everything_queued(service):
    return json.dumps(service.db.execute('SELECT * FROM push_jobs').fetchall())


def test_the_graph_finishing_pushes_once_with_counts_and_never_a_title_prompt_or_result(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity = graph_setup(tmp_path)
        push = push_for(tmp_path, owner, manager, workspace)
        push.attach()
        try:
            a, b = graph.plan(owner, [spec('a', 'TOPSECRET payroll prompt'), spec('b', 'second', ['a'])])
            await graph.start_ready(owner)
            await settle(ledger, graph, owner, a['id'], 'completed')
            assert jobs(push, 'orchestrator.report') == []  # one node still pending: not finished
            await graph.start_ready(owner)
            await settle(ledger, graph, owner, b['id'], 'completed')
            ((resource, title, body, audience),) = jobs(push, 'orchestrator.report')
            assert resource == workspace.orchestrator.binding(owner.id)['conversation_id'] and audience == owner.id
            assert (title, body) == ('Main agent finished its plan', '2 completed.')
            assert 'TOPSECRET' not in everything_queued(push) and 'finished' not in body.lower().replace('finished its plan', '')
            graph.board(owner)  # reading again, or a new process picking the graph up, announces nothing more
            restarted = TaskGraph(workspace.orchestrator)
            restarted.add_listener(push.on_graph_event)
            restarted.resume()
            assert len(jobs(push, 'orchestrator.report')) == 1
            await restarted.close()
            # more work, finished again at once: the per-resource interval keeps it from becoming a storm
            (c,) = graph.plan(owner, [spec('c', 'third')])
            await graph.cancel(owner, c['id'])
            assert len(jobs(push, 'orchestrator.report')) == 1
        finally:
            await push.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())


@posix_only
def test_a_cli_session_asking_for_attention_pushes_once_and_carries_no_prompt_or_screen_text(tmp_path):
    async def run():
        workspace, owner, manager, ledger, primary = workspace_setup(tmp_path)
        state = workspace.state
        state.workspace, state.sessions, state.port = workspace, SessionManager(), 8787
        state.projects, state.delivery = ProjectFiles(), DeliveryService(tmp_path / 'delivery')
        service = state.cli_sessions = CliSessionService(state, directory=tmp_path / 'cli-sessions', tick=0.1, thresholds=dict(FAST), grace=(0.6, 0.6),
            executables={'claude': write_fake_cli(tmp_path / 'bin')})
        push = push_for(tmp_path, owner, manager, workspace, service)
        push.attach()
        try:
            folder = tmp_path / 'cli-folder'
            folder.mkdir()
            started = await service.start(owner=owner.id, cli='claude', cwd=str(folder), prompt='Refactor the payments module for ACME')
            sid = started['id']
            await wait_for(lambda: jobs(push, 'cli.session.attention'))
            ((resource, title, body, audience),) = jobs(push, 'cli.session.attention')
            assert resource == started['pty_session_id'] and audience == owner.id
            assert title == 'CLI session is waiting for you' and body == 'Claude Code is waiting for input.'
            # a leak, a permission dialog and a flip back to attention: no storm, and never the screen
            await service.send(owner.id, sid, 'secret')
            await wait_for(lambda: 'leaked' in service.read(owner.id, sid)['text'])
            await service.send(owner.id, sid, 'approve')
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.NEEDS_APPROVAL)
            queued = everything_queued(push)
            for forbidden in ('payments', 'ACME', 'sk-abcdef', 'hunter2', 'Do you want to proceed', 'leaked'):
                assert forbidden not in queued, forbidden
            assert len(jobs(push, 'cli.session.attention')) == 1  # one push per session per interval, only on the flip
            await service.stop(owner.id, sid)
        finally:
            await service.close()
            state.sessions.kill_all()
            await push.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_permission_prompt_flip_reads_as_needs_approval_status_only(tmp_path):
    async def run():
        workspace, owner, manager, ledger, primary = workspace_setup(tmp_path)
        record = {'owner': owner.id, 'pty_session_id': 'pty-7', 'prompt': 'never in a push'}
        cli = SimpleNamespace(store=SimpleNamespace(get=lambda kind, identifier: record), add_listener=lambda fn: None)
        push = push_for(tmp_path, owner, manager, workspace, cli)
        try:
            push.on_cli_event('cli.session.status', {'id': 'c1', 'status': 'working', 'attention': False, 'label': 'Claude Code'})
            push.on_cli_event('cli.session.status', {'id': 'c1', 'status': 'needs_approval', 'attention': True, 'label': 'Claude Code'})
            push.on_cli_event('cli.session.status', {'id': 'c1', 'status': 'needs_approval', 'attention': True, 'label': 'Claude Code'})
            assert jobs(push, 'cli.session.attention') == [('pty-7', 'CLI session needs approval', 'Claude Code is waiting for a permission decision.', owner.id)]
            assert 'never in a push' not in everything_queued(push)
        finally:
            await push.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_host_recovered_is_pushed_after_a_restart_that_recovered_work(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))

    async def no_network(self, client):  # the lifespan's worker must never reach Expo from a test
        return None

    monkeypatch.setattr(PushDelivery, 'drain', no_network)
    state = AppState(passcode='host-secret')
    app = create_app(state)
    state.push.register('host-secret', TOKEN, 'http://localhost:8787')
    state.sessions.reattach = lambda: [object(), object()]
    state.cli_sessions.recover = lambda: _done({'adopted': 1, 'finished': 0, 'lost': 1})
    with TestClient(app, base_url='http://localhost'):
        rows = jobs(state.push, 'host.recovered')
    assert rows == [('host', 'TermX restarted and recovered work', '1 terminal kept; 1 CLI session re-attached; 1 CLI session lost.', None)]


async def _done(value):
    return value


def test_host_recovered_counts_recovering_tasks_and_stays_silent_when_nothing_was_recovered():
    captured = []
    state = SimpleNamespace(agent=SimpleNamespace(pending_recoveries=lambda: ['a', 'b']), push=SimpleNamespace(enqueue=lambda *a, **k: captured.append((a, k))))
    service = HostStatusService(state)
    service._push_recovered(0, {})
    assert captured[0][0][:4] == ('host.recovered', 'host', 'TermX restarted and recovered work', '2 tasks recovering.')
    state.agent.pending_recoveries = lambda: []
    captured.clear()
    service._push_recovered(0, {'adopted': 0, 'finished': 0, 'lost': 0})
    assert captured == []


@posix_only
def test_a_cli_permission_answer_from_the_main_agent_reaches_the_existing_approval_push(tmp_path):
    """The main agent answering a CLI's permission prompt is an always-ask delegation; it surfaces as the normal
    ``approval.requested`` task event, which is what already notifies the owner's phone."""
    async def run():
        adapter = _FanOutAdapter({})
        workspace, owner, manager, ledger, primary = workspace_setup(tmp_path, adapter)
        state = workspace.state
        state.workspace, state.sessions, state.port = workspace, SessionManager(), 8787
        state.projects, state.delivery = ProjectFiles(), DeliveryService(tmp_path / 'delivery')
        service = state.cli_sessions = CliSessionService(state, directory=tmp_path / 'cli-sessions', tick=0.1, thresholds=dict(FAST), grace=(0.6, 0.6),
            executables={'claude': write_fake_cli(tmp_path / 'bin')})
        push = push_for(tmp_path, owner, manager, workspace, service)
        try:
            folder = tmp_path / 'cli-folder'
            folder.mkdir()
            started = await service.start(owner=owner.id, cli='claude', cwd=str(folder))
            sid = started['id']
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.WAITING_INPUT)
            await service.send(owner.id, sid, 'approve')
            await wait_for(lambda: service.get(owner.id, sid)['status'] == S.NEEDS_APPROVAL)
            adapter.routes['CoordinateCase'] = ([[_fn('answer-1', 'cli_session_send', session_id=sid, text='1')]], 0)
            parent = await running_primary(workspace, owner, manager, primary)
            _, approval = await wait_for_pending_approval(ledger, parent['id'], 'tool')
            assert approval['payload']['title'] or approval['payload']  # a real pending approval, not auto-answered
            assert 'answers for you' in json.dumps(approval)
            queued = push.db.execute("SELECT task,title,kind FROM push_jobs WHERE kind='task' AND title='Approval needed'").fetchall()
            assert (parent['id'], 'Approval needed', 'task') in queued
            assert 'answers for you' not in everything_queued(push) and 'Do you want to proceed' not in everything_queued(push)
            assert 'ECHO:1' not in service.read(owner.id, sid)['text']  # nothing was typed into the prompt before the owner decided
            await manager.resolve_approval(parent['id'], approval['id'], 'denied')
            await service.stop(owner.id, sid)
        finally:
            await service.close()
            state.sessions.kill_all()
            await push.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())
