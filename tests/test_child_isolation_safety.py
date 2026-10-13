import asyncio
import os

import pytest

from termx.agent.child_isolation import snapshot


def test_snapshot_does_not_remove_existing_destination(tmp_path):
    source = tmp_path / 'project'
    source.mkdir()
    destination = tmp_path / 'snapshot'
    destination.mkdir()
    kept = destination / 'valuable.txt'
    kept.write_text('Keep this')
    with pytest.raises(FileExistsError):
        snapshot(source, destination)
    assert kept.read_text() == 'Keep this'


@pytest.mark.skipif(not hasattr(os, 'O_NOFOLLOW'), reason='Platform lacks no-follow open')
def test_snapshot_rejects_file_swapped_to_external_symlink(tmp_path, monkeypatch):
    source = tmp_path / 'project'
    source.mkdir()
    item = source / 'document'
    item.write_text('Public project data')
    outside = tmp_path / 'private'
    outside.write_text('Private data')
    original = os.open

    def swapped(path, flags, *args, **kwargs):
        if path == item:
            item.unlink()
            item.symlink_to(outside)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', swapped)
    destination = tmp_path / 'snapshot'
    with pytest.raises(OSError):
        snapshot(source, destination)
    assert not destination.exists()


def test_snapshot_counts_empty_files_toward_limit(tmp_path):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'empty').touch()
    destination = tmp_path / 'snapshot'
    with pytest.raises(ValueError):
        snapshot(source, destination, max_files=0)
    assert not destination.exists()


def test_cancel_child_during_planning_does_not_cancel_coordinator(tmp_path):
    from test_agent import FakeAdapter, build_manager
    from termx.agent.providers import ProviderCall

    async def run():
        planning = asyncio.Event()

        class WaitingAdapter(FakeAdapter):
            async def plan(self, prompt, cwd, manifest):
                if 'Child planning' in prompt:
                    planning.set()
                    await asyncio.Event().wait()
                return await super().plan(prompt, cwd, manifest)

        project = tmp_path / 'project'
        project.mkdir()
        manager, store = build_manager(tmp_path, WaitingAdapter())
        dispatch = None
        try:
            parent = await manager.create_task(prompt='Coordinator', cwd=str(project), provider_id='fake')
            parent_cancel = asyncio.Event()
            manager._cancel[parent['id']] = parent_cancel
            call = ProviderCall(type='function', call_id='spawn', name='spawn_subagent',
                                arguments={'agent': 'Worker', 'task': 'Child planning'})
            dispatch = asyncio.create_task(manager._spawn_subagent(parent['id'], parent, call, parent_cancel))
            await asyncio.wait_for(planning.wait(), 5)
            child = store.children(parent['id'])[0]
            manager.cancel(child['id'])
            await asyncio.wait_for(dispatch, 5)
            assert store.get_task(child['id'])['status'] == 'cancelled'
            assert not parent_cancel.is_set()
        finally:
            if dispatch and not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
            await manager.close()
            store.close()

    asyncio.run(run())
