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
    async def attach(self,binding,cfg=None):
        self.binding=binding
        if self.gate=='attach':
            self.entered.set();await self.release.wait()
        return binding
    async def send(self,binding,prompt,attachments=None):
        self.sent.append(prompt);binding.status='active'
        if self.gate in {'send','send-success'}:
            self.entered.set();await self.release.wait()
            if self.gate=='send':raise RuntimeError('fixture transport closed during send')
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


def test_native_child_parent_is_durable_before_startup_and_revocation_prevents_send(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    parent=store.create_task(prompt='Coordinator',cwd=str(tmp_path),provider_id='fixture',model='fixture',limits={},status='running')
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
        permitted=True;created=[]
        def authorize():
            if not permitted:raise PermissionError('Coordinator or target grant changed')
        def enrolled(identifier):
            assert store.get_task(identifier)['parent_id']==parent['id']
            created.append(identifier)
        startup=asyncio.create_task(gateway.create_task(prompt='Bounded native work',cwd=str(tmp_path),engine='fixture',parent_id=parent['id'],dispatch_guard=authorize,cleanup_guard=lambda:False,on_created=enrolled))
        await asyncio.wait_for(adapter.entered.wait(),2)
        assert len(created)==1 and not adapter.sent
        permitted=False;adapter.release.set()
        with pytest.raises(PermissionError,match='grant changed'):
            await asyncio.wait_for(startup,2)
        assert store.get_task(created[0])['status']=='failed' and not adapter.sent
        assert adapter.closed==[adapter.binding.binding_id]
        assert adapter.binding.binding_id not in gateway._bindings
    asyncio.run(exercise())


def test_revoked_native_followup_after_configuration_is_closed_before_send(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    previous,binding=enroll();store.update_task(previous['id'],status='completed')
    binding.status='idle';store.update_engine_session(binding.binding_id,status='idle',payload=binding.as_dict())
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,gate='configure');gateway.register(adapter)
        permitted=True;created=[]
        def authorize():
            if not permitted:raise PermissionError('Child ownership changed')
        startup=asyncio.create_task(gateway.create_task(prompt='Exact existing-session followup',cwd=str(tmp_path),engine='fixture',conversation_id=binding.conversation_id,dispatch_guard=authorize,on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        permitted=False;adapter.release.set()
        with pytest.raises(PermissionError,match='ownership changed'):
            await asyncio.wait_for(startup,2)
        assert len(created)==1 and store.get_task(created[0])['status']=='failed'
        assert not adapter.sent and adapter.closed==[binding.binding_id]
        assert store.get_engine_session(binding.binding_id)['status']=='lost'
        assert store.get_task(previous['id'])['status']=='completed'
    asyncio.run(exercise())


def test_revoked_native_resume_closes_connection_without_creating_or_replaying_turn(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    previous,binding=enroll();store.update_task(previous['id'],status='completed')
    binding.status='lost';store.update_engine_session(binding.binding_id,status='lost',payload=binding.as_dict())
    gateway._bindings.pop(binding.binding_id)
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,gate='attach');gateway.register(adapter)
        permitted=True;created=[]
        def authorize():
            if not permitted:raise PermissionError('Project access revoked during resume')
        startup=asyncio.create_task(gateway.create_task(prompt='A new explicit turn',cwd=str(tmp_path),engine='fixture',conversation_id=binding.conversation_id,dispatch_guard=authorize,on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        permitted=False;adapter.release.set()
        with pytest.raises(PermissionError,match='revoked during resume'):
            await asyncio.wait_for(startup,2)
        assert not created and not adapter.sent and adapter.closed==[binding.binding_id]
        assert store.get_task(previous['id'])['status']=='completed'
        assert store.get_engine_session(binding.binding_id)['status']=='lost'
    asyncio.run(exercise())


@pytest.mark.parametrize('human_authority_valid',[True,False])
def test_post_send_guard_cannot_close_live_user_takeover_but_fails_closed_on_unknown_authority(gateway_ledger,tmp_path,human_authority_valid):
    store,gateway,_=gateway_ledger
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path,gate='send-success');gateway.register(adapter)
        permitted=True;created=[]
        def authorize():
            if not permitted:raise PermissionError('Coordinator ownership changed')
        def cleanup_allowed():
            if human_authority_valid:return False
            raise PermissionError('Cannot verify current source authority')
        startup=asyncio.create_task(gateway.create_task(prompt='One authorized native attempt',cwd=str(tmp_path),engine='fixture',dispatch_guard=authorize,cleanup_guard=cleanup_allowed,on_created=created.append))
        await asyncio.wait_for(adapter.entered.wait(),2)
        permitted=False;adapter.release.set()
        with pytest.raises(PermissionError,match='Coordinator ownership changed'):
            await asyncio.wait_for(startup,2)
        assert adapter.sent==['One authorized native attempt']
        if human_authority_valid:
            assert store.get_task(created[0])['status']=='running'
            assert not adapter.cancelled and not adapter.closed
            assert gateway._binding_task[adapter.binding.binding_id]==created[0]
        else:
            assert store.get_task(created[0])['status']=='failed'
            assert adapter.closed==[adapter.binding.binding_id]
    asyncio.run(exercise())


def test_stopped_attempt_cleanup_never_closes_binding_reassigned_to_another_active_turn(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll();store.update_task(old['id'],status='cancelled')
    newer,_=enroll('New user-directed native turn',binding)
    gateway._result_buffers[binding.binding_id]=['Current user response']
    async def exercise():
        adapter=GatedStartupAdapter(tmp_path)
        assert await gateway._abort_stopped_start(old['id'],adapter,binding)
        assert not adapter.cancelled and not adapter.closed
    asyncio.run(exercise())
    assert store.get_task(newer['id'])['status']=='running'
    assert gateway._binding_task[binding.binding_id]==newer['id']
    assert gateway._result_buffers[binding.binding_id]==['Current user response']


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


def test_restart_fails_unbound_native_startup_and_denies_lost_approval_without_replay(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    parent=store.create_task(prompt='Live internal parent',cwd=str(tmp_path),provider_id='fixture',model='fixture',limits={},status='running')
    native=store.create_task(prompt='Unbound native child',cwd=str(tmp_path),provider_id='engine:fixture',model='fixture',limits={},status='running',engine='fixture',parent_id=parent['id'])
    approval=store.create_approval(native['id'],'tool',{'engine_request_id':'request-from-stopped-host'})
    assert gateway.recover()==1
    saved=store.get_task(native['id'])
    assert saved['status']=='failed' and 'native startup' in saved['error'] and 'no work was replayed' in saved['error']
    assert saved['parent_id']==parent['id'] and store.get_task(parent['id'])['status']=='running'
    assert store.get_approval(approval['id'])['status']=='denied'
    assert gateway.recover()==0
    assert sum(event['type']=='task.failed' for event in store.events(native['id']))==1
    assert not gateway._bindings


def test_restart_reconciles_active_native_task_even_if_binding_was_already_lost(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll();store.update_engine_session(binding.binding_id,status='lost')
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'prior-host-request'})
    assert gateway.recover()==1
    assert store.get_task(task['id'])['status']=='failed'
    assert store.get_engine_session(binding.binding_id)['status']=='lost'
    assert store.get_approval(approval['id'])['status']=='denied'
    assert gateway.recover()==0


@pytest.mark.parametrize('bound',[False,True])
def test_restart_preserves_durable_native_cancellation_instead_of_offering_failed_replay(gateway_ledger,tmp_path,bound):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll() if bound else (store.create_task(prompt='Stopped startup',cwd=str(tmp_path),provider_id='engine:fixture',model='fixture',limits={},status='cancelling',engine='fixture'),None)
    store.update_task(task['id'],status='cancelling',result='Saved partial output')
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'cannot-survive-host-restart'})
    gateway.recover()
    current=store.get_task(task['id'])
    assert current['status']=='cancelled' and current['result']=='Saved partial output'
    assert 'No native work was replayed' in current['error']
    assert store.get_approval(approval['id'])['status']=='denied'
    events=[event['type'] for event in store.events(task['id'])]
    assert events.count('task.cancelled')==1 and 'task.failed' not in events
    assert gateway.recover()==0


@pytest.mark.parametrize('terminal',['cancelled','completed','failed'])
def test_native_approval_cannot_resume_a_terminal_task(gateway_ledger,terminal):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll();store.update_task(task['id'],status=terminal,result='Authoritative result')
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'old-request','engine_method':'browser.review'})
    async def exercise():
        with pytest.raises(ValueError,match='no longer has an active engine request'):
            await gateway.resolve_approval(task['id'],approval['id'],'approved')
        with pytest.raises(ValueError,match='stopped or unavailable'):
            await gateway.approval_sink(binding.binding_id,'late-request','browser.review',{},'tool')
    asyncio.run(exercise())
    assert store.get_task(task['id'])['status']==terminal
    assert store.get_task(task['id'])['result']=='Authoritative result'
    assert len(store.approvals(task['id']))==1
    assert store.get_approval(approval['id'])['status']=='pending'


@pytest.mark.parametrize('expired',[False,True])
def test_native_approval_delivery_failure_is_explicit_and_never_retransmits(gateway_ledger,tmp_path,expired):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'one-native-request'})
    responses=[]
    async def respond(*args,**kwargs):
        responses.append(args)
        if expired:raise KeyError('private expired request detail')
        raise ConnectionError('private transport detail')
    adapter.respond_approval=respond
    async def exercise():
        with pytest.raises(ValueError,match='expired before|could not be confirmed') as failure:
            await gateway.resolve_approval(task['id'],approval['id'],'approved')
        assert 'private' not in str(failure.value)
        with pytest.raises(ValueError):
            await gateway.resolve_approval(task['id'],approval['id'],'approved')
    asyncio.run(exercise())
    assert len(responses)==1 and store.get_approval(approval['id'])['status']=='approved'
    assert store.get_task(task['id'])['status']=='failed'
    events=store.events(task['id'])
    delivery=[event for event in events if event['type']=='engine.approval.delivery.failed']
    assert len(delivery)==1 and delivery[0]['payload']['delivery']==('expired' if expired else 'uncertain')
    assert any(event['type']=='task.recovery.blocked' for event in events)
    assert adapter.closed==[binding.binding_id]


def test_lost_native_approval_response_does_not_close_a_newer_user_turn(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
    approval=store.create_approval(old['id'],'tool',{'engine_request_id':'old-native-request'})
    following=[]
    async def respond(*args,**kwargs):
        binding.status='idle'
        gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','result':'Confirmed native result'}))
        newer,_=enroll('New user-owned instruction',binding);following.append(newer)
        raise ConnectionError('Old acknowledgement was lost')
    adapter.respond_approval=respond
    async def exercise():
        with pytest.raises(ValueError,match='could not be confirmed'):
            await gateway.resolve_approval(old['id'],approval['id'],'approved')
    asyncio.run(exercise())
    assert store.get_task(old['id'])['status']=='completed' and store.get_task(old['id'])['result']=='Confirmed native result'
    assert store.get_task(following[0]['id'])['status']=='running'
    assert gateway._binding_task[binding.binding_id]==following[0]['id']
    assert not adapter.closed and not adapter.cancelled
    assert not any(event['type']=='task.failed' for event in store.events(old['id']))


def test_parallel_native_approval_resolution_sends_one_decision(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
    approval=store.create_approval(task['id'],'tool',{'engine_request_id':'exact-native-request'})
    responses=[]
    async def exercise():
        entered=asyncio.Event();release=asyncio.Event()
        async def respond(*args,**kwargs):
            responses.append(args);entered.set();await release.wait()
        adapter.respond_approval=respond
        first=asyncio.create_task(gateway.resolve_approval(task['id'],approval['id'],'approved'))
        await asyncio.wait_for(entered.wait(),2)
        with pytest.raises(ValueError,match='already resolved'):
            await gateway.resolve_approval(task['id'],approval['id'],'approved')
        release.set();await asyncio.wait_for(first,2)
    asyncio.run(exercise())
    assert len(responses)==1 and store.get_approval(approval['id'])['status']=='approved'


def test_native_permissions_publish_attention_before_request_and_wait_for_last_decision(gateway_ledger,tmp_path):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
    responses=[]
    async def respond(_binding,token,decision,**kwargs):
        responses.append((token,decision))
        # The task stays waiting until the engine has accepted the decision.
        assert store.get_task(task['id'])['status']=='awaiting_approval'
    adapter.respond_approval=respond
    async def exercise():
        first=await gateway.approval_sink(binding.binding_id,'request-one','item/commandExecution/requestApproval',{'command':'synthetic'},'tool')
        second=await gateway.approval_sink(binding.binding_id,'request-two','session/request_permission',{'toolCall':{'title':'Synthetic permission'}},'tool')
        assert store.get_task(task['id'])['status']=='awaiting_approval'
        events=store.events(task['id'])
        assert [event['type'] for event in events][:2]==['task.status','approval.requested']
        await gateway.resolve_approval(task['id'],first,'approved')
        assert store.get_task(task['id'])['status']=='awaiting_approval'
        await gateway.resolve_approval(task['id'],second,'denied')
        assert store.get_task(task['id'])['status']=='running'
    asyncio.run(exercise())
    assert responses==[('request-one','approve'),('request-two','deny')]
    statuses=[event['payload']['status'] for event in store.events(task['id']) if event['type']=='task.status']
    assert statuses==['awaiting_approval','running']


@pytest.mark.parametrize('replacement',['terminal','new-turn'])
def test_native_permission_ack_cannot_restore_old_terminal_or_reassigned_turn(gateway_ledger,tmp_path,replacement):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    adapter=GatedStartupAdapter(tmp_path);gateway.register(adapter)
    following=[]
    async def respond(*args,**kwargs):
        store.update_task(task['id'],status='completed',result='Native final result')
        if replacement=='new-turn':
            newer,_=enroll('Direct user follow-up',binding)
            store.update_task(newer['id'],status='awaiting_approval')
            following.append(newer)
    adapter.respond_approval=respond
    async def exercise():
        approval=await gateway.approval_sink(binding.binding_id,'permission','browser.review',{},'tool')
        await gateway.resolve_approval(task['id'],approval,'approved')
    asyncio.run(exercise())
    assert store.get_task(task['id'])['status']=='completed'
    assert store.get_task(task['id'])['result']=='Native final result'
    if following:
        assert store.get_task(following[0]['id'])['status']=='awaiting_approval'
        assert gateway._binding_task[binding.binding_id]==following[0]['id']
    assert not [event for event in store.events(task['id']) if event['type']=='task.status' and event['payload'].get('status')=='running']


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


def test_reused_binding_routes_late_actual_turn_events_to_original_task(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    gateway._prepare_turn_dispatch(binding,old['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.started',{}, {'turn_id':'old-wire-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Original answer'}, {'turn_id':'old-wire-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'turn_id':'old-wire-turn'}))
    newer,_=enroll('New user instruction',binding)
    gateway._prepare_turn_dispatch(binding,newer['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.started',{}, {'turn_id':'current-wire-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Current answer'}, {'turn_id':'current-wire-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Stale answer'}, {'turn_id':'old-wire-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','result':'Stale final'}, {'turn_id':'old-wire-turn'}))
    assert store.get_task(newer['id'])['status']=='running'
    assert gateway._result_buffers[binding.binding_id]==['Current answer']
    assert binding.native_turn_id=='current-wire-turn'
    assert store.get_task(old['id'])['result']=='Original answer'
    assert not [event for event in store.events(newer['id']) if 'Stale' in str(event['payload'])]
    persisted=store.get_engine_session(binding.binding_id)['payload']['extensions_snapshot']
    assert persisted['termx_turn_owners']=={'old-wire-turn':old['id'],'current-wire-turn':newer['id']}
    # The same durable ownership survives rehydrating a saved session binding.
    gateway._bindings[binding.binding_id]=EngineSessionBinding(**store.get_engine_session(binding.binding_id)['payload'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'failed'}, {'turn_id':'old-wire-turn'}))
    assert store.get_task(newer['id'])['status']=='running'
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'turn_id':'current-wire-turn'}))
    assert store.get_task(newer['id'])['result']=='Current answer'
    assert store.get_task(newer['id'])['status']=='completed'


def test_unknown_or_missing_turn_id_cannot_borrow_current_native_turn(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    gateway._prepare_turn_dispatch(binding,task['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.started',{'turnId':'actual-current'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Current text','turnId':'actual-current'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Unknown stale text'}, {'turn_id':'unknown-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}))
    assert binding.native_turn_id=='actual-current'
    assert store.get_task(task['id'])['status']=='running'
    assert gateway._result_buffers[binding.binding_id]==['Current text']
    diagnostics=[event for event in store.events(task['id']) if event['type']=='engine.event.uncorrelated']
    assert len(diagnostics)==2
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed','turn':{'id':'actual-current'}}))
    assert store.get_task(task['id'])['result']=='Current text'


def test_host_attempt_completion_cannot_finish_reused_non_id_session(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    gateway._prepare_turn_dispatch(binding,old['id'])
    store.update_task(old['id'],status='completed',result='Original result')
    newer,_=enroll('New non-ID prompt',binding)
    gateway._prepare_turn_dispatch(binding,newer['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Current streamed text'}, {'dispatch_id':newer['id']}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'interrupted'}, {'dispatch_id':old['id']}))
    assert store.get_task(newer['id'])['status']=='running'
    assert gateway._result_buffers[binding.binding_id]==['Current streamed text']
    # Even direct finalization of the old ledger cannot clear this buffer.
    gateway._finish_turn(binding.binding_id,old['id'],{'status':'completed'})
    assert gateway._result_buffers[binding.binding_id]==['Current streamed text']
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'dispatch_id':newer['id']}))
    assert store.get_task(newer['id'])['result']=='Current streamed text'


def test_retired_native_permission_cannot_be_bound_to_new_turn(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    old,binding=enroll()
    gateway._prepare_turn_dispatch(binding,old['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.started',{}, {'turn_id':'retired-permission-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'turn_id':'retired-permission-turn'}))
    newer,_=enroll('New permission owner',binding)
    gateway._prepare_turn_dispatch(binding,newer['id'])
    async def exercise():
        with pytest.raises(ValueError,match='earlier turn'):
            await gateway.approval_sink(binding.binding_id,'old-request','session/request_permission',
                {'turnId':'retired-permission-turn'},'tool')
        assert not store.approvals(newer['id'])
        # A first permission may precede the new turn/start RPC response.
        request=await gateway.approval_sink(binding.binding_id,'current-request','session/request_permission',
            {'turn':{'id':'new-fast-permission-turn'}},'tool')
        assert store.get_approval(request)['task_id']==newer['id']
    asyncio.run(exercise())


def test_fast_native_completion_before_send_response_stays_final(gateway_ledger,tmp_path):
    store,gateway,_=gateway_ledger
    class FastAdapter(GatedStartupAdapter):
        async def send(self,binding,prompt,attachments=None):
            self.sent.append(prompt);binding.status='active'
            gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Fast complete result'}, {'turn_id':'fast-native-turn'}))
            binding.status='idle'
            gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'turn_id':'fast-native-turn'}))
            await asyncio.sleep(0)
            gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.bound',{}, {'turn_id':'fast-native-turn','dispatch_id':binding.extensions_snapshot['termx_dispatch_id']}))
            return 'fast-native-turn'
    async def exercise():
        adapter=FastAdapter(tmp_path,gate='none');gateway.register(adapter)
        task=await gateway.create_task(prompt='Fast native prompt',cwd=str(tmp_path),engine='fixture')
        assert task['status']=='completed' and task['result']=='Fast complete result'
        assert adapter.binding.native_turn_id=='fast-native-turn'
        owners=store.get_engine_session(adapter.binding.binding_id)['payload']['extensions_snapshot']['termx_turn_owners']
        assert owners=={'fast-native-turn':task['id']}
    asyncio.run(exercise())


def test_unknown_pre_response_events_wait_for_actual_proof_without_public_content(gateway_ledger):
    store,gateway,enroll=gateway_ledger
    task,binding=enroll()
    gateway._prepare_turn_dispatch(binding,task['id'])
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'UNMATCHED_PRIVATE_OLD_OUTPUT'}, {'turn_id':'unknown-old-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',{'delta':'Actual fast answer'}, {'turn_id':'actual-rpc-turn'}))
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.completed',{'status':'completed'}, {'turn_id':'actual-rpc-turn'}))
    assert store.get_task(task['id'])['status']=='running'
    assert binding.native_turn_id is None
    assert not gateway._result_buffers.get(binding.binding_id)
    assert 'UNMATCHED_PRIVATE_OLD_OUTPUT' not in str(store.events(task['id']))
    assert len(gateway._pending_turn_events[binding.binding_id]['events'])==3
    gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.bound',{}, {
        'turn_id':'actual-rpc-turn','dispatch_id':task['id']}))
    assert store.get_task(task['id'])['result']=='Actual fast answer'
    assert store.get_task(task['id'])['status']=='completed'
    assert 'UNMATCHED_PRIVATE_OLD_OUTPUT' not in str(store.events(task['id']))
    assert 'unknown-old-turn' not in binding.extensions_snapshot['termx_turn_owners']
    assert binding.binding_id not in gateway._pending_turn_events


@pytest.mark.parametrize('layer',['gateway','driver'])
def test_unknown_turn_buffer_overflow_fails_closed_without_replaying_or_publishing_content(gateway_ledger,tmp_path,layer):
    store,gateway,_=gateway_ledger
    class OverflowAdapter(GatedStartupAdapter):
        async def send(self,binding,prompt,attachments=None):
            self.sent.append(prompt);binding.status='active'
            if layer=='gateway':
                gateway.on_engine_event(binding.binding_id,EngineEvent('engine.message.delta',
                    {'delta':'UNMATCHED_OVERFLOW_PRIVATE_'+('x'*(1024*1024))}, {'turn_id':'overflow-wire-turn'}))
                pending=gateway._pending_turn_events[binding.binding_id]
                assert pending['overflow'] and pending['events']==[] and pending['bytes']==0
            else:
                gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.correlation.failed',
                    {'message':'Unproven native activity exceeded its safe private buffer.'},
                    {'dispatch_id':binding.extensions_snapshot['termx_dispatch_id']}))
            gateway.on_engine_event(binding.binding_id,EngineEvent('engine.turn.bound',{}, {
                'turn_id':'overflow-wire-turn','dispatch_id':binding.extensions_snapshot['termx_dispatch_id']}))
    async def exercise():
        adapter=OverflowAdapter(tmp_path,gate='none');gateway.register(adapter)
        task=await gateway.create_task(prompt='One bounded native attempt',cwd=str(tmp_path),engine='fixture')
        assert task['status']=='failed' and 'safe event buffer' in task['error']
        assert adapter.sent==['One bounded native attempt']
        assert adapter.cancelled==adapter.closed==[adapter.binding.binding_id]
        assert 'UNMATCHED_OVERFLOW_PRIVATE_' not in str(task['events'])
        assert not gateway._pending_turn_events
    asyncio.run(exercise())
