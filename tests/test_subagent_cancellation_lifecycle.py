"""Cancellation stays bounded and preserves independent approval boundaries."""
import asyncio

from test_agent import FakeAdapter, _FanOutAdapter, _fn, build_manager
from termx.agent.providers import ProviderCall


class WaitingAdapter(FakeAdapter):
    async def turn(self, **kwargs):
        await asyncio.Event().wait()


async def spawn(manager, parent, name, cancel):
    outcome = await manager._spawn_subagent(parent['id'], parent,
        ProviderCall(type='function', call_id='spawn-' + name, name='spawn_subagent',
                     arguments={'agent': name, 'task': name}), cancel)
    assert outcome['ok'], outcome
    return outcome['task_id']


async def wait_condition(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), 2)


def test_repeated_cancel_emits_one_cancelling_transition(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, WaitingAdapter())
        try:
            parent = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
            child_id = await spawn(manager, parent, 'Worker', asyncio.Event())
            await wait_condition(lambda: store.get_task(child_id)['status'] == 'running')
            manager.cancel(child_id)
            manager.cancel(child_id)
            manager.cancel(child_id)
            await wait_condition(lambda: store.get_task(child_id)['status'] == 'cancelled')
            transitions = [e for e in store.events(child_id) if e['type'] == 'task.status'
                           and e['payload'].get('status') == 'cancelling']
            assert len(transitions) == 1
            await asyncio.wait_for(manager._subagents[parent['id']][child_id].done.wait(), 2)
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_cancelled_child_settles_wrapper_preserves_parent_plan(tmp_path):
    async def run():
        adapter = _FanOutAdapter({'Writer': ([[_fn('write', 'run_shell', command='git push origin main')]], 0)})
        manager, store = build_manager(tmp_path, adapter)
        try:
            parent = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
            plan_id = parent['approvals'][0]['id']
            child_id = await spawn(manager, parent, 'Writer', asyncio.Event())
            wrappers = lambda: [a for a in store.approvals(parent['id'])
                               if a['payload'].get('child_id') == child_id]
            try:
                await wait_condition(lambda: bool(wrappers()))
            except TimeoutError:
                raise AssertionError(store.get_task(child_id, include_events=True))
            wrapper_id = wrappers()[0]['id']
            assert store.get_approval(wrapper_id)['status'] == 'pending'
            manager.cancel(child_id)
            await asyncio.wait_for(manager._subagents[parent['id']][child_id].done.wait(), 2)
            assert store.get_approval(wrapper_id)['status'] == 'denied'
            assert wrapper_id not in manager._pending_approval_calls
            assert store.get_approval(plan_id)['status'] == 'pending'
            assert store.get_task(parent['id'])['status'] == 'awaiting_approval'
            assert not any(e['type'] == 'task.status' and e['payload'].get('status') == 'running'
                           for e in store.events(parent['id']))
            # A finished child must not make the original plan unresolvable.
            await manager.resolve_approval(parent['id'], plan_id, 'denied')
            assert store.get_task(parent['id'])['status'] == 'cancelled'
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_cancel_paused_coordinator_settles_active_isolated_children(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, WaitingAdapter())
        try:
            parent = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
            children = [await spawn(manager, parent, name, asyncio.Event()) for name in ('One', 'Two')]
            await wait_condition(lambda: all(store.get_task(child)['status'] == 'running' for child in children))
            assert parent['id'] not in manager._workers
            assert parent['id'] not in manager._cancel
            manager.cancel(parent['id'])
            await asyncio.wait_for(asyncio.gather(
                *(manager._subagents[parent['id']][child].done.wait() for child in children)), 2)
            assert store.get_task(parent['id'])['status'] == 'cancelled'
            assert all(store.get_task(child)['status'] == 'cancelled' for child in children)
            assert all(store.get_task(child)['cwd'] != parent['cwd'] for child in children)
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_completed_child_keeps_coordinator_plan_awaiting_approval(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, _FanOutAdapter({'Done': ([], 0)}))
        try:
            parent = await manager.create_task(prompt='Coordinate', cwd=str(tmp_path), provider_id='fake')
            plan_id = parent['approvals'][0]['id']
            child_id = await spawn(manager, parent, 'Done', asyncio.Event())
            await asyncio.wait_for(manager._subagents[parent['id']][child_id].done.wait(), 2)
            assert store.get_task(child_id)['status'] == 'completed'
            assert store.get_task(parent['id'])['status'] == 'awaiting_approval'
            assert store.get_approval(plan_id)['status'] == 'pending'
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())
