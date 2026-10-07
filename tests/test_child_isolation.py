import asyncio
from pathlib import Path
import pytest
from termx.agent.child_isolation import snapshot

def test_non_git_snapshot_links_and_capacity_fail_closed(tmp_path):
    source=tmp_path/'project';source.mkdir();(source/'linked').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError):snapshot(source,tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()
    (source/'linked').unlink();(source/'large').write_bytes(b'12345')
    with pytest.raises(ValueError):snapshot(source,tmp_path/'snapshot',max_bytes=4)

@pytest.mark.parametrize('git',[False,True])
def test_parallel_children_write_isolated_same_filename_without_parent_change(tmp_path,monkeypatch,git):
    from test_agent import _FanOutAdapter,_fn,build_manager,_approve_pending,wait_for_status,_git_repo
    project=tmp_path/'project';project.mkdir();(project/'parent.txt').write_text('parent state')
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    if git:_git_repo(project)
    async def run():
        adapter=_FanOutAdapter({'Coordinate':([[_fn('s1','spawn_subagent',task='child-a'),_fn('s2','spawn_subagent',task='child-b')],[_fn('wait','await_subagents')]],0.),
            'child-a':([[_fn('write-a','write_file',path='same.txt',content='from A')]],0.),
            'child-b':([[_fn('write-b','write_file',path='same.txt',content='from B')]],0.)})
        manager,store=build_manager(tmp_path,adapter)
        try:
            parent=await manager.create_task(prompt='Coordinate',cwd=str(project),provider_id='fake')
            await manager.resolve_approval(parent['id'],parent['approvals'][0]['id'],'approved')
            await _approve_pending(manager,store,parent['id']);await _approve_pending(manager,store,parent['id'])
            await wait_for_status(store,parent['id'],'completed')
            children=[t for t in store.list_tasks() if t.get('parent_id')==parent['id']]
            assert len(children)==2 and len({t['cwd'] for t in children})==2
            assert all(Path(t['cwd'])!=project and t['status']=='completed' for t in children)
            assert {Path(t['cwd'],'same.txt').read_text() for t in children}=={'from A','from B'}
            assert not (project/'same.txt').exists() and (project/'parent.txt').read_text()=='parent state'
            for child in children:
                isolation=next(e['payload'] for e in store.events(child['id']) if e['type']=='task.isolation')
                assert isolation['kind']==('worktree' if git else 'snapshot') and isolation['automatic_apply'] is False
                if git:assert store.task_worktree(child['id'])['worktree_path']==child['cwd']
        finally:await manager.close();store.close()
    asyncio.run(run())
