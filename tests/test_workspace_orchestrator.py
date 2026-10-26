"""Actual AgentManager orchestration with deterministic inference fixtures.

No provider network request or native engine process is involved. The runtime,
SQLite ledgers, isolation, tool approval and cancellation paths are real.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from test_agent import FakeAdapter, _FanOutAdapter, _fn, build_manager, wait_for_pending_approval, wait_for_status
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.tools import default_registry
from termx.identity import AuthenticationService, Principal
from termx.workspace.service import WorkspaceService
from termx.workspace.store import Conflict, WorkspaceStore


class HoldingAdapter(FakeAdapter):
    async def turn(self, **kwargs):
        await asyncio.Event().wait()


def setup(tmp_path, adapter=None):
    manager, ledger = build_manager(tmp_path, adapter or HoldingAdapter())
    identity = AuthenticationService(tmp_path/'identity.sqlite3')
    owner = identity.setup_owner('owner', 'strong synthetic password')
    state = SimpleNamespace(identity=identity,agent_store=ledger,agent=manager,
        engines=SimpleNamespace(engines=lambda:[]))
    workspace = WorkspaceService(state,tmp_path/'workspace.sqlite3')
    workspace.principal_lookup = identity.principal_by_id
    primary = workspace.orchestrator.ensure(owner)['session']
    return workspace,owner,manager,ledger,primary


async def close(workspace, manager, ledger):
    for worker in list(manager._workers.values()):
        worker.cancel()
    await manager.close()
    await workspace.close()
    ledger.close()


async def running_primary(workspace, owner, manager, primary):
    parent = await workspace.send(owner,primary['id'],prompt='CoordinateCase',request_id='primary-request')
    await manager.resolve_approval(parent['id'],parent['approvals'][0]['id'],'approved')
    await asyncio.sleep(0)
    return parent


def spawn_call(call_id='spawn-once', **changes):
    return ProviderCall(type='function',call_id=call_id,name='spawn_subagent',
        arguments={'task':'Inspect synthetic sample','agent':'Researcher','mode':'ask',**changes})


def test_primary_is_single_durable_and_never_calls_model(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                snapshots=list(executor.map(lambda _:workspace.orchestrator.ensure(owner),range(6)))
            assert {row['session']['id'] for row in snapshots}=={primary['id']}
            assert ledger.list_tasks()==[]
            assert manager._adapter_factory(None,None).turns==0
            restored=WorkspaceStore(workspace.store.path)
            assert restored.get('orchestrator',workspace.orchestrator._id(owner.id))['conversation_id']==primary['id']
            restored.close()
            assert primary['engine']=='internal' and primary['mode']=='agent' and primary['scratch']
            with pytest.raises(Conflict,match='persistent main agent'):
                workspace.update_session(owner,primary['id'],revision=primary['revision'],changes={'archived':True})
            with pytest.raises(Conflict,match='persistent main agent'):
                workspace.update_session(owner,primary['id'],revision=primary['revision'],changes={'engine':'codex'})
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_real_provider_tool_spawns_child_and_reports_completion(tmp_path):
    async def run():
        adapter=_FanOutAdapter({'CoordinateCase':([[_fn('child-call','spawn_subagent',
            agent='Researcher',task='ChildCase',mode='ask')],[_fn('collect','await_subagents',timeout_s=10)]],0),
            'ChildCase':([],0)})
        workspace,owner,manager,ledger,primary=setup(tmp_path,adapter)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            _,approval=await wait_for_pending_approval(ledger,parent['id'],'tool')
            assert not ledger.children(parent['id'])  # Delegation has not crossed its approval gate.
            await manager.resolve_approval(parent['id'],approval['id'],'approved')
            await wait_for_status(ledger,parent['id'],'completed')
            snapshot=workspace.orchestrator.snapshot(owner)
            assert len(snapshot['children'])==1
            child=snapshot['children'][0]
            assert child['status']=='completed' and child['result']=='ChildCase done.'
            assert child['parent_session_id']==primary['id'] and child['control_owner']=='orchestrator'
            turns=workspace.session(owner,child['session_id'])['turns']
            assert len(turns)==1 and turns[0]['task_id']==child['task_id']
            assert ledger.get_task(child['task_id'])['parent_id']==parent['id']
            assert any(event['kind']=='task.completed' and 'ChildCase done.' in event['message'] for event in snapshot['activity'])
            assert 'ChildCase done.' in workspace.orchestrator.context(owner,primary['id'])
            assert len(workspace.orchestrator.snapshot(owner)['activity'])==len(snapshot['activity'])
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_spawn_replay_does_not_duplicate_even_after_terminal_child(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            call=spawn_call()
            first=await manager._spawn_subagent(parent['id'],parent,call,manager._cancel[parent['id']])
            manager.cancel(first['task_id'])
            await wait_for_status(ledger,first['task_id'],'cancelled')
            second=await manager._spawn_subagent(parent['id'],parent,call,manager._cancel[parent['id']])
            assert second['task_id']==first['task_id'] and second['reused'] and second['status']=='cancelled'
            changed=await manager._spawn_subagent(parent['id'],parent,spawn_call(task='different action'),manager._cancel[parent['id']])
            assert not changed['ok'] and 'changed content' in changed['error']
            assert len(ledger.children(parent['id']))==1
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_control_transfer_scopes_and_command_receipts(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            child_id=child['session_id']
            summary=workspace.orchestrator.snapshot(owner)['children'][0]
            taken=workspace.orchestrator.control(owner,child_id,owner='user',revision=summary['revision'])
            with pytest.raises(Conflict,match='changed'):
                workspace.orchestrator.control(owner,child_id,owner='orchestrator',revision=summary['revision'])
            message=ProviderCall(type='function',call_id='message-once',name='message_managed_session',
                arguments={'session_id':child_id,'message':'Inspect the second sample'})
            with pytest.raises(Conflict,match='directly controlling'):
                await workspace.orchestrator.command(parent['id'],message)
            workspace.orchestrator.control(owner,child_id,owner='orchestrator',revision=taken['revision'])
            result=await workspace.orchestrator.command(parent['id'],message)
            assert await workspace.orchestrator.command(parent['id'],message)==result
            assert sum(e['type']=='task.steered' for e in ledger.events(child['task_id']))==1
            stranger=Principal('foreign-user','Foreign',owner.scopes,owner.policy_version)
            with pytest.raises((KeyError,PermissionError)):
                workspace.orchestrator.control(stranger,child_id,owner='user',revision=1)
            workspace.state.identity.set_scopes(owner.id,['machine-view'])
            with pytest.raises(PermissionError):workspace.orchestrator.snapshot(owner)
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_direct_followup_claims_user_and_canceled_main_cannot_resurrect_child(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            manager.cancel(child['task_id'])
            await wait_for_status(ledger,child['task_id'],'cancelled')
            task=await workspace.send(owner,child['session_id'],prompt='Direct user instruction',request_id='direct-child-once')
            assert workspace.orchestrator.relation(child['session_id'])['control_owner']=='user'
            assert task.get('parent_id') is None
            manager.cancel(parent['id'])
            await wait_for_status(ledger,parent['id'],'cancelled')
            with pytest.raises(Conflict,match='no longer accepting'):
                await workspace.orchestrator.command(parent['id'],ProviderCall(type='function',call_id='late',
                    name='message_managed_session',arguments={'session_id':child['session_id'],'message':'Late instruction'}))
            assert len(workspace.session(owner,child['session_id'])['turns'])==2
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_orchestration_tools_keep_delegation_approval_and_depth_boundaries():
    registry=default_registry()
    tools={tool['name']:tool for tool in registry.provider_tools()}
    assert {'spawn_project_agent','managed_session_status','message_managed_session','cancel_managed_session'}<=tools.keys()
    child_tools={tool['name'] for tool in registry.provider_tools(allow_subagents=False)}
    assert not {'spawn_project_agent','message_managed_session','cancel_managed_session'} & child_tools
    assert registry.spec_for(ProviderCall(type='function',call_id='x',name='spawn_project_agent',arguments={})).approval=='always'
    assert registry.spec_for(ProviderCall(type='function',call_id='x',name='message_managed_session',arguments={})).approval=='always'


def test_late_child_result_is_inline_source_report_without_inference(tmp_path,monkeypatch):
    class LateAdapter(FakeAdapter):
        def __init__(self):
            super().__init__();self.release=asyncio.Event();self.parent_turns=0
        async def turn(self,**kwargs):
            self.turns+=1
            if 'DelayedChildCase' in kwargs['prompt']:
                await self.release.wait()
                return ProviderTurn(response_id='child-response',text='Verified synthetic result',calls=[],usage={})
            self.parent_turns+=1
            calls=[_fn('late-child','spawn_subagent',agent='Researcher',task='DelayedChildCase',mode='ask')] if self.parent_turns==1 else []
            return ProviderTurn(response_id='parent-'+str(self.parent_turns),text='' if calls else 'Delegated work is running.',calls=calls,usage={})
    async def run():
        adapter=LateAdapter()
        workspace,owner,manager,ledger,primary=setup(tmp_path,adapter)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            _,approval=await wait_for_pending_approval(ledger,parent['id'],'tool')
            await manager.resolve_approval(parent['id'],approval['id'],'approved')
            await wait_for_status(ledger,parent['id'],'completed')
            child=ledger.children(parent['id'])[0]
            before=adapter.turns
            adapter.release.set()
            await wait_for_status(ledger,child['id'],'completed')
            session=workspace.session(owner,primary['id'])
            reports=[turn['task'] for turn in session['turns'] if (turn.get('task') or {}).get('coordination_report')]
            assert len(reports)==1
            assert reports[0]['engine']=='coordination' and reports[0]['model']=='No model call'
            assert reports[0]['coordination_report']['source_status']=='completed'
            assert 'Verified synthetic result' in reports[0]['result']
            assert adapter.turns==before
            workspace.orchestrator._reconciled_owners.clear()
            workspace.orchestrator.snapshot(owner)
            assert len([t for t in workspace.session(owner,primary['id'])['turns'] if (t.get('task') or {}).get('coordination_report')])==1
            assert ledger.get_task(parent['id'])['status']=='completed'
            assert 'Verified synthetic result' not in ledger.get_task(reports[0]['id'])['result']
            with pytest.raises(ValueError,match='without a model call'):
                await workspace.retry(owner,reports[0]['id'],request_id='cannot-retry-report')
            from fastapi import HTTPException
            original=workspace.record
            source=reports[0]['coordination_report']
            def revoked(principal,kind,identifier,**kwargs):
                if (kind=='task' and identifier==source['source_task_id']) or (kind=='conversation' and identifier==source['source_session_id']):
                    raise HTTPException(403,'Project access was revoked')
                return original(principal,kind,identifier,**kwargs)
            monkeypatch.setattr(workspace,'record',revoked)
            hidden=workspace.session(owner,primary['id'])['turns'][-1]['task']
            assert 'Verified synthetic result' not in hidden['result'] and 'current project access' in hidden['result']
            snapshot=workspace.orchestrator.snapshot(owner)
            assert snapshot['session']['id']==primary['id'] and snapshot['children']==[]
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_terminal_parent_is_not_resurrected_by_late_child_approval(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            ledger.update_task(parent['id'],status='completed',result='Main turn ended')
            approval=ledger.create_approval(child['task_id'],'tool',{'title':'Review synthetic change'})
            manager._escalate_child_approval(parent['id'],child['task_id'],'Researcher',approval)
            assert ledger.get_task(parent['id'])['status']=='completed'
            assert not [a for a in ledger.approvals(parent['id']) if a['status']=='pending']
            assert ledger.get_approval(approval['id'])['status']=='pending'
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_partial_child_enrollment_repairs_same_conversation(tmp_path,monkeypatch):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        original=workspace.store.update
        failed=False
        def stop_after_relation(kind,identifier,*args,**kwargs):
            nonlocal failed
            if kind=='task' and not failed and workspace.store.list('delegated_session',owner.id):
                failed=True
                raise OSError('simulated interruption after relation commit')
            return original(kind,identifier,*args,**kwargs)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            monkeypatch.setattr(workspace.store,'update',stop_after_relation)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            assert failed
            relation=workspace.store.list('delegated_session',owner.id)[0]
            assert ledger.workspace_turns_page(relation['id'])==[]
            snapshot=workspace.orchestrator.snapshot(owner)
            assert len(snapshot['children'])==1 and snapshot['children'][0]['session_id']==relation['id']
            assert workspace.store.get('task',child['task_id'])['conversation_id']==relation['id']
            assert len(ledger.workspace_turns_page(relation['id']))==1
            workspace.orchestrator._reconciled_owners.clear()
            workspace.orchestrator.snapshot(owner)
            assert len(workspace.store.list('delegated_session',owner.id))==1
            assert len(ledger.workspace_turns_page(relation['id']))==1
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_legacy_cancel_respects_direct_user_ownership(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            relation=workspace.orchestrator.relation(child['session_id'])
            workspace.orchestrator.control(owner,relation['id'],owner='user',revision=relation['revision'])
            result=manager._cancel_subagent(parent['id'],ProviderCall(type='function',call_id='legacy-cancel',name='cancel_subagent',arguments={'task_id':child['task_id']}))
            assert not result['ok'] and result['user_controlled']==[child['task_id']]
            assert ledger.get_task(child['task_id'])['status'] in {'planning','running'}
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_cancel_while_followup_hook_waits_starts_no_new_child(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            manager.cancel(child['task_id'])
            await wait_for_status(ledger,child['task_id'],'cancelled')
            started=asyncio.Event();release=asyncio.Event()
            async def hook(*args,**kwargs):
                started.set();await release.wait()
            workspace.run_hooks=hook
            instruction=ProviderCall(type='function',call_id='delayed-followup',name='message_managed_session',arguments={'session_id':child['session_id'],'message':'Follow up after hook'})
            pending=asyncio.create_task(workspace.orchestrator.command(parent['id'],instruction))
            await asyncio.wait_for(started.wait(),3)
            manager.cancel(parent['id'])
            await wait_for_status(ledger,parent['id'],'cancelled')
            release.set()
            with pytest.raises(Conflict,match='no longer accepting'):
                await pending
            assert len(ledger.children(parent['id']))==1
            assert len(ledger.workspace_turns_page(child['session_id']))==1
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_cross_project_delegation_uses_explicit_authorized_target(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        target=tmp_path/'target-project';target.mkdir();(target/'sample.txt').write_text('synthetic project data')
        project={'id':'project-safe','name':'Synthetic project','path':str(target)}
        workspace.state.projects=SimpleNamespace(projects=lambda:[project],project=lambda identifier:project if identifier==project['id'] else (_ for _ in ()).throw(KeyError(identifier)))
        allowed=False
        def check(principal,scope,project_id,cwd):
            if not allowed:raise PermissionError('Project grant is missing')
        workspace.project_check=check
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            call=ProviderCall(type='function',call_id='project-delegation',name='spawn_project_agent',arguments={'project_id':'project-safe','task':'Inspect sample.txt','agent':'Project worker','mode':'ask'})
            with pytest.raises(PermissionError,match='missing'):
                await workspace.orchestrator.command(parent['id'],call)
            assert not ledger.children(parent['id'])
            allowed=True
            result=await workspace.orchestrator.command(parent['id'],call)
            assert result['ok'] and result['session_id']
            child=ledger.get_task(result['task_id'])
            relation=workspace.orchestrator.relation(result['session_id'])
            assert child['cwd']==str(target) and relation['project_id']=='project-safe'
            assert relation['source_cwd']==str(target) and relation['target_project_id']=='project-safe'
            assert workspace.session(owner,result['session_id'])['project_id']=='project-safe'
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_parent_approval_wrapper_cannot_grant_after_child_scope_revocation(tmp_path,monkeypatch):
    from fastapi import HTTPException
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await manager._spawn_subagent(parent['id'],parent,spawn_call(),manager._cancel[parent['id']])
            approval=ledger.create_approval(child['task_id'],'tool',{'title':'Synthetic action awaiting review'})
            manager._escalate_child_approval(parent['id'],child['task_id'],'Researcher',approval)
            wrapper=next(row for row in ledger.approvals(parent['id']) if row['status']=='pending')
            original=workspace.record
            def revoked(principal,kind,identifier,**kwargs):
                if kind=='task' and identifier==child['task_id'] and kwargs.get('scope')=='agent-control':
                    raise HTTPException(403,'Child project control revoked')
                return original(principal,kind,identifier,**kwargs)
            monkeypatch.setattr(workspace,'record',revoked)
            with pytest.raises(HTTPException) as denied:
                await manager.resolve_approval(parent['id'],wrapper['id'],'approved')
            assert denied.value.status_code==403
            assert ledger.get_approval(wrapper['id'])['status']=='pending'
            assert ledger.get_approval(approval['id'])['status']=='pending'
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_scheduled_main_grant_cannot_inject_or_coordinate_other_conversations(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            with pytest.raises(PermissionError,match='specific target conversation'):
                await workspace.send(owner,primary['id'],prompt='Scheduled inspection',request_id='scheduled-primary',delegation_id='synthetic-grant')
            assert ledger.list_tasks()==[]  # No provider/context preparation began.
            parent=await running_primary(workspace,owner,manager,primary)
            metadata=workspace.store.get('task',parent['id'])
            workspace.store.update('task',parent['id'],{**metadata,'delegation_id':'synthetic-grant'},metadata['revision'])
            for name in ('managed_session_status','message_managed_session','cancel_managed_session','list_agent_sessions'):
                with pytest.raises(PermissionError,match='specific target conversation'):
                    await workspace.orchestrator.command(parent['id'],ProviderCall(type='function',call_id=name,name=name,arguments={}))
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_existing_primary_can_reopen_with_view_authority_without_allowing_creation(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            from termx.authorization import AuthorizationService
            workspace.state.authorization=AuthorizationService(workspace.state.identity)
            workspace.state.authorization.claim_principal(owner,'conversation',primary['id'])
            workspace.state.identity.set_scopes(owner.id,['agent-view'])
            reopened=workspace.orchestrator.ensure(owner)
            assert reopened['session']['id']==primary['id']
            assert ledger.list_tasks()==[]
            # Remove only the synthetic binding to exercise creation authority.
            with workspace.store.lock:
                workspace.store.db.execute("DELETE FROM records WHERE kind='orchestrator' AND id=?",(workspace.orchestrator._id(owner.id),))
                workspace.store.db.commit()
            with pytest.raises(PermissionError):
                workspace.orchestrator.ensure(owner)
            assert workspace.orchestrator.binding(owner.id) is None
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())


def test_child_report_does_not_replace_failed_main_run_status(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        try:
            task=ledger.create_task(prompt='Synthetic failed main task',cwd=primary['cwd'],provider_id='fixture',model='fixture',limits={},mode='agent',status='failed')
            ledger.update_task(task['id'],error='Original main failure')
            ledger.add_conversation_turn(primary['id'],prompt=task['prompt'],task_id=task['id'],mode='agent')
            report=ledger.add_coordination_report(primary['id'],event_id='late-child-report',source={},cwd=primary['cwd'],limits={})
            assert report['engine']=='coordination' and report['status']=='completed'
            assert workspace.session(owner,primary['id'])['latest_status']=='failed'
            assert ledger.workspace_conversations_snapshot([primary['id']])[0]['latest_status']=='failed'
            assert ledger.workspace_latest_execution_task(primary['id'])['id']==task['id']
            assert ledger.get_task(task['id'])['error']=='Original main failure'
        finally:await close(workspace,manager,ledger)
    asyncio.run(run())
