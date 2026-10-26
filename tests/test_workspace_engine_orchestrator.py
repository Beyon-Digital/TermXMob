"""Native/ACP orchestration through real workspace/gateway contracts.

Adapters below are deterministic protocol boundaries, never vendor inference.
The ACP case uses the repository's trusted local stdio fixture executable.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_agent import wait_for_status
from test_workspace_orchestrator import setup, running_primary, close
from termx.agent.providers import ProviderCall
from termx.engines.gateway import EngineGateway
from termx.engines.types import EngineCapabilities,EngineDescriptor,EngineEvent,EngineSessionBinding
from termx.identity import Principal
from termx.workspace.store import Conflict


class NativeFixture:
    def __init__(self,gateway,*,permission=False,steer=True):
        self.gateway=gateway;self.permission=permission;self.can_steer=steer
        self.sent=[];self.steered=[];self.cancelled=[];self.decisions=[];self.bindings=[]
    def descriptor(self):
        return EngineDescriptor(id='fixture-native',label='Fixture native engine',installed=True,auth_state='not_required')
    def capabilities(self):
        return EngineCapabilities(tools_filter='advisory',approvals='native',streaming=True,
            resume='supported',steer='supported' if self.can_steer else 'unsupported')
    async def create_session(self,cfg):
        binding=EngineSessionBinding.new('fixture-native','native-'+str(len(self.bindings)+1),cwd=cfg.cwd,status='idle')
        self.bindings.append(binding)
        return binding
    async def attach(self,binding,cfg=None):
        self.bindings.append(binding);return binding
    async def configure_session(self,binding,cfg):pass
    async def send(self,binding,prompt,attachments=None):
        self.sent.append((binding.native_session_id,prompt));binding.status='active'
        self.gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Synthetic native progress'}))
        if self.permission:
            await self.gateway.approval_sink(binding.binding_id,'permission-fixture','session/request_permission',
                {'toolCall':{'title':'Inspect synthetic sample'},'options':[{'kind':'allow_once','optionId':'once'}]},'tool')
    async def steer(self,binding,message):
        if not self.can_steer:return False
        self.steered.append((binding.native_session_id,message));return True
    async def cancel(self,binding):
        self.cancelled.append(binding.native_session_id)
        self.finish(binding,status='interrupted')
    async def respond_approval(self,binding,token,decision,**kwargs):
        self.decisions.append((token,decision));self.finish(binding)
    async def close(self,binding):binding.status='closed'
    async def shutdown(self):
        for binding in self.bindings:binding.status='closed'
    def finish(self,binding=None,*,status='completed',result='Synthetic native result'):
        binding=binding or self.bindings[-1]
        binding.status='idle'
        self.gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':status,'result':result}))


def configure(workspace,manager,ledger,**kwargs):
    gateway=EngineGateway(ledger,manager.emit_external)
    adapter=NativeFixture(gateway,**kwargs);gateway.register(adapter)
    workspace.state.engines=gateway
    return gateway,adapter


def spawn(call_id='native-once',**arguments):
    return ProviderCall(type='function',call_id=call_id,name='spawn_engine_agent',
        arguments={'engine':'fixture-native','task':'Inspect synthetic native sample','mode':'agent',**arguments})


async def finish_all(workspace,manager,ledger,gateway):
    await gateway.shutdown()
    await close(workspace,manager,ledger)


def test_native_spawn_uses_gateway_identity_dedup_and_parent_handle(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            result=await workspace.orchestrator.command(parent['id'],spawn())
            assert result['engine']=='fixture-native' and len(adapter.sent)==1
            task=ledger.get_task(result['task_id'])
            assert task['parent_id']==parent['id'] and task['engine']=='fixture-native'
            session=workspace.session(owner,result['session_id'])
            assert session['engine']=='fixture-native' and session['cwd']!=primary['cwd']
            assert ledger.engine_session_for_task(task['id'])['native_session_id']=='native-1'
            assert await workspace.orchestrator.command(parent['id'],spawn())==result
            assert len(adapter.sent)==1
            with pytest.raises(Conflict,match='changed content'):
                await workspace.orchestrator.command(parent['id'],spawn(task='different native action'))
            adapter.finish()
            handle=manager._subagent_handles(parent['id'])[task['id']]
            assert handle.done.is_set() and handle.status=='completed'
            assert any(event['type']=='subagent.finished' and event['payload']['child_id']==task['id'] for event in ledger.events(parent['id']))
            snapshot=workspace.orchestrator.snapshot(owner)
            assert snapshot['children'][0]['engine']=='fixture-native'
            assert snapshot['children'][0]['capability_limits']['tool_steps']=='unmeasured'
            assert snapshot['children'][0]['capability_limits']['cost']=='unmeasured'
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_native_approval_propagates_as_exact_source_without_auto_grant(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger,permission=True)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await workspace.orchestrator.command(parent['id'],spawn())
            approvals=[row for row in ledger.approvals(child['task_id']) if row['status']=='pending']
            assert len(approvals)==1 and adapter.decisions==[]
            assert not [row for row in ledger.approvals(parent['id']) if row['status']=='pending']
            snapshot=workspace.orchestrator.snapshot(owner)
            assert snapshot['children'][0]['status']=='awaiting_approval'
            assert snapshot['children'][0]['pending_approvals']==1
            assert any(row['kind']=='approval.requested' and row['task_id']==child['task_id'] for row in snapshot['activity'])
            assert any(event['type']=='subagent.event' and event['payload']['type']=='approval.requested' for event in ledger.events(parent['id']))
            await gateway.resolve_approval(child['task_id'],approvals[0]['id'],'approved')  # Explicit synthetic human decision.
            assert adapter.decisions==[('permission-fixture','approve')]
            assert ledger.get_task(child['task_id'])['status']=='completed'
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_existing_native_adoption_preserves_history_target_and_identity(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            existing=workspace.create_session(owner,title='Existing native chat',engine='fixture-native',mode='agent')
            original=await workspace.send(owner,existing['id'],prompt='Original user instruction',request_id='original-native')
            original_binding=ledger.engine_session_for_task(original['id'])
            preview=workspace.orchestrator.native.review(owner,existing['id'])
            call=ProviderCall(type='function',call_id='adopt-existing',name='adopt_managed_session',arguments={
                'session_id':existing['id'],'expected_revision':preview['revision'],'review_digest':preview['review_digest']})
            result=await workspace.orchestrator.command(parent['id'],call)
            assert result['control_owner']=='user' and result['requires_user_handoff']
            assert len(adapter.sent)==1 and ledger.get_task(original['id']).get('parent_id') is None
            assert ledger.engine_session_for_task(original['id'])==original_binding
            current=workspace.session(owner,existing['id'])
            assert current['cwd']==existing['cwd'] and current['engine']==existing['engine']
            assert [turn['task_id'] for turn in current['turns']]==[original['id']]
            message=ProviderCall(type='function',call_id='native-steer',name='message_managed_session',arguments={'session_id':existing['id'],'message':'Check the second sample'})
            with pytest.raises(Conflict,match='directly controlling'):
                await workspace.orchestrator.command(parent['id'],message)
            relation=workspace.orchestrator.relation(existing['id'])
            workspace.orchestrator.control(owner,existing['id'],owner='orchestrator',revision=relation['revision'])
            await workspace.orchestrator.command(parent['id'],message)
            assert adapter.steered==[('native-1','Check the second sample')]
            adapter.finish()
            followup=ProviderCall(type='function',call_id='native-followup',name='message_managed_session',arguments={'session_id':existing['id'],'message':'One bounded follow-up'})
            next_task=await workspace.orchestrator.command(parent['id'],followup)
            assert len(adapter.sent)==2
            assert ledger.engine_session_for_task(next_task['task_id'])['native_session_id']==original_binding['native_session_id']
            assert ledger.get_task(next_task['task_id'])['parent_id']==parent['id']
            assert len(workspace.session(owner,existing['id'])['turns'])==2
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_adoption_review_is_scope_revision_and_request_bound_without_inference(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            existing=workspace.create_session(owner,title='Empty existing chat',engine='fixture-native',mode='agent')
            preview=workspace.orchestrator.native.review(owner,existing['id'])
            call=ProviderCall(type='function',call_id='direct-adoption',name='adopt_managed_session',arguments={
                'session_id':existing['id'],'expected_revision':preview['revision'],'review_digest':preview['review_digest']})
            workspace.update_session(owner,existing['id'],revision=existing['revision'],changes={'title':'Updated existing chat'})
            with pytest.raises(Conflict,match='changed'):
                workspace.orchestrator.native.adopt(owner,'user:'+owner.id,call)
            preview=workspace.orchestrator.native.review(owner,existing['id'])
            call.arguments.update(expected_revision=preview['revision'],review_digest=preview['review_digest'])
            result=workspace.orchestrator.native.adopt(owner,'user:'+owner.id,call)
            assert workspace.orchestrator.native.adopt(owner,'user:'+owner.id,call)==result
            row=workspace.orchestrator.snapshot(owner)['children'][0]
            assert row['task_id'] is None and row['status']=='idle' and row['origin']=='adopted'
            assert adapter.sent==[] and ledger.list_tasks()==[]
            stranger=Principal('other-owner','Other',owner.scopes,owner.policy_version)
            with pytest.raises((KeyError,PermissionError)):
                workspace.orchestrator.native.review(stranger,existing['id'])
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_native_takeover_stops_coordinator_watch_and_return_restarts_lease(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await workspace.orchestrator.command(parent['id'],spawn())
            await asyncio.sleep(.05)
            assert manager.tree_budget.snapshot(parent['id'])['active_workers']==2
            relation=workspace.orchestrator.relation(child['session_id'])
            taken=workspace.orchestrator.control(owner,child['session_id'],owner='user',revision=relation['revision'])
            await asyncio.sleep(.55)
            assert manager.tree_budget.snapshot(parent['id'])['active_workers']==1
            assert adapter.cancelled==[]
            workspace.orchestrator.control(owner,child['session_id'],owner='orchestrator',revision=taken['revision'])
            await asyncio.sleep(.05)
            assert manager.tree_budget.snapshot(parent['id'])['active_workers']==2
            relation=workspace.orchestrator.relation(child['session_id'])
            workspace.orchestrator.control(owner,child['session_id'],owner='user',revision=relation['revision'])
            manager.cancel(parent['id'])
            await wait_for_status(ledger,parent['id'],'cancelled')
            await asyncio.sleep(.55)
            assert adapter.cancelled==[] and ledger.get_task(child['task_id'])['status']=='running'
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_native_worker_cap_and_unverified_readonly_are_fail_closed(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            ledger.update_task(parent['id'],limits={**parent['limits'],'max_parallel_subagents':1})
            with pytest.raises(ValueError,match='containment'):
                await workspace.orchestrator.command(parent['id'],spawn(mode='ask'))
            assert adapter.sent==[]
            first=await workspace.orchestrator.command(parent['id'],spawn())
            with pytest.raises(Conflict,match='worker allowance'):
                await workspace.orchestrator.command(parent['id'],spawn('second-worker'))
            assert len(adapter.sent)==1
            cancel=ProviderCall(type='function',call_id='native-cancel',name='cancel_managed_session',arguments={'session_id':first['session_id']})
            outcome=await workspace.orchestrator.command(parent['id'],cancel)
            assert adapter.cancelled==['native-1'] and outcome['cancelled']==[first['task_id']]
            assert await workspace.orchestrator.command(parent['id'],cancel)==outcome and adapter.cancelled==['native-1']
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_returning_new_user_turn_does_not_inherit_cancelled_original_main(tmp_path):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            old_parent=await running_primary(workspace,owner,manager,primary)
            original=await workspace.orchestrator.command(old_parent['id'],spawn())
            relation=workspace.orchestrator.relation(original['session_id'])
            workspace.orchestrator.control(owner,original['session_id'],owner='user',revision=relation['revision'])
            manager.cancel(old_parent['id'])
            await wait_for_status(ledger,old_parent['id'],'cancelled')
            adapter.finish()
            direct=await workspace.send(owner,original['session_id'],prompt='User-owned next turn',request_id='new-direct-native')
            assert direct.get('parent_id') is None
            relation=workspace.orchestrator.relation(original['session_id'])
            workspace.orchestrator.control(owner,original['session_id'],owner='orchestrator',revision=relation['revision'])
            await asyncio.sleep(.55)
            assert adapter.cancelled==[] and ledger.get_task(direct['id'])['status']=='running'
            current=next(row for row in workspace.orchestrator.snapshot(owner)['children'] if row['session_id']==original['session_id'])
            assert current['capability_limits']['time_budget']=='host-monitoring'
            fresh=await workspace.send(owner,primary['id'],prompt='Manage the returned child',request_id='fresh-primary-native')
            await manager.resolve_approval(fresh['id'],fresh['approvals'][0]['id'],'approved')
            await asyncio.sleep(0)
            await workspace.orchestrator.command(fresh['id'],ProviderCall(type='function',call_id='steer-returned-child',
                name='message_managed_session',arguments={'session_id':original['session_id'],'message':'One current bounded instruction'}))
            assert workspace.store.get('task',direct['id'])['native_supervisor_parent']==fresh['id']
            manager.cancel(fresh['id'])
            await wait_for_status(ledger,fresh['id'],'cancelled')
            await wait_for_status(ledger,direct['id'],'cancelled')
            assert adapter.cancelled==['native-1']
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_native_supervision_store_failure_requests_one_cancel_and_reports(tmp_path,monkeypatch):
    from fastapi import HTTPException
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await workspace.orchestrator.command(parent['id'],spawn())
            original=workspace.validate_task_execution
            def unavailable(identifier):
                if identifier==child['task_id']:raise HTTPException(503,'Synthetic unavailable authorization store')
                return original(identifier)
            monkeypatch.setattr(workspace,'validate_task_execution',unavailable)
            await wait_for_status(ledger,child['task_id'],'cancelled')
            assert adapter.cancelled==['native-1']
            assert any(event['type']=='task.recovery.blocked' and 'supervision' in event['payload'].get('message','').lower()
                for event in ledger.events(child['task_id']))
            assert ledger.get_task(child['task_id'])['status']=='cancelled'
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_takeover_between_cancel_receipt_and_scheduled_effect_prevents_dispatch(tmp_path,monkeypatch):
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await workspace.orchestrator.command(parent['id'],spawn())
            original=asyncio.wait_for
            async def handoff_before_effect(coroutine,timeout):
                if getattr(getattr(coroutine,'cr_code',None),'co_name','')=='owned_cancel':
                    relation=workspace.orchestrator.relation(child['session_id'])
                    workspace.orchestrator.control(owner,child['session_id'],owner='user',revision=relation['revision'])
                return await original(coroutine,timeout)
            monkeypatch.setattr(asyncio,'wait_for',handoff_before_effect)
            call=ProviderCall(type='function',call_id='cancel-during-handoff',name='cancel_managed_session',arguments={'session_id':child['session_id']})
            with pytest.raises(Conflict,match='direct control'):
                await workspace.orchestrator.command(parent['id'],call)
            assert adapter.cancelled==[] and ledger.get_task(child['task_id'])['status']=='running'
            assert workspace.store.get('task',child['task_id']).get('native_cancel_request') is None
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_actual_acp_stdio_child_dispatches_once_and_resumes_same_native_session(tmp_path):
    from test_engines_acp import FakeAcpEngine
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway=EngineGateway(ledger,manager.emit_external)
        engine=FakeAcpEngine(event_sink=gateway.on_engine_event,approval_sink=gateway.approval_sink,
            env_extra={'FAKE_ACP_STATE_FILE':str(tmp_path/'fake-acp-state.json'),'FAKE_ACP_LOG':str(tmp_path/'fake-acp-requests.jsonl')})
        engine._executable=sys.executable
        gateway.register(engine);workspace.state.engines=gateway
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            call=spawn(engine='fake-acp',task='Inspect one synthetic example')
            result=await workspace.orchestrator.command(parent['id'],call)
            await wait_for_status(ledger,result['task_id'],'completed')
            original=ledger.engine_session_for_task(result['task_id'])
            followup=ProviderCall(type='function',call_id='acp-followup',name='message_managed_session',arguments={'session_id':result['session_id'],'message':'Inspect a second synthetic example'})
            second=await workspace.orchestrator.command(parent['id'],followup)
            await wait_for_status(ledger,second['task_id'],'completed')
            assert ledger.engine_session_for_task(second['task_id'])['native_session_id']==original['native_session_id']
            assert len(workspace.session(owner,result['session_id'])['turns'])==2
            log=(tmp_path/'fake-acp-requests.jsonl').read_text()
            assert log.count('"method": "session/prompt"')==2
            assert 'fake-acp' in workspace.orchestrator.snapshot(owner)['children'][0]['engine']
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())


def test_legacy_status_and_await_hide_cached_result_after_source_revocation(tmp_path,monkeypatch):
    from fastapi import HTTPException
    import json
    async def run():
        workspace,owner,manager,ledger,primary=setup(tmp_path)
        gateway,adapter=configure(workspace,manager,ledger)
        try:
            parent=await running_primary(workspace,owner,manager,primary)
            child=await workspace.orchestrator.command(parent['id'],spawn(agent='Private project specialist'))
            adapter.finish(result='Private synthetic native result')
            original=workspace.record
            def revoked(principal,kind,identifier,**kwargs):
                if kind=='task' and identifier==child['task_id'] or kind=='conversation' and identifier==child['session_id']:
                    raise HTTPException(403,'Source project access revoked')
                return original(principal,kind,identifier,**kwargs)
            monkeypatch.setattr(workspace,'record',revoked)
            call=ProviderCall(type='function',call_id='legacy-status',name='subagent_status',arguments={})
            status=manager._subagent_statuses(parent['id'],call)
            waited=await manager._await_subagents(parent['id'],ProviderCall(type='function',call_id='legacy-await',name='await_subagents',arguments={'timeout_s':0}),asyncio.Event())
            for result in (status,waited):
                assert not result['ok'] and result['children'][child['task_id']]['status']=='unavailable'
                assert 'result' not in result['children'][child['task_id']]
                assert 'Private project specialist' not in json.dumps(result)
                assert 'Synthetic native progress' not in json.dumps(result)
        finally:await finish_all(workspace,manager,ledger,gateway)
    asyncio.run(run())
