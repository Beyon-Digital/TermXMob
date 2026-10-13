"""Regression coverage for desktop team admission and durable dispatch."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_agent import FakeAdapter, build_manager, wait_for_status
from termx.agent.providers import ProviderCall
from termx.workspace.store import Conflict
from termx.workspace.swarms import assemble


class WaitingAdapter(FakeAdapter):
    async def turn(self, **kwargs):
        await asyncio.Event().wait()


async def setup_team(tmp_path, **limits):
    manager, store = build_manager(tmp_path, WaitingAdapter())
    task = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake', limits=limits)
    await manager.resolve_approval(task['id'], task['approvals'][0]['id'], 'approved')
    await asyncio.sleep(0)
    workspace = SimpleNamespace(state=SimpleNamespace(agent=manager), agents=store,
                                record=Mock(return_value={}), store=SimpleNamespace(log=Mock()))
    return workspace, manager, store, task


def members(count=2):
    return [{'agent': f'Worker {i}', 'task': f'Inspect area {i}', 'mode': 'ask'} for i in range(count)]


def test_team_real_children_and_idempotent_retry(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='once', members=members())
            assert result['ok']
            ids = [row['task_id'] for row in result['members']]
            assert len(set(ids)) == 2
            assert all(store.get_task(child)['parent_id'] == task['id'] for child in ids)
            assert await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='once', members=members()) == result
            assert len(manager._subagent_handles(task['id'])) == 2
            with pytest.raises(Conflict, match='changed'):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='once', members=members(1))
            assert {call.kwargs['scope'] for call in workspace.record.call_args_list} == {'agent-run', 'agent-control'}
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize('limits', [{'max_parallel_subagents': 1}, {'max_subagents_total': 1}])
def test_team_rejects_whole_request_before_dispatch(tmp_path, limits):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path, **limits)
        try:
            with pytest.raises(Conflict, match='allowance'):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='large', members=members())
            assert not manager._subagent_handles(task['id'])
            assert not any(e['type'] == 'swarm.requested' for e in store.events(task['id']))
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', [{'status': 'completed'}, {'mode': 'ask'}, {'parent_id': 'parent'}, {'engine': 'codex'}])
def test_team_rejects_ineligible_coordinator(tmp_path, change):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        try:
            if 'parent_id' in change:
                store._db.execute('UPDATE tasks SET parent_id=? WHERE id=?', (change['parent_id'], task['id']))
                store._db.commit()
            else:
                store.update_task(task['id'], **change)
            with pytest.raises(ValueError):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='invalid', members=members(1))
            assert not manager._subagent_handles(task['id'])
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_team_runner_and_missing_runtime_rejected(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        try:
            workspace.record.return_value = {'runner_id': 'remote'}
            with pytest.raises(ValueError, match='local'):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='remote', members=members(1))
            workspace.record.return_value = {}
            manager._cancel[task['id']].set()
            with pytest.raises(Conflict, match='recover'):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='stopped', members=members(1))
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_partial_dispatch_is_durable_and_never_replayed(tmp_path, monkeypatch):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        original = manager._spawn_subagent_locked
        calls = 0
        async def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('isolation unavailable')
            return await original(*args)
        monkeypatch.setattr(manager, '_spawn_subagent_locked', fail_second)
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='partial', members=members(3))
            assert [r['ok'] for r in result['members']] == [True, False, False]
            assert 'isolation unavailable' in result['members'][1]['error']
            assert 'Not started' in result['members'][2]['error']
            assert await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='partial', members=members(3)) == result
            assert calls == 2
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_interrupted_dispatch_cannot_replay(tmp_path, monkeypatch):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        async def interrupted(*args):
            raise asyncio.CancelledError
        monkeypatch.setattr(manager, '_spawn_subagent_locked', interrupted)
        try:
            with pytest.raises(asyncio.CancelledError):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='interrupted', members=members(1))
            with pytest.raises(Conflict, match='interrupted'):
                await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='interrupted', members=members(1))
            assert sum(e['type'] == 'swarm.requested' for e in store.events(task['id'])) == 1
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_concurrent_provider_spawns_obey_parallel_cap(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path, max_parallel_subagents=1)
        try:
            results = await asyncio.gather(*(manager._spawn_subagent(task['id'], task,
                ProviderCall(type='function', call_id=f'call-{i}', name='spawn_subagent', arguments=member),
                manager._cancel[task['id']]) for i, member in enumerate(members())))
            assert sum(r['ok'] for r in results) == 1
            assert len(manager._subagent_handles(task['id'])) == 1
            assert 'parallel limit' in next(r['error'] for r in results if not r['ok'])
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_concurrent_team_and_provider_share_admission_lock(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path, max_parallel_subagents=1)
        try:
            outcomes = await asyncio.gather(
                assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='team', members=members(1)),
                manager._spawn_subagent(task['id'], task,
                    ProviderCall(type='function', call_id='provider', name='spawn_subagent', arguments=members(1)[0]),
                    manager._cancel[task['id']]), return_exceptions=True)
            assert len(manager._subagent_handles(task['id'])) == 1
            assert sum(isinstance(outcome, dict) and outcome.get('ok', False) for outcome in outcomes) == 1
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_revoked_ownership_stops_remaining_workers(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        checks = 0
        def authority(*args, **kwargs):
            nonlocal checks
            if kwargs['scope'] == 'agent-run':
                checks += 1
                if checks == 3:
                    raise PermissionError('access revoked')
            return {}
        workspace.record.side_effect = authority
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='revoked', members=members(3))
            assert [r['ok'] for r in result['members']] == [True, False, False]
            assert result['members'][1]['error'] == 'access revoked'
            assert len(manager._subagent_handles(task['id'])) == 1
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_waiting_plan_coordinator_accepts_team_without_live_worker(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, WaitingAdapter())
        task = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
        workspace = SimpleNamespace(state=SimpleNamespace(agent=manager), agents=store,
                                    record=Mock(return_value={}), store=SimpleNamespace(log=Mock()))
        assert task['status'] == 'awaiting_approval'
        assert task['id'] not in manager._cancel
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='waiting', members=members(1))
            assert result['ok']
            child = result['members'][0]['task_id']
            delegation = next(e['payload'] for e in store.events(child) if e['type'] == 'task.delegation')
            assert delegation == {'agent': 'Worker 0', 'task': 'Inspect area 0', 'call_id': 'swarm:waiting:0'}
            assert any(e['type'] == 'task.isolation' for e in store.events(child))
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_cancelling_worker_preserves_coordinator_and_siblings(tmp_path):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='isolation', members=members())
            first, second = [r['task_id'] for r in result['members']]
            manager.cancel(first)
            assert not manager._cancel[task['id']].is_set()
            assert not manager._cancel[second].is_set()
            assert store.get_task(task['id'])['status'] == 'running'
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_coordinator_finishing_during_creation_cancels_registered_child(tmp_path, monkeypatch):
    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        original = manager.create_task
        async def finish_parent(**kwargs):
            child = await original(**kwargs)
            store.update_task(task['id'], status='completed')
            return child
        monkeypatch.setattr(manager, 'create_task', finish_parent)
        try:
            result = await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='race', members=members(2))
            assert not result['ok']
            child = result['members'][0]['task_id']
            await wait_for_status(store, child, 'cancelled', timeout=2)
            await asyncio.wait_for(manager._subagent_handles(task['id'])[child].done.wait(), timeout=2)
            assert 'Not started' in result['members'][1]['error']
            assert await assemble(workspace, SimpleNamespace(id='owner'), task['id'], request_id='race', members=members(2)) == result
        finally:
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_planning_child_is_enrolled_and_can_be_cancelled_before_dispatch_returns(tmp_path):
    class SlowPlanningAdapter(WaitingAdapter):
        def __init__(self):
            super().__init__()
            self.planning = asyncio.Event()
            self.release = asyncio.Event()
        async def plan(self, prompt, cwd, manifest):
            if prompt != 'Coordinate':
                self.planning.set()
                await self.release.wait()
            return await super().plan(prompt, cwd, manifest)

    async def run():
        adapter = SlowPlanningAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
        await manager.resolve_approval(task['id'], task['approvals'][0]['id'], 'approved')
        await asyncio.sleep(0)
        workspace = SimpleNamespace(state=SimpleNamespace(agent=manager), agents=store,
                                    record=Mock(return_value={}), store=SimpleNamespace(log=Mock()))
        enrolled = []
        def on_event(task_id, event):
            if task_id == task['id'] and event['type'] == 'subagent.started':
                child_id = event['payload']['child_id']
                assert child_id in manager._subagent_handles(task['id'])
                enrolled.append(child_id)
        manager._listeners.add(on_event)
        dispatch = asyncio.create_task(assemble(workspace, SimpleNamespace(id='owner'), task['id'],
            request_id='planning', members=[{'agent': 'Planner', 'task': 'Inspect slowly', 'mode': 'agent'}]))
        try:
            await asyncio.wait_for(adapter.planning.wait(), timeout=3)
            assert not dispatch.done()
            assert len(enrolled) == 1
            child = enrolled[0]
            assert store.get_task(child)['status'] == 'planning'
            assert any(e['type'] == 'task.isolation' for e in store.events(child))
            manager.cancel(child)
            result = await asyncio.wait_for(dispatch, timeout=3)
            assert not result['ok']
            await wait_for_status(store, child, 'cancelled', timeout=2)
            assert not manager._cancel[task['id']].is_set()
            adapter.release.set()
        finally:
            adapter.release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())


def test_cancellation_during_snapshot_preparation_removes_unused_workspace(tmp_path, monkeypatch):
    import threading
    from termx.agent import child_isolation, worktrees

    started = threading.Event()
    release = threading.Event()
    destinations = []
    def slow_snapshot(source, destination, **kwargs):
        destination.mkdir(parents=True)
        (destination / 'partial.txt').write_text('partial copy')
        destinations.append(destination)
        started.set()
        assert release.wait(timeout=5)
        return {'kind': 'snapshot', 'path': str(destination)}
    monkeypatch.setattr(child_isolation, 'snapshot', slow_snapshot)
    monkeypatch.setattr(worktrees, 'git_root', lambda cwd: None)

    async def run():
        workspace, manager, store, task = await setup_team(tmp_path)
        dispatch = asyncio.create_task(assemble(workspace, SimpleNamespace(id='owner'), task['id'],
            request_id='snapshot', members=[{'agent': 'Writer', 'task': 'Inspect isolated files', 'mode': 'agent'}]))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            manager.cancel(task['id'])
            release.set()
            result = await asyncio.wait_for(dispatch, timeout=3)
            assert not result['ok']
            assert not manager._subagent_handles(task['id'])
            assert len(destinations) == 1 and not destinations[0].exists()
            assert not any(t.get('parent_id') == task['id'] for t in store.list_tasks())
        finally:
            release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
            await asyncio.sleep(0)
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            store.close()
    asyncio.run(run())
