"""Task graph: waiting visibility, restart recovery and per-role routing for node tasks."""
from __future__ import annotations

import asyncio

from test_orchestrator_graph import Clock, close, setup, spec
from termx.workspace.task_graph import TaskGraph


def _events(workspace, owner, kind):
    return [row for row in workspace.store.list('orchestrator_event', owner.id) if row['kind'] == kind]


def test_node_waiting_on_the_user_says_so_once_and_clears(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            assert graph.board(owner)['nodes'][0]['waiting'] is None
            ledger.update_task(started['task_id'], status='awaiting_approval')
            graph.clock.now += 10_000
            view = graph.board(owner)['nodes'][0]
            assert view['status'] == 'running' and view['waiting'] == 'approval'
            graph.board(owner)  # repeated sweeps stay quiet
            events = _events(workspace, owner, 'orchestrator.node_waiting')
            assert len(events) == 1 and events[0]['node_id'] == node['id']
            ledger.update_task(started['task_id'], status='running')
            manager._emit(started['task_id'], 'tool.progress', {'note': 'resumed'})  # a resumed task emits again
            assert graph.board(owner)['nodes'][0]['waiting'] is None
            ledger.update_task(started['task_id'], status='recovery_confirmation_required')
            assert graph.board(owner)['nodes'][0]['waiting'] == 'recovery'
            assert len(_events(workspace, owner, 'orchestrator.node_waiting')) == 2
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_recovering_task_is_not_marked_stalled(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            ledger.update_task(started['task_id'], status='recovering')
            graph.clock.now += 10_000
            assert graph.board(owner)['nodes'][0]['status'] == 'running'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_resume_repairs_nodes_whose_task_finished_while_the_host_was_down(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            (node,) = graph.plan(owner, [spec('a', 'HOLD'), ][:1])
            (started,) = await graph.start_ready(owner)
            ledger.update_task(started['task_id'], status='completed', result='finished while down')
            restarted = TaskGraph(workspace.orchestrator)
            restarted.clock = Clock()
            restarted.resume()
            row = workspace.store.get('orchestrator_node', node['id'])
            assert row['status'] == 'completed' and row['result_summary'] == 'finished while down'
            assert len(_events(workspace, owner, 'orchestrator.node_completed')) == 1
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_resume_restarts_the_stall_clock_instead_of_stalling_at_once(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            restarted = TaskGraph(workspace.orchestrator)
            restarted.clock = Clock(graph.clock.now + 100_000)  # the host was down for a long time
            restarted.resume()
            assert workspace.store.get('orchestrator_node', started['id'])['status'] == 'running'
            assert not _events(workspace, owner, 'orchestrator.node_stalled')
            restarted.clock.now += 301
            restarted.sweep()
            assert workspace.store.get('orchestrator_node', started['id'])['status'] == 'stalled'
            await restarted.close()
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_node_tasks_use_the_worker_role_for_routing(tmp_path):
    async def run():
        workspace, owner, manager, ledger, graph, _ = setup(tmp_path)
        try:
            graph.plan(owner, [spec('a', 'HOLD')])
            (started,) = await graph.start_ready(owner)
            task = ledger.get_task(started['task_id'])
            assert manager.task_role(task) == 'worker'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())
