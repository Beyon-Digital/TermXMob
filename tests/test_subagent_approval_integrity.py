import asyncio

import pytest


def test_stale_child_tool_approval_preserves_parent_request_for_denial(tmp_path):
    from test_agent import FakeAdapter, build_manager

    async def run():
        manager, store = build_manager(tmp_path, FakeAdapter())
        try:
            parent = await manager.create_task(prompt='Parent', cwd=str(tmp_path), provider_id='fake')
            child = await manager.create_task(prompt='Child', cwd=str(tmp_path), provider_id='fake', parent_id=parent['id'])
            inner = store.create_approval(child['id'], 'tool', {'title': 'Write file'})
            manager._escalate_child_approval(parent['id'], child['id'], 'Writer', inner)
            outer = next(a for a in store.approvals(parent['id']) if a['payload'].get('child_approval_id') == inner['id'])
            # The actual executable child call disappeared (e.g. recovery).
            with pytest.raises(ValueError, match='no longer available'):
                await manager.resolve_approval(parent['id'], outer['id'], 'approved')
            assert store.get_approval(outer['id'])['status'] == 'pending'
            assert store.get_approval(inner['id'])['status'] == 'pending'
            assert outer['id'] in manager._pending_approval_calls
            await manager.resolve_approval(parent['id'], outer['id'], 'denied')
            assert store.get_approval(outer['id'])['status'] == 'denied'
            assert store.get_approval(inner['id'])['status'] == 'denied'
            assert store.get_task(child['id'])['status'] == 'cancelled'
        finally:
            await manager.close()
            store.close()

    asyncio.run(run())


def test_parent_tool_wrapper_rejects_changed_child_binding(tmp_path):
    from test_agent import FakeAdapter, build_manager

    async def run():
        manager, store = build_manager(tmp_path, FakeAdapter())
        try:
            parent = await manager.create_task(prompt='Parent', cwd=str(tmp_path), provider_id='fake')
            unrelated = await manager.create_task(prompt='Other parent', cwd=str(tmp_path), provider_id='fake')
            inner = store.create_approval(unrelated['id'], 'tool', {'title': 'Write file'})
            manager._escalate_child_approval(parent['id'], unrelated['id'], 'Writer', inner)
            outer = next(a for a in store.approvals(parent['id']) if a['payload'].get('child_approval_id') == inner['id'])
            with pytest.raises(ValueError, match='no longer belongs'):
                await manager.resolve_approval(parent['id'], outer['id'], 'denied')
            assert store.get_approval(outer['id'])['status'] == 'pending'
            assert store.get_approval(inner['id'])['status'] == 'pending'
        finally:
            await manager.close()
            store.close()

    asyncio.run(run())
