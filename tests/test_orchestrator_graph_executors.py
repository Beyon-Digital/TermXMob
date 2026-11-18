"""Task-graph executors: internal sub-agents, protocol engines and interactive CLI sessions.

Validation, routing, settling from engine/CLI events, restart safety, cancel propagation and the approval path run
against a fake CLI session service (fast, deterministic) and the real engine gateway fixture. The review tool and an
end-to-end CLI node (a real PTY, a real Git worktree, the fake CLI executable) are covered further down.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from test_orchestrator_graph import Clock, close, setup, settle, spec
from termx.agent.policies.engine import PolicyEngine
from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.agent.tools.registry import ToolContext
from termx.cli_sessions.service import KIND as CLI_KIND, CliSessionError, CliSessionService
from termx.workspace.store import Conflict
from termx.workspace.task_graph import CLI_WAIT_GRACE, MAX_RUNNING, TaskGraph, executor_of

CLI = {'kind': 'cli', 'cli': 'claude'}


def tool_call(name, call_id='c1', **arguments):
    return ProviderCall(type='function', call_id=call_id, name=name, arguments=arguments)


class FakeCli:
    """The slice of CliSessionService the graph uses, writing real ``cli_session`` records into the workspace store."""

    def __init__(self, store):
        self.store = store
        self.listeners = []
        self.starts, self.stops, self.resumes = [], [], []
        self.installed = {'claude', 'codex', 'gemini', 'aider'}
        self.refuse = None

    def add_listener(self, listener):
        self.listeners.append(listener)

    def executable(self, cli):
        return f'/usr/bin/fake-{cli}' if cli in self.installed else None

    @staticmethod
    def body(cli, cwd, **extra):
        return {'cli': cli, 'label': cli.title(), 'title': cli, 'status': 'starting', 'status_source': 'heuristic', 'status_detail': 'launched',
                'status_updated_at': time.time(), 'cwd': cwd, 'isolation': 'none', 'worktree': None, 'pty_session_id': 'pty-' + cli,
                'sandbox_profile': 'host', 'mode': 'default', 'started_at': time.time(), 'ended_at': None, 'exit_code': None,
                'ended_reason': None, 'native_session_id': None, 'resumable': True, 'resumed_from': None, 'resumed_to': None, **extra}

    def emit(self, kind, row):
        for listener in list(self.listeners):
            listener(kind, CliSessionService.public(row))

    async def start(self, *, owner, cli, cwd, project_id=None, prompt=None, mode='default', isolation='none', title=None, claim=None,
                    session_id=None, **_):
        if self.refuse:
            raise self.refuse
        self.starts.append({'owner': owner, 'cli': cli, 'cwd': cwd, 'project_id': project_id, 'prompt': prompt, 'mode': mode,
                            'isolation': isolation, 'session_id': session_id, 'title': title})
        row = self.store.create(CLI_KIND, owner, self.body(cli, cwd, mode=mode, isolation=isolation), project_id, session_id)
        self.emit('cli.session.created', row)
        return CliSessionService.public(row)

    def get(self, owner, session_id):
        row = self.store.get(CLI_KIND, session_id)
        if row is None or row['owner'] != owner:
            raise CliSessionError(404, 'CLI session not found')
        return CliSessionService.public(row)

    def set(self, session_id, status, **extra):
        row = self.store.get(CLI_KIND, session_id)
        ended = status in {'done', 'failed', 'lost'}
        body = {**row, 'status': status, **({'ended_at': time.time(), 'ended_reason': extra.pop('ended_reason', 'exited')} if ended else {}), **extra}
        saved = self.store.update(CLI_KIND, session_id, body)
        self.emit('cli.session.exited' if ended else 'cli.session.status', saved)
        return saved

    async def stop(self, owner, session_id):
        self.stops.append(session_id)
        row = self.store.get(CLI_KIND, session_id)
        if row is None or row['owner'] != owner:
            raise CliSessionError(404, 'CLI session not found')
        if row['status'] not in {'done', 'failed', 'lost'}:
            self.set(session_id, 'done', exit_code=0, ended_reason='stopped')
        return self.get(owner, session_id)

    async def resume(self, owner, session_id, confirm_bypass=False, claim=None):
        self.resumes.append(session_id)
        old = self.store.get(CLI_KIND, session_id)
        if old.get('resumed_to'):
            raise CliSessionError(409, 'This CLI session was already resumed')
        new_id = f'{len(self.resumes):032x}'
        row = self.store.create(CLI_KIND, owner, self.body(old['cli'], old['cwd'], resumed_from=session_id), old.get('project_id'), new_id)
        self.store.update(CLI_KIND, session_id, {**old, 'resumed_to': new_id})
        self.emit('cli.session.created', row)
        return CliSessionService.public(row)


def world(tmp_path, **kwargs):
    workspace, owner, manager, ledger, graph, identity = setup(tmp_path, **kwargs)
    fake = FakeCli(workspace.store)
    workspace.state.cli_sessions = fake
    return workspace, owner, manager, ledger, graph, fake


def events(workspace, owner, kind):
    return sorted((row for row in workspace.store.list('orchestrator_event', owner.id) if row['kind'] == kind), key=lambda row: row['sequence'])


def row_of(graph, owner, node_id):
    return next(row for row in graph.board(owner)['nodes'] if row['id'] == node_id)


def only_session(fake):
    (start,) = fake.starts
    return start['session_id']


# -- plan validation -----------------------------------------------------------------------------------


def test_executor_defaults_to_internal_and_is_stored_normalised(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            nodes = graph.plan(owner, [spec('a'), spec('b', executor={'kind': 'internal'}),
                spec('c', executor={'kind': 'cli', 'cli': 'codex'}, mode='agent'),
                spec('d', executor={'kind': 'cli', 'cli': 'claude', 'isolation': 'none', 'mode': 'read_only'})])
            assert [n['executor'] for n in nodes] == [{'kind': 'internal'}, {'kind': 'internal'},
                {'kind': 'cli', 'cli': 'codex', 'mode': 'default', 'isolation': 'worktree'},
                {'kind': 'cli', 'cli': 'claude', 'mode': 'read_only', 'isolation': 'none'}]
            # a read-only (ask) node asks the CLI for its read-only mode
            (ask,) = graph.plan(owner, [spec('e', executor=CLI)])
            assert ask['executor']['mode'] == 'read_only'
            stored = workspace.store.get('orchestrator_node', ask['id'])
            assert stored['executor'] == ask['executor']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


@pytest.mark.parametrize('executor, message', [
    ('cli', 'must be an object'),
    ({'kind': 'sideways'}, 'kind must be one of'),
    ({'kind': 'cli'}, 'Choose one of'),
    ({'kind': 'cli', 'cli': 'bash'}, 'Choose one of'),
    ({'kind': 'cli', 'cli': 'claude', 'argv': ['--x']}, 'unknown fields'),
    ({'kind': 'cli', 'cli': 'claude', 'mode': 'bypass'}, 'bypass is never available'),
    ({'kind': 'cli', 'cli': 'gemini', 'mode': 'read_only'}, 'no read_only mode'),
    ({'kind': 'cli', 'cli': 'claude', 'isolation': 'chroot'}, 'isolation must be'),
    ({'kind': 'cli', 'cli': 'claude', 'engine': 'codex'}, 'belongs to a engine executor'),
    ({'kind': 'engine'}, 'not configured'),
    ({'kind': 'engine', 'engine': 'internal'}, 'not configured'),
    ({'kind': 'engine', 'engine': 'made-up'}, 'not configured'),
    ({'kind': 'engine', 'engine': 'codex', 'cli': 'claude'}, 'belongs to a cli executor'),
    ({'kind': 'engine', 'engine': 'codex', 'isolation': 'none'}, 'always run isolated'),
    ({'kind': 'engine', 'engine': 'acp-x', 'mode': 'ask'}, 'not verified'),
    ({'kind': 'internal', 'mode': 'agent'}, 'takes no mode'),
    ({'kind': 'internal', 'cli': 'claude'}, 'belongs to a cli executor'),
])
def test_plan_refuses_anything_outside_the_allowlists(tmp_path, executor, message):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        workspace.state.engines = SimpleNamespace(engines=lambda: ['codex', 'acp-x'])
        try:
            with pytest.raises(ValueError, match=message):
                graph.plan(owner, [spec('a', executor=executor)])
            assert graph.board(owner)['nodes'] == []  # nothing from a refused call is stored
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_engine_ids_come_from_the_gateway_and_a_bot_cannot_run_elsewhere(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        workspace.state.engines = SimpleNamespace(engines=lambda: ['codex'])
        try:
            (node,) = graph.plan(owner, [spec('a', executor={'kind': 'engine', 'engine': 'codex'}, mode='agent')])
            assert node['executor'] == {'kind': 'engine', 'engine': 'codex', 'mode': 'agent'}
            with pytest.raises(ValueError, match='bot runs on the internal executor'):
                graph.plan(owner, [spec('b', executor=CLI, bot_id='whoever')])
            workspace.state.engines = SimpleNamespace(engines=lambda: [])
            with pytest.raises(ValueError, match='not configured'):
                graph.plan(owner, [spec('c', executor={'kind': 'engine', 'engine': 'codex'}, mode='agent')])
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_nodes_stored_before_executors_existed_behave_exactly_as_before(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'old node')])
            stored = workspace.store.get('orchestrator_node', node['id'])
            legacy = {key: value for key, value in stored.items() if key not in {'executor', 'id', 'owner', 'project_id', 'revision', 'created_at', 'updated_at'}}
            workspace.store.update('orchestrator_node', node['id'], legacy)
            assert 'executor' not in workspace.store.get('orchestrator_node', node['id'])
            assert executor_of(workspace.store.get('orchestrator_node', node['id'])) == {'kind': 'internal'}
            view = graph.board(owner)['nodes'][0]
            assert view['executor'] == {'kind': 'internal'} and view['blocked_reason'] is None and view['session_id'] is None
            (started,) = await graph.start_ready(owner)
            await settle(ledger, graph, owner, started['id'], 'completed')
            assert fake.starts == [] and fake.stops == []  # the CLI service is not involved
            assert workspace.store.get('orchestrator_node', node['id'])['task_id']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- routing and approval ----------------------------------------------------------------------------


def test_a_cli_node_starts_a_session_with_the_nodes_brief_and_an_id_recorded_first(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            a, b = graph.plan(owner, [spec('a', 'first part'), spec('b', 'second part', ['a'], executor=CLI, mode='agent')])
            (done,) = await graph.start_ready(owner)
            await settle(ledger, graph, owner, a['id'], 'completed')
            (started,) = await graph.start_ready(owner)
            assert started['id'] == b['id'] and started['status'] == 'running' and started['attempts'] == 1
            (start,) = fake.starts
            assert (start['cli'], start['mode'], start['isolation'], start['project_id'], start['owner']) == ('claude', 'default', 'none', None, owner.id)
            assert 'second part' in start['prompt'] and 'finished' in start['prompt']  # dependency summary rides along as evidence
            assert len(start['session_id']) == 32 and str(workspace.scratch_root(owner.id)) in start['cwd']
            row = workspace.store.get('orchestrator_node', b['id'])
            assert row['cli_session_id'] == start['session_id'] and row['cli_session_ids'] == [start['session_id']] and row['task_id'] is None
            view = row_of(graph, owner, b['id'])
            assert view['session_id'] == start['session_id'] and view['isolation'] == 'direct'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_refused_cli_launch_fails_the_node_and_uses_an_attempt(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        fake.refuse = CliSessionError(409, 'Claude Code is not installed on this host')
        try:
            (node,) = graph.plan(owner, [spec('a', executor=CLI, mode='agent')])
            await graph.start_ready(owner)
            view = row_of(graph, owner, node['id'])
            assert view['status'] == 'failed' and 'not installed' in view['error'] and view['attempts'] == 1
            assert len(events(workspace, owner, 'orchestrator.node_failed')) == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def decision_for(workspace, ledger, parent, call, *, auto=False):
    manager = SimpleNamespace(orchestrator=workspace.orchestrator, is_unattended=workspace.is_unattended_task)
    runtime = {'auto': {'enabled': True}} if auto else {}
    ctx = ToolContext(task_id=parent['id'], cwd=str(workspace.scratch_root('x')), task={**(ledger.get_task(parent['id']) or {}), 'runtime': runtime},
        read_only=False, cancel=asyncio.Event(), emit=lambda *_: {}, store=ledger, manager=manager, project_id='', policy_engine=PolicyEngine(ledger))
    return default_registry().get(call.name).decide(call, ctx)


def test_starting_cli_nodes_goes_through_the_same_always_ask_delegation_as_cli_session_start(tmp_path):
    from test_workspace_orchestrator import running_primary

    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        primary = workspace.orchestrator.binding(owner.id)['conversation_id']
        try:
            parent = await running_primary(workspace, owner, manager, {'id': primary})
            planned = await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'Fix parser', 'prompt': 'Fix the failing parser test', 'mode': 'agent', 'executor': CLI}]))
            assert planned['ok'] and planned['nodes'][0]['executor']['cli'] == 'claude'
            assert fake.starts == []  # planning starts nothing
            for auto in (False, True):
                decision = decision_for(workspace, ledger, parent, tool_call('start_ready', 's0'), auto=auto)
                assert decision.approval_required and not decision.auto_resolved, decision
                assert decision.intent.risk_class == 'delegation' and decision.required_capabilities == ('cli.session.start',)
                assert 'CLI session' in decision.consequence and 'Fix the failing parser test' in decision.consequence
                assert 'never used' in decision.consequence  # the same text the direct tool shows: no permission bypass
            direct = decision_for(workspace, ledger, parent, tool_call('cli_session_start', 'd0', cli='claude'), auto=True)
            assert direct.intent.risk_class == 'delegation' and direct.required_capabilities == ('cli.session.start',)
            retry = decision_for(workspace, ledger, parent, tool_call('retry_node', 'r0', node_id=planned['nodes'][0]['id']))
            assert retry.approval_required and retry.intent.risk_class == 'delegation'
            fake.installed.discard('claude')  # refused up front, before anyone is asked
            refused = decision_for(workspace, ledger, parent, tool_call('start_ready', 's1'))
            assert not refused.allowed and not refused.approval_required and 'not installed' in refused.consequence
            fake.installed.add('claude')
            started = await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's2'))
            assert started['ok'] and started['started'][0]['status'] == 'running' and len(fake.starts) == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_bypass_mode_can_never_be_reached_through_the_graph(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            for mode in ('bypass', 'yolo', ''):
                with pytest.raises(ValueError):
                    graph.plan(owner, [spec('a', executor={'kind': 'cli', 'cli': 'claude', 'mode': mode})])
            # a stored node that somehow carries a hostile mode fails instead of launching
            (node,) = graph.plan(owner, [spec('a', executor=CLI, mode='agent')])
            workspace.store.update('orchestrator_node', node['id'], {**workspace.store.get('orchestrator_node', node['id']),
                'executor': {'kind': 'cli', 'cli': 'claude', 'mode': 'bypass', 'isolation': 'none'}})
            await graph.start_ready(owner)
            view = row_of(graph, owner, node['id'])
            assert view['status'] == 'failed' and 'never available' in view['error'] and fake.starts == []
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- settling from CLI events ------------------------------------------------------------------------


def started_cli(workspace, owner, graph, fake):
    async def go():
        (node,) = graph.plan(owner, [spec('a', 'do it', executor=CLI, mode='agent')])
        await graph.start_ready(owner)
        return node['id'], only_session(fake)
    return go()


def test_cli_status_events_move_the_node_and_report_each_new_reason_once(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            # a CLI at its prompt during its first seconds is starting up, not "waiting" yet (SessionStart hook)
            fake.set(sid, 'waiting_input')
            assert row_of(graph, owner, node_id)['status'] == 'running'
            fake.set(sid, 'working')
            graph.clock.now += CLI_WAIT_GRACE + 1  # a turn that took a while, then the prompt is back
            fake.set(sid, 'waiting_input')
            view = row_of(graph, owner, node_id)
            assert view['status'] == 'blocked' and view['blocked_reason'] == 'waiting_input' and view['executor']['kind'] == 'cli'
            fake.set(sid, 'waiting_input', status_detail='again')  # the same state reported again changes nothing
            fake.set(sid, 'needs_approval')
            assert row_of(graph, owner, node_id)['blocked_reason'] == 'needs_approval'
            blocked = events(workspace, owner, 'orchestrator.node_blocked')
            assert [e['status'] for e in blocked] == ['blocked', 'blocked'] and len(blocked) == 2
            assert 'permission decision' in blocked[-1]['message'] and 'waiting for input' in blocked[0]['message']
            fake.set(sid, 'working')  # the Main agent (or the user) answered: back to running
            assert row_of(graph, owner, node_id)['status'] == 'running' and row_of(graph, owner, node_id)['blocked_reason'] is None
            fake.set(sid, 'done', exit_code=0)
            view = row_of(graph, owner, node_id)
            assert view['status'] == 'completed' and 'review_node_changes' in view['result_summary']
            assert len(events(workspace, owner, 'orchestrator.node_completed')) == 1
            fake.set(sid, 'failed', exit_code=1)  # a late, contradictory event never reopens a finished node
            assert row_of(graph, owner, node_id)['status'] == 'completed'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_needs_approval_blocks_at_once_and_waiting_input_blocks_after_the_start_grace(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            fake.set(sid, 'needs_approval')  # Claude's folder-trust dialog: a person must answer, so no grace
            assert row_of(graph, owner, node_id)['blocked_reason'] == 'needs_approval'
            other, = graph.plan(owner, [spec('b', 'second', executor=CLI, mode='agent')])
            await graph.start_ready(owner)
            second = fake.starts[1]['session_id']
            fake.set(second, 'waiting_input')
            assert row_of(graph, owner, other['id'])['status'] == 'running'
            graph.clock.now += CLI_WAIT_GRACE + 1  # nothing else came: it really is waiting for a first message
            assert row_of(graph, owner, other['id'])['blocked_reason'] == 'waiting_input'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_exit_codes_and_a_lost_terminal_settle_the_node(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            a, b, c = graph.plan(owner, [spec(key, key, executor=CLI, mode='agent') for key in 'abc'])
            await graph.start_ready(owner)
            sa, sb, sc = (start['session_id'] for start in fake.starts)
            fake.set(sa, 'failed', exit_code=3)
            failed = row_of(graph, owner, a['id'])
            assert failed['status'] == 'failed' and 'exit 3' in failed['error'] and failed['attempts'] == 1
            fake.set(sb, 'lost', resumable=True)
            lost = row_of(graph, owner, b['id'])
            assert lost['status'] == 'blocked' and lost['blocked_reason'] == 'lost' and lost['resumable'] is True
            assert 'retry_node resumes' in lost['error']
            fake.set(sc, 'lost', resumable=False)
            assert row_of(graph, owner, c['id'])['status'] == 'failed' and 'cannot resume' in row_of(graph, owner, c['id'])['error']
            assert len(events(workspace, owner, 'orchestrator.node_blocked')) == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_cli_nodes_count_against_the_running_limit_until_their_session_is_gone(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            nodes = graph.plan(owner, [spec(f'n{i}', f'work {i}', executor=CLI, mode='agent') for i in range(MAX_RUNNING + 1)])
            assert len(await graph.start_ready(owner)) == MAX_RUNNING and len(fake.starts) == MAX_RUNNING
            first = fake.starts[0]['session_id']
            fake.set(first, 'needs_approval')  # blocked on a person: its terminal is still alive, so it keeps its slot
            assert await graph.start_ready(owner) == [] and len(fake.starts) == MAX_RUNNING
            fake.set(first, 'lost', resumable=True)  # gone: the slot frees (the node waits for a resume)
            started = await graph.start_ready(owner)
            assert [n['title'] for n in started] == [nodes[-1]['title']] and len(fake.starts) == MAX_RUNNING + 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- restart safety ----------------------------------------------------------------------------------


def restart(workspace, fake, now):
    """A new process: a fresh graph and a CLI service that no longer notifies the old one."""
    fake.listeners.clear()
    graph = TaskGraph(workspace.orchestrator)
    graph.clock = Clock(now)
    graph.resume()
    return graph


def test_a_session_the_host_kept_alive_is_not_launched_again_after_a_restart(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            fake.set(sid, 'working')
            restarted = restart(workspace, fake, graph.clock.now + 5000)
            assert row_of(restarted, owner, node_id)['status'] == 'running'
            assert await restarted.start_ready(owner) == [] and len(fake.starts) == 1  # matched by its stored session id
            fake.set(sid, 'needs_approval')  # and the new process keeps following it
            assert row_of(restarted, owner, node_id)['blocked_reason'] == 'needs_approval'
            fake.set(sid, 'done', exit_code=0)
            assert row_of(restarted, owner, node_id)['status'] == 'completed' and len(fake.starts) == 1
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_session_that_ended_while_the_host_was_down_settles_the_node_on_resume(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            fake.listeners.clear()  # nobody was listening
            fake.set(sid, 'done', exit_code=0)
            assert workspace.store.get('orchestrator_node', node_id)['status'] == 'running'
            restarted = restart(workspace, fake, graph.clock.now + 5000)
            assert workspace.store.get('orchestrator_node', node_id)['status'] == 'completed'
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_lost_session_becomes_a_resumable_blocked_node_and_retry_resumes_without_a_second_launch(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            fake.listeners.clear()
            fake.set(sid, 'lost', resumable=True)  # the host restarted and the terminal did not survive
            restarted = restart(workspace, fake, graph.clock.now + 5000)
            view = row_of(restarted, owner, node_id)
            assert view['status'] == 'blocked' and view['blocked_reason'] == 'lost' and view['resumable']
            assert await restarted.start_ready(owner) == [] and len(fake.starts) == 1  # never relaunched by itself
            node = await restarted.retry(owner, node_id)
            assert fake.resumes == [sid] and len(fake.starts) == 1
            row = workspace.store.get('orchestrator_node', node_id)
            assert node['status'] == 'running' and row['cli_session_id'] != sid and row['cli_session_ids'][0] == sid and row['attempts'] == 1
            with pytest.raises(Conflict, match='running'):  # a second retry cannot resume it twice
                await restarted.retry(owner, node_id)
            fake.set(row['cli_session_id'], 'working')
            fake.set(row['cli_session_id'], 'done', exit_code=0)
            assert row_of(restarted, owner, node_id)['status'] == 'completed'
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_session_resumed_by_the_owner_elsewhere_is_followed_not_duplicated(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            node_id, sid = await started_cli(workspace, owner, graph, fake)
            fake.set(sid, 'lost', resumable=True)
            assert row_of(graph, owner, node_id)['blocked_reason'] == 'lost'
            resumed = await fake.resume(owner.id, sid)  # POST /api/cli-sessions/{id}/resume from a phone
            view = row_of(graph, owner, node_id)
            assert view['status'] == 'running' and view['session_id'] == resumed['id'] and len(fake.starts) == 1
            fake.set(resumed['id'], 'working')
            graph.clock.now += CLI_WAIT_GRACE + 1
            fake.set(resumed['id'], 'waiting_input')
            assert row_of(graph, owner, node_id)['blocked_reason'] == 'waiting_input'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_crash_between_recording_the_launch_and_the_session_never_relaunches(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'work', executor=CLI, mode='agent')])
            stored = workspace.store.get('orchestrator_node', node['id'])
            workspace.store.update('orchestrator_node', node['id'], {**stored, 'status': 'running', 'attempts': 1, 'cli_session_id': 'f' * 32,
                'cli_session_ids': ['f' * 32], 'cli_started_at': 1.0, 'last_event_at': 1.0})  # intent recorded, session never created
            restarted = restart(workspace, fake, 10_000.0)
            view = row_of(restarted, owner, node['id'])
            assert view['status'] == 'failed' and 'never created' in view['error'] and fake.starts == []
            assert await restarted.start_ready(owner) == []  # a failed node is not started by start_ready
            again = await restarted.retry(owner, node['id'])  # only an explicit retry starts it, and it records a new id first
            assert again['status'] == 'running' and len(fake.starts) == 1 and fake.starts[0]['session_id'] != 'f' * 32
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- cancel propagation ------------------------------------------------------------------------------


def test_cancelling_a_cli_node_stops_its_session_and_a_later_done_does_not_complete_it(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            a, b = graph.plan(owner, [spec('a', 'work', executor=CLI, mode='agent'), spec('b', 'after', ['a'])])
            await graph.start_ready(owner)
            sid = only_session(fake)
            fake.set(sid, 'working')
            graph.clock.now += CLI_WAIT_GRACE + 1
            fake.set(sid, 'waiting_input')
            assert row_of(graph, owner, a['id'])['status'] == 'blocked'  # cancel works on a blocked node too
            cancelled = await graph.cancel(owner, a['id'])
            assert fake.stops == [sid]
            assert sorted(row['status'] for row in cancelled) == ['cancelled', 'cancelled']  # and its dependent
            assert row_of(graph, owner, a['id'])['status'] == 'cancelled' and row_of(graph, owner, b['id'])['status'] == 'cancelled'
            assert workspace.store.get(CLI_KIND, sid)['status'] == 'done'  # the session's own exit event arrived...
            assert row_of(graph, owner, a['id'])['status'] == 'cancelled'  # ...and did not turn the node into "completed"
            assert not events(workspace, owner, 'orchestrator.node_completed')
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_cancel_tool_and_reassign_rules_for_non_internal_nodes(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'work', executor=CLI, mode='agent')])
            await graph.start_ready(owner)
            sid = only_session(fake)
            fake.set(sid, 'failed', exit_code=1)
            with pytest.raises(Conflict, match='Only internal nodes use a model provider'):
                await graph.reassign(owner, node['id'], model='another-model')
            retried = await graph.retry(owner, node['id'])  # a failed CLI node can be started again (second attempt)
            assert retried['status'] == 'running' and retried['attempts'] == 2 and len(fake.starts) == 2
            fake.set(fake.starts[1]['session_id'], 'failed', exit_code=1)
            with pytest.raises(Conflict, match='both attempts'):
                await graph.retry(owner, node['id'])
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- engine executor (the real gateway, a deterministic native adapter) ---------------------------------


def engine_world(tmp_path):
    from test_workspace_engine_orchestrator import configure
    from test_workspace_orchestrator import setup as workspace_setup

    workspace, owner, manager, ledger, primary = workspace_setup(tmp_path)
    gateway, adapter = configure(workspace, manager, ledger)
    gateway.engines = lambda: ['fixture-native']
    return workspace, owner, manager, ledger, primary, gateway, adapter


def test_an_engine_node_runs_through_engine_coordination_and_settles_from_its_task(tmp_path):
    from test_workspace_engine_orchestrator import finish_all
    from test_workspace_orchestrator import running_primary

    async def run():
        workspace, owner, manager, ledger, primary, gateway, adapter = engine_world(tmp_path)
        graph = workspace.orchestrator.graph
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            engine = {'kind': 'engine', 'engine': 'fixture-native'}
            planned = await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'Inspect', 'prompt': 'Inspect synthetic native sample', 'mode': 'agent', 'executor': engine},
                {'key': 'b', 'title': 'After', 'prompt': 'after', 'depends_on': ['a'], 'mode': 'ask'}]))
            assert planned['ok'] and planned['nodes'][0]['executor'] == {**engine, 'mode': 'agent'}
            decision = decision_for(workspace, ledger, parent, tool_call('start_ready', 's0'))
            assert decision.approval_required and 'engine fixture-native' in decision.consequence
            assert decision.intent.risk_class == 'delegation'
            # started by the user from the board there is no main-agent turn to parent it: it stays pending
            assert await graph.start_ready(owner) == [] and adapter.sent == []
            started = await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's1'))
            (node,) = started['started']
            assert node['status'] == 'running' and node['attempts'] == 1 and node['task_id'] and node['session_id']
            assert len(adapter.sent) == 1 and 'Inspect synthetic native sample' in adapter.sent[0][1]
            task = ledger.get_task(node['task_id'])
            assert task['engine'] == 'fixture-native' and task['parent_id'] == parent['id']
            # the same coordination path as spawn_engine_agent: its own receipt, durable child conversation
            assert workspace.orchestrator.relation(node['session_id'])['dispatch_call_id'] == f'graph:{node["id"]}:1'
            assert node['executor']['engine'] == 'fixture-native'
            again = await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's2'))
            assert again['started'] == [] and len(adapter.sent) == 1  # a running node is never started twice
            adapter.finish()
            done = await settle(ledger, graph, owner, node['id'], 'completed')
            assert done['result_summary'] == 'Synthetic native progress' and len(events(workspace, owner, 'orchestrator.node_completed')) == 1
            after = await workspace.orchestrator.command(parent['id'], tool_call('node_status', 'n1', node_id=planned['nodes'][1]['id']))
            assert after['nodes'][0]['blocked_by'] == []
        finally:
            await finish_all(workspace, manager, ledger, gateway)
    asyncio.run(run())


def test_cancelling_an_engine_node_cancels_the_native_session(tmp_path):
    from test_workspace_engine_orchestrator import finish_all
    from test_workspace_orchestrator import running_primary

    async def run():
        workspace, owner, manager, ledger, primary, gateway, adapter = engine_world(tmp_path)
        graph = workspace.orchestrator.graph
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'Inspect', 'prompt': 'long native job', 'mode': 'agent', 'executor': {'kind': 'engine', 'engine': 'fixture-native'}}]))
            (node,) = (await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's1')))['started']
            cancelled = await workspace.orchestrator.command(parent['id'], tool_call('cancel_node', 'c1', node_id=node['id']))
            assert [row['status'] for row in cancelled['cancelled']] == ['cancelled']
            assert adapter.cancelled == ['native-1']
            await asyncio.sleep(0.05)
            assert row_of(graph, owner, node['id'])['status'] == 'cancelled'
            with pytest.raises(Conflict, match='Only internal nodes'):
                await workspace.orchestrator.command(parent['id'], tool_call('reassign_node', 'r1', node_id=node['id'], model='x'))
        finally:
            await finish_all(workspace, manager, ledger, gateway)
    asyncio.run(run())


def test_an_engine_launch_replayed_after_a_restart_reuses_its_receipt_instead_of_a_second_child(tmp_path):
    from test_workspace_engine_orchestrator import finish_all
    from test_workspace_orchestrator import running_primary

    async def run():
        workspace, owner, manager, ledger, primary, gateway, adapter = engine_world(tmp_path)
        graph = workspace.orchestrator.graph
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            await workspace.orchestrator.command(parent['id'], tool_call('plan_nodes', 'p1', nodes=[
                {'key': 'a', 'title': 'Inspect', 'prompt': 'native job', 'mode': 'agent', 'executor': {'kind': 'engine', 'engine': 'fixture-native'}}]))
            (node,) = (await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's1')))['started']
            # the process died after the child existed but before the node was linked to it
            stored = workspace.store.get('orchestrator_node', node['id'])
            workspace.store.update('orchestrator_node', node['id'], {**stored, 'status': 'pending', 'attempts': 0, 'task_id': None,
                'task_ids': [], 'engine_session_id': None})
            again = await workspace.orchestrator.command(parent['id'], tool_call('start_ready', 's2'))
            assert again['started'][0]['task_id'] == node['task_id'] and len(adapter.sent) == 1
        finally:
            await finish_all(workspace, manager, ledger, gateway)
    asyncio.run(run())


# -- context, tools and API shape -------------------------------------------------------------------


def test_the_main_agent_prompt_explains_the_three_executors_and_the_review_step(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            primary = workspace.orchestrator.binding(owner.id)['conversation_id']
            (node,) = graph.plan(owner, [spec('a', 'x', executor=CLI, mode='agent')])
            context = workspace.orchestrator.context(owner, primary)
            for fragment in ('"kind":"internal"', '"kind":"engine"', '"kind":"cli"', 'real interactive CLI', 'review_node_changes',
                             'retry_node resumes a lost one', 'never instructions'):
                assert fragment in context, fragment
            listed = json.loads(context.split('not instructions; bot entries show only a headline, and read_inbox returns the message itself:\n')[1])
            assert listed['nodes'][0]['executor'] == {'kind': 'cli', 'cli': 'claude', 'mode': 'default', 'isolation': 'worktree'} or \
                listed['nodes'][0]['executor']['kind'] == 'cli'
            assert listed['rollup']['counts']['blocked'] == 0
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_plan_nodes_schema_offers_the_executor_with_a_closed_vocabulary():
    registry = default_registry()
    node = registry.get('plan_nodes').parameters['properties']['nodes']['items']['properties']['executor']
    assert node['additionalProperties'] is False and node['required'] == ['kind']
    assert set(node['properties']) == {'kind', 'engine', 'cli', 'mode', 'isolation'}
    assert node['properties']['kind']['enum'] == ['internal', 'engine', 'cli']
    assert 'bypass' not in json.dumps(node['properties']['mode'])


def test_node_view_reports_the_executor_over_the_board_and_graphql_type(tmp_path):
    from termx.graphql.types import OrchestratorNode

    async def run():
        workspace, owner, manager, ledger, graph, fake = world(tmp_path)
        try:
            graph.plan(owner, [spec('a', executor=CLI, mode='agent')])
            await graph.start_ready(owner)
            view = graph.board(owner)['nodes'][0]
            wrapped = OrchestratorNode.wrap(view)
            assert wrapped.executor == {'kind': 'cli', 'cli': 'claude', 'mode': 'default', 'isolation': 'worktree'} or wrapped.executor['kind'] == 'cli'
            assert wrapped.session_id == view['session_id'] and wrapped.blocked_reason is None and wrapped.resumable is False
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())
