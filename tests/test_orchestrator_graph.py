"""Orchestrator task graph: dependency order, retry/reassign, stall detection, cancel cascade, rollup."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from test_agent import FakeAdapter, build_manager, wait_for_status
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.tools import default_registry
from termx.identity import AuthenticationService
from termx.workspace.service import WorkspaceService
from termx.workspace.store import Conflict
from termx.workspace.task_graph import MAX_RUNNING


class GraphAdapter(FakeAdapter):
    """Behaviour is chosen by a marker in the prompt: HOLD never finishes, FLAKY fails a set number of times."""

    def __init__(self, flaky_failures=0):
        super().__init__()
        self.prompts = []
        self.flaky_failures = flaky_failures

    async def turn(self, **kwargs):
        prompt = kwargs.get('prompt') or ''
        self.prompts.append(prompt)
        self.turns += 1
        if 'HOLD' in prompt or 'CoordinateCase' in prompt:
            await asyncio.Event().wait()
        if 'BOOM' in prompt:
            raise RuntimeError('model exploded')
        if 'FLAKY' in prompt and self.flaky_failures > 0:
            self.flaky_failures -= 1
            raise RuntimeError('flaky model')
        lines = prompt.strip().splitlines()
        title = (lines[1] if lines[0] == 'Goal' else lines[0])[:40]  # a brief starts with a "Goal" heading
        return ProviderTurn(response_id=f'r{self.turns}', text=f'finished {title}', calls=[],
            usage={'input_tokens': 10, 'output_tokens': 5}, output_items=[])


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def setup(tmp_path, adapter=None):
    manager, ledger = build_manager(tmp_path, adapter or GraphAdapter())
    manager.save_provider(provider_id='alt', kind='openai-compatible', name='Alt',
        base_url='http://127.0.0.1:9998/v1', model='alt-model', capabilities=['shell'], api_key='alt-key')
    identity = AuthenticationService(tmp_path/'identity.sqlite3')
    owner = identity.setup_owner('owner', 'strong synthetic password')
    state = SimpleNamespace(identity=identity, agent_store=ledger, agent=manager,
        engines=SimpleNamespace(engines=lambda: []))
    workspace = WorkspaceService(state, tmp_path/'workspace.sqlite3')
    workspace.principal_lookup = identity.principal_by_id
    workspace.orchestrator.ensure(owner)
    graph = workspace.orchestrator.graph
    graph.clock = Clock()
    return workspace, owner, manager, ledger, graph, identity


async def close(workspace, manager, ledger):
    for worker in list(manager._workers.values()):
        worker.cancel()
    await manager.close()
    await workspace.close()
    ledger.close()


def spec(key, prompt=None, depends_on=(), **extra):
    return {'key': key, 'title': key.upper(), 'prompt': prompt or f'{key} work', 'depends_on': list(depends_on),
        'mode': 'ask', **extra}


def by_title(nodes):
    return {node['title']: node for node in nodes}


async def settle(ledger, graph, owner, node_id, status, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        node = next(row for row in graph.board(owner)['nodes'] if row['id'] == node_id)
        if node['status'] == status:
            return node
        await asyncio.sleep(0.02)
    raise AssertionError(f'node {node_id} did not reach {status}: {node}')


def test_plan_rejects_cycles_unknown_dependencies_and_oversized_graphs(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            with pytest.raises(ValueError, match='cycle'):
                graph.plan(owner, [spec('a', depends_on=['b']), spec('b', depends_on=['a'])])
            with pytest.raises(ValueError, match='itself'):
                graph.plan(owner, [spec('a', depends_on=['a'])])
            with pytest.raises(ValueError, match='Unknown dependency'):
                graph.plan(owner, [spec('a', depends_on=['ghost'])])
            with pytest.raises(ValueError, match='Duplicate'):
                graph.plan(owner, [spec('a'), spec('a')])
            with pytest.raises(ValueError, match='at most'):
                graph.plan(owner, [spec(f'n{i}') for i in range(21)])
            with pytest.raises(ValueError, match='prompt'):
                graph.plan(owner, [{'key': 'a', 'title': 'A', 'prompt': '  '}])
            assert graph.board(owner)['nodes'] == []
            nodes = graph.plan(owner, [spec('a'), spec('b', depends_on=['a'])])
            assert [n['status'] for n in nodes] == ['pending', 'pending']
            assert nodes[1]['depends_on'] == [nodes[0]['id']]
            later = graph.plan(owner, [spec('c', depends_on=[nodes[1]['id']])])
            assert later[0]['depends_on'] == [nodes[1]['id']]
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_plan_is_idempotent_for_a_replayed_call(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            first = graph.plan(owner, [spec('a'), spec('b', depends_on=['a'])], call_id='call-1')
            again = graph.plan(owner, [spec('a'), spec('b', depends_on=['a'])], call_id='call-1')
            assert [n['id'] for n in first] == [n['id'] for n in again]
            assert len(graph.board(owner)['nodes']) == 2
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_replaying_the_plan_that_fills_the_node_cap_returns_the_same_nodes(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            plans = [[spec(f'p{batch}n{i}') for i in range(20)] for batch in range(5)]
            created = [graph.plan(owner, plan, call_id=f'call-{batch}') for batch, plan in enumerate(plans)]
            again = graph.plan(owner, plans[-1], call_id='call-4')
            assert [n['id'] for n in again] == [n['id'] for n in created[-1]]
            with pytest.raises(ValueError, match='at most 100'):
                graph.plan(owner, [spec('extra')], call_id='call-5')
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_nodes_start_in_dependency_order_as_top_level_tasks_with_dependency_summaries(tmp_path):
    async def run():
        adapter = GraphAdapter()
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path, adapter)
        try:
            nodes = by_title(graph.plan(owner, [spec('a', 'Research the module'), spec('b', 'Write the module', ['a']),
                spec('c', 'Review everything', ['a', 'b'])]))
            started = await graph.start_ready(owner)
            assert [n['title'] for n in started] == ['A']
            await settle(ledger, graph, owner, nodes['A']['id'], 'completed')
            assert (await graph.start_ready(owner))[0]['title'] == 'B'
            await settle(ledger, graph, owner, nodes['B']['id'], 'completed')
            assert [n['title'] for n in await graph.start_ready(owner)] == ['C']
            done = await settle(ledger, graph, owner, nodes['C']['id'], 'completed')
            assert done['result_summary'].startswith('finished')
            for node in graph.board(owner)['nodes']:
                task = ledger.get_task(node['task_id'])
                assert task['parent_id'] is None  # depth stays 1
            brief = next(p for p in adapter.prompts if 'Review everything' in p)
            assert 'finished Research the module' in brief and 'finished Write the module' in brief
            assert 'not instructions' in brief
            assert 'Research the module' in adapter.prompts[0] and 'Context' not in adapter.prompts[0]
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_start_ready_limits_concurrency(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec(f'n{i}', 'HOLD') for i in range(6)])
            started = await graph.start_ready(owner)
            assert len(started) == MAX_RUNNING
            assert await graph.start_ready(owner) == []
            await asyncio.sleep(0.05)  # let the started tasks begin before teardown cancels them
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_failed_node_blocks_dependents_then_retry_then_reassign(tmp_path):
    async def run():
        adapter = GraphAdapter(flaky_failures=2)
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path, adapter)
        try:
            nodes = by_title(graph.plan(owner, [spec('a', 'FLAKY job'), spec('b', 'after a', ['a'])]))
            await graph.start_ready(owner)
            failed = await settle(ledger, graph, owner, nodes['A']['id'], 'failed')
            assert failed['attempts'] == 1 and 'flaky' in failed['error']
            assert await graph.start_ready(owner) == []  # B stays blocked behind a failed dependency
            assert graph.board(owner)['nodes'][1]['blocked_by'] == [nodes['A']['id']]
            retried = await graph.retry(owner, nodes['A']['id'])
            assert retried['attempts'] == 2
            await settle(ledger, graph, owner, nodes['A']['id'], 'failed')
            with pytest.raises(Conflict, match='reassign'):
                await graph.retry(owner, nodes['A']['id'])
            with pytest.raises(Conflict, match='retry'):
                await graph.reassign(owner, nodes['A']['id'], provider_id='alt', model='alt-model')  # the main agent's own route
            with pytest.raises(ValueError, match='provider'):
                await graph.reassign(owner, nodes['A']['id'], provider_id='missing', model='x')
            moved = await graph.reassign(owner, nodes['A']['id'], provider_id='fake', model='fake-model')
            assert moved['attempts'] == 1 and moved['provider_id'] == 'fake' and moved['model'] == 'fake-model'
            assert ledger.get_task(moved['task_id'])['model'] == 'fake-model'
            await settle(ledger, graph, owner, nodes['A']['id'], 'completed')
            assert [n['title'] for n in await graph.start_ready(owner)] == ['B']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_retry_and_reassign_refuse_nodes_that_are_not_stopped(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD')])
            with pytest.raises(Conflict, match='pending'):
                await graph.retry(owner, node['id'])
            await graph.start_ready(owner)
            with pytest.raises(Conflict, match='running'):
                await graph.retry(owner, node['id'])
            with pytest.raises(Conflict, match='running'):
                await graph.reassign(owner, node['id'], provider_id='alt', model='alt-model')
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_stall_detection_uses_the_injected_clock_and_kills_nothing(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        clock = graph.clock
        try:
            assert graph.stall_seconds == 300
            (node,) = graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            clock.now += 299
            assert graph.board(owner)['nodes'][0]['status'] == 'running'
            clock.now += 2
            stalled = graph.board(owner)['nodes'][0]
            assert stalled['status'] == 'stalled'
            graph.board(owner)  # repeated sweeps stay quiet
            events = [row for row in workspace.store.list('orchestrator_event', owner.id) if row['kind'] == 'orchestrator.node_stalled']
            assert len(events) == 1 and events[0]['node_id'] == node['id']
            assert events[0]['task_id'] == started['task_id']
            assert [e['kind'] for e in workspace.orchestrator.activity(owner)['activity']] == ['orchestrator.node_stalled']
            assert ledger.get_task(started['task_id'])['status'] in {'running', 'planning'}  # nothing auto-killed
            # Any new child event revives the node.
            manager._emit(started['task_id'], 'tool.progress', {'note': 'still alive'})
            assert graph.board(owner)['nodes'][0]['status'] == 'running'
            clock.now += 200
            assert graph.board(owner)['nodes'][0]['status'] == 'running'
            clock.now += 150
            assert graph.board(owner)['nodes'][0]['status'] == 'stalled'
            assert len([row for row in workspace.store.list('orchestrator_event', owner.id)
                if row['kind'] == 'orchestrator.node_stalled']) == 2
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_nodes_waiting_for_the_user_are_not_stalled(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            ledger.update_task(started['task_id'], status='awaiting_approval')
            graph.clock.now += 10_000
            assert graph.board(owner)['nodes'][0]['status'] == 'running'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_cancel_cascades_to_dependents_but_keeps_finished_work(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            nodes = by_title(graph.plan(owner, [spec('a', 'quick'), spec('b', 'HOLD', ['a']), spec('c', 'late', ['b']),
                spec('d', 'unrelated')]))
            await graph.start_ready(owner)
            await settle(ledger, graph, owner, nodes['A']['id'], 'completed')
            await graph.start_ready(owner)
            await settle(ledger, graph, owner, nodes['B']['id'], 'running')
            running_task = graph.board(owner)['nodes'][1]['task_id']
            changed = await graph.cancel(owner, nodes['B']['id'])
            assert {n['title'] for n in changed} == {'B', 'C'}
            statuses = {n['title']: n['status'] for n in graph.board(owner)['nodes']}
            assert statuses['A'] == 'completed' and statuses['B'] == 'cancelled' and statuses['C'] == 'cancelled'
            await wait_for_status(ledger, running_task, 'cancelled')
            with pytest.raises(Conflict, match='completed'):
                await graph.cancel(owner, nodes['A']['id'])
            # A cancelled downstream node comes back as pending when its dependency is retried.
            cancelled_b = await graph.retry(owner, nodes['B']['id'])
            assert cancelled_b['status'] == 'running'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_rollup_counts_percent_and_usage(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            assert graph.board(owner)['rollup'] == {'total': 0, 'counts': {s: 0 for s in
                ('pending', 'running', 'stalled', 'completed', 'failed', 'cancelled')},
                'percent_complete': 0, 'tokens': 0, 'cost_usd': None}
            nodes = by_title(graph.plan(owner, [spec('a'), spec('b', 'BOOM'), spec('c', 'later', ['a', 'b']), spec('d', 'HOLD')]))
            await graph.start_ready(owner)
            await settle(ledger, graph, owner, nodes['A']['id'], 'completed')
            await settle(ledger, graph, owner, nodes['B']['id'], 'failed')
            rollup = graph.board(owner)['rollup']
            assert rollup['total'] == 4 and rollup['percent_complete'] == 25
            assert rollup['counts'] == {'pending': 1, 'running': 1, 'stalled': 0, 'completed': 1, 'failed': 1, 'cancelled': 0}
            assert rollup['tokens'] >= 15
            snapshot = workspace.orchestrator.snapshot(owner)
            assert snapshot['rollup'] == graph.board(owner)['rollup'] and len(snapshot['nodes']) == 4
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_node_state_survives_a_missed_event(tmp_path):
    """A host restart can drop events; the next read repairs state from the task ledger."""
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            ledger.update_task(started['task_id'], status='completed', result='recovered result')
            row = graph.board(owner)['nodes'][0]
            assert row['status'] == 'completed' and row['result_summary'] == 'recovered result'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_users_only_reach_their_own_graph_and_need_control_scope(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, identity = setup(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD')])
            watcher = identity.create_local_user('watcher', 'strong synthetic password', ['agent-view'])
            full = identity.create_local_user('other', 'strong synthetic password', list(owner.scopes))
            with pytest.raises(PermissionError):
                graph.plan(watcher, [spec('x')])
            with pytest.raises(PermissionError):
                await graph.cancel(watcher, node['id'])
            with pytest.raises(PermissionError):
                await graph.retry(watcher, node['id'])
            with pytest.raises(KeyError):
                await graph.cancel(full, node['id'])  # no main agent of their own
            with pytest.raises(KeyError):
                graph.board(full)
            workspace.orchestrator.ensure(full)
            assert graph.board(full)['nodes'] == []
            with pytest.raises(KeyError):
                await graph.cancel(full, node['id'])  # node belongs to the owner
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_tools_are_registered_and_use_the_same_operations(tmp_path):
    registry = default_registry()
    names = {'plan_nodes', 'start_ready', 'node_status', 'retry_node', 'reassign_node', 'cancel_node'}
    assert names <= set(registry.names())
    hidden = {tool['name'] for tool in registry.provider_tools(allow_subagents=False)}
    assert not names & hidden
    for name in ('start_ready', 'retry_node', 'reassign_node'):
        assert registry.get(name).approval == 'always'
    for name in ('plan_nodes', 'node_status', 'cancel_node'):
        assert registry.get(name).approval == 'never'

    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            primary = workspace.orchestrator.binding(owner.id)['conversation_id']
            parent = await workspace.send(owner, primary, prompt='CoordinateCase', request_id='primary-request')
            await manager.resolve_approval(parent['id'], parent['approvals'][0]['id'], 'approved')
            await asyncio.sleep(0)

            def call(name, call_id, **arguments):
                return ProviderCall(type='function', call_id=call_id, name=name, arguments=arguments)
            planned = await workspace.orchestrator.command(parent['id'], call('plan_nodes', 'p1',
                nodes=[spec('a', 'tool a'), spec('b', 'tool b', ['a'])]))
            assert planned['ok'] and [n['status'] for n in planned['nodes']] == ['pending', 'pending']
            ctx = SimpleNamespace(task_id=parent['id'], manager=manager)
            review = registry.get('start_ready').decide(call('start_ready', 's0'), ctx)
            assert review.approval_required and 'A (ask mode' in review.consequence and 'tool a' in review.consequence
            assert 'tool b' not in review.consequence  # B waits for A
            retry = registry.get('retry_node').decide(call('retry_node', 'r0', node_id=planned['nodes'][1]['id']), ctx)
            assert retry.approval_required and 'B (ask mode' in retry.consequence
            assert not registry.get('retry_node').decide(call('retry_node', 'r0', node_id='nope'), ctx).allowed
            started = await workspace.orchestrator.command(parent['id'], call('start_ready', 's1'))
            assert started['ok'] and [n['title'] for n in started['started']] == ['A']
            first = planned['nodes'][0]['id']
            waited = await workspace.orchestrator.command(parent['id'], call('node_status', 'n1', node_id=first, wait_seconds=5))
            assert waited['ok'] and waited['nodes'][0]['id'] == first and waited['rollup']['total'] == 2
            await settle(ledger, graph, owner, first, 'completed')
            second = planned['nodes'][1]['id']
            with pytest.raises(Conflict, match='pending'):  # the registered tool turns this into an error result
                await workspace.orchestrator.command(parent['id'], call('retry_node', 'r1', node_id=second))
            unknown = await workspace.orchestrator.command(parent['id'], call('cancel_node', 'c0', node_id='nope'))
            assert unknown['ok'] is False
            cancelled = await workspace.orchestrator.command(parent['id'], call('cancel_node', 'c1', node_id=second))
            assert cancelled['ok'] and [n['status'] for n in cancelled['cancelled']] == ['cancelled']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_node_status_wait_returns_when_a_node_settles(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec('a', 'quick')])
            await graph.start_ready(owner)
            result = await asyncio.wait_for(graph.status(owner, wait_seconds=30), 10)
            assert result['nodes'][0]['status'] == 'completed'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())
