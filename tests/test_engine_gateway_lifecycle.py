"""Native lifecycle events preserve durable turn identity and terminal state."""
from __future__ import annotations

import asyncio
import pytest

from termx.agent.store import AgentStore
from termx.engines.gateway import EngineGateway
from termx.engines.types import EngineEvent, EngineSessionBinding


class GatedStartupAdapter:
    """No engine process or model call: gates expose gateway admission races."""
    def __init__(self,root,*,gate='create',close_fails=False):
        self.root=str(root);self.gate=gate;self.close_fails=close_fails
        self.entered=asyncio.Event();self.release=asyncio.Event()
        self.sent=[];self.cancelled=[];self.closed=[];self.binding=None
    def descriptor(self):
        return type('Descriptor',(),{'id':'fixture'})()
    def capabilities(self):
        return type('Capabilities',(),{'mcp_native':'unsupported','tools_filter':'gateway'})()
    async def create_session(self,cfg):
        if self.gate=='create':
            self.entered.set();await self.release.wait()
        self.binding=EngineSessionBinding.new('fixture','native-gated',cwd=self.root,status='idle')
        return self.binding
    async def configure_session(self,binding,cfg):
        if self.gate=='configure':
            self.entered.set();await self.release.wait()
    async def send(self,binding,prompt,attachments=None):
        self.sent.append(prompt);binding.status='active'
        if self.gate=='send':
            self.entered.set();await self.release.wait()
            raise RuntimeError('fixture transport closed during send')
    async def cancel(self,binding):
        self.cancelled.append(binding.binding_id)
    async def close(self,binding):
        self.closed.append(binding.binding_id)
        if self.close_fails:raise RuntimeError('fixture cleanup failure')
        binding.status='closed'


def test_stop_during_new_native_session_creation_never_dispatches_late_prompt(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
        created=[]
        startup=asyncio.create_task(gateway.create_task(prompt='Cancelled startup',cwd=str(tmp_path),engine='fixture',on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        assert len(created)==1
        cancelled=await gateway.cancel(created[0]);assert cancelled['status']=='cancelled'
        adapter.release.set()
        result=await asyncio.wait_for(startup,2)
        assert result['status']=='cancelled' and not adapter.sent
        assert adapter.closed==[adapter.binding.binding_id]
        assert adapter.binding.binding_id not in gateway._bindings
        assert store.engine_session_for_task(created[0]) is None
    asyncio.run(exercise())


def test_stop_during_existing_native_session_configuration_prevents_followup_dispatch(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    previous,binding=enroll();store.update_task(previous['id'],status='completed')
    binding.status='idle'
    store.update_engine_session(binding.binding_id,status='idle',payload=binding.as_dict())
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,gate='configure');gateway.register(adapter)
        created=[]
        startup=asyncio.create_task(gateway.create_task(prompt='Cancelled follow-up',cwd=str(tmp_path),engine='fixture',conversation_id=binding.conversation_id,on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        await gateway.cancel(created[0])
        adapter.release.set()
        result=await asyncio.wait_for(startup,2)
        assert result['status']=='cancelled' and not adapter.sent
        assert adapter.closed==[binding.binding_id]
        assert store.get_engine_session(binding.binding_id)['status']=='lost'
        assert binding.binding_id not in gateway._bindings
        assert store.get_task(previous['id'])['status']=='completed'
    asyncio.run(exercise())


def test_cancelled_native_startup_discloses_unconfirmed_cleanup_without_dispatch(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,close_fails=True);gateway.register(adapter)
        created=[]
        startup=asyncio.create_task(gateway.create_task(prompt='Cancelled startup',cwd=str(tmp_path),engine='fixture',on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        await gateway.cancel(created[0]);adapter.release.set()
        with pytest.raises(ValueError,match='cleanup could not be confirmed'):
            await asyncio.wait_for(startup,2)
        assert store.get_task(created[0])['status']=='cancelled' and not adapter.sent
        assert any(event['type']=='engine.session.cleanup.failed' for event in store.events(created[0]))
    asyncio.run(exercise())


def test_send_error_after_confirmed_cancellation_never_replaces_terminal_outcome(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,gate='send');gateway.register(adapter)
        created=[]
        async def confirmed_cancel(binding):
            adapter.cancelled.append(binding.binding_id)
            binding.status='closed'
            gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'interrupted'}))
        adapter.cancel=confirmed_cancel
        startup=asyncio.create_task(gateway.create_task(prompt='One attempted native send',cwd=str(tmp_path),engine='fixture',on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        cancelled=await gateway.cancel(created[0]);assert cancelled['status']=='cancelled'
        adapter.release.set()
        result=await asyncio.wait_for(startup,2)
        assert result['status']=='cancelled' and result['error']=='Interrupted'
        assert adapter.sent==['One attempted native send']
        assert adapter.cancelled==[adapter.binding.binding_id] and not adapter.closed
        assert not any(event['type']=='task.failed' for event in store.events(created[0]))
    asyncio.run(exercise())


@pytest.fixture
def gateway_ledger(tmp_path):
    store=AgentStore(tmp_path/'agents.sqlite3',tmp_path/'artifacts')
    gateway=EngineGateway(store,store.append_event)

    def enroll(prompt='Original request',binding=None):
        task=store.create_task(prompt=prompt,cwd=str(tmp_path),provider_id='engine:fixture',
                               model='fixture',limits={},engine='fixture',status='running')
        if binding is None:
            conversation=store.create_conversation(cwd=str(tmp_path))
            binding=EngineSessionBinding.new('fixture','pending_fixture',cwd=str(tmp_path),
                                             conversation_id=conversation['id'],status='active')
            store.save_engine_session(binding.binding_id,task_id=task['id'],engine='fixture',
                native_session_id=binding.native_session_id,conversation_id=conversation['id'],
                cwd=str(tmp_path),status='active',payload=binding.as_dict())
        else:
            binding.status='active'
            store.update_engine_session(binding.binding_id,task_id=task['id'],status='active',
                                        payload=binding.as_dict())
        gateway._bindings[binding.binding_id]=binding
        gateway._binding_task[binding.binding_id]=task['id']
        store.update_task(task['id'],engine_session_id=binding.binding_id,
                          engine_native_id=binding.native_session_id)
        return task,binding

    yield store,gateway,enroll
    store.close()


@pytest.mark.parametrize('terminal',['completed','failed','cancelled'])
def test_late_permission_expiry_never_resurrects_a_terminal_turn(gateway_ledger,terminal):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'expired-request'})
    store.update_task(task['id'],status=terminal,result='Preserved outcome')
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.approval.expired',{
        'request_id':'expired-request'}))
    saved=store.get_task(task['id'])
    assert saved['status']==terminal
    assert saved['result']=='Preserved outcome'
    assert next(row for row in store.approvals(task['id']) if row['id']==approval['id'])['status']=='denied'


@pytest.mark.parametrize('terminal',['completed','failed','cancelled'])
def test_late_native_completion_preserves_authoritative_terminal_outcome(gateway_ledger,terminal):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    store.update_task(task['id'],status=terminal,result='Confirmed result',error='Confirmed diagnostic')
    gateway._result_buffers[binding.binding_id]=['Late buffered response']
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{
        'status':'completed','result':'Late replacement'}))
    saved=store.get_task(task['id'])
    assert (saved['status'],saved['result'],saved['error'])==(terminal,'Confirmed result','Confirmed diagnostic')
    events=store.events(task['id'])
    assert [event['type'] for event in events]==['engine.turn.completed']
    assert binding.binding_id not in gateway._result_buffers


def test_confirmed_native_interrupt_survives_late_completion_without_duplicate_outcome(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Preserved partial answer'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'interrupted'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','result':'Arrived after Stop'}))
    saved=store.get_task(task['id'])
    assert (saved['status'],saved['result'],saved['error'])==('cancelled','Preserved partial answer','Interrupted')
    types=[event['type'] for event in store.events(task['id'])]
    assert types.count('engine.turn.completed')==2 and types.count('task.cancelled')==1
    assert 'task.completed' not in types and 'task.failed' not in types


def test_duplicate_native_completion_keeps_first_result_and_emits_one_completion(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','result':'First final answer'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Duplicate tail'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','result':'Second final answer'}))
    saved=store.get_task(task['id'])
    assert saved['status']=='completed' and saved['result']=='First final answer'
    types=[event['type'] for event in store.events(task['id'])]
    assert types.count('engine.turn.completed')==2 and types.count('task.completed')==1
    assert binding.binding_id not in gateway._result_buffers


def test_old_permission_expiry_cannot_release_a_new_turns_human_wait(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    previous=store.create_approval(old['id'],'tool',{'engine_request_id':'old-request'})
    store.resolve_approval(previous['id'],'denied')
    store.update_task(old['id'],status='cancelled')
    newer,_=enroll('Explicit follow-up',binding)
    pending=store.create_approval(newer['id'],'tool',{'engine_request_id':'new-request'})
    store.update_task(newer['id'],status='awaiting_approval')
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.approval.expired',{
        'request_id':'old-request'}))
    assert store.get_task(newer['id'])['status']=='awaiting_approval'
    assert next(row for row in store.approvals(newer['id']) if row['id']==pending['id'])['status']=='pending'


def test_native_session_id_learned_during_turn_survives_reopening_the_ledger(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    binding.native_session_id='native-session-from-init'
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.session.bound',{
        'native_session_id':binding.native_session_id}))
    recovered=AgentStore(store.path,store.artifact_dir)
    try:
        record=recovered.engine_session_for_conversation(binding.conversation_id)
        assert record['native_session_id']=='native-session-from-init'
        assert record['payload']['native_session_id']=='native-session-from-init'
        assert recovered.get_task(task['id'])['engine_native_id']=='native-session-from-init'
    finally:recovered.close()


def test_lost_turn_keeps_partial_result_without_contaminating_followup_or_other_binding(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    independent,other=enroll('Other workspace request')
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Original partial answer'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{
        'delta':'Private worker output','parent_call_id':'delegated-call'}))
    gateway.on_engine_event(other.binding_id,EngineEvent('engine.message.delta',{'delta':'Independent answer'}))
    approval=store.create_approval(old['id'],'tool',{'engine_request_id':'connection-lost'})
    binding.status='lost'
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.session.lost',{'message':'Protocol stream closed'}))
    assert store.get_task(old['id'])['status']=='failed'
    assert store.get_task(old['id'])['result']=='Original partial answer'
    assert store.get_engine_session(binding.binding_id)['status']=='lost'
    assert next(row for row in store.approvals(old['id']) if row['id']==approval['id'])['status']=='denied'

    following,_=enroll('Explicit follow-up',binding)
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Fresh answer'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}))
    gateway.on_engine_event(other.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}))
    assert store.get_task(following['id'])['result']=='Fresh answer'
    assert store.get_task(independent['id'])['result']=='Independent answer'
    assert store.get_task(old['id'])['result']=='Original partial answer'
