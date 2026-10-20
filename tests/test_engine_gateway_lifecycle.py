"""Native lifecycle events preserve durable turn identity and terminal state."""
from __future__ import annotations

import pytest

from termx.agent.store import AgentStore
from termx.engines.gateway import EngineGateway
from termx.engines.types import EngineEvent, EngineSessionBinding


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
