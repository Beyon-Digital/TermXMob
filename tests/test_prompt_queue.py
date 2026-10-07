from types import SimpleNamespace
import asyncio
import pytest
from termx.agent.store import AgentStore
from termx.identity import AuthenticationService
from termx.workspace.service import WorkspaceService
from termx.workspace.prompt_queue import PromptQueue
from termx.workspace.store import Conflict

class Agent:
    def __init__(self,store):self.store=store;self._listeners=set();self.calls=[]
    async def create_task(self,**kwargs):
        self.calls.append(kwargs)
        task=self.store.create_task(prompt=kwargs['prompt'],cwd=kwargs['cwd'],provider_id='fixture',model='fixture',limits=kwargs['limits'],mode=kwargs['mode'])
        kwargs['on_created'](task['id']);await asyncio.sleep(0);return task

@pytest.fixture
def anyio_backend():return 'asyncio'

@pytest.fixture
def queued(tmp_path):
    identity=AuthenticationService(tmp_path/'identity.sqlite3');owner=identity.setup_owner('owner','strong fixture password')
    credentials=asyncio.run(identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture'));origin=identity.resolve(credentials.access_token)
    ledger=AgentStore(tmp_path/'agent.sqlite3',tmp_path/'artifacts');agent=Agent(ledger)
    state=SimpleNamespace(identity=identity,agent_store=ledger,agent=agent,engines=SimpleNamespace(engines=lambda:[]))
    workspace=WorkspaceService(state,tmp_path/'workspace.sqlite3',project_check=lambda *args:None)
    row=workspace.create_session(origin.principal,title='Queued work',project_id='fixture-project',cwd=str(tmp_path),provider_id='fixture',model='fixture')
    queue=PromptQueue(workspace)
    yield queue,workspace,origin,credentials,row,agent
    workspace.store.close();ledger.close()

@pytest.mark.anyio
async def test_ordered_exact_request_survives_queue_reconstruction_and_one_dispatch(queued):
    queue,workspace,origin,credentials,row,agent=queued
    first=await workspace.send(origin.principal,row['id'],prompt='initial',request_id='first-request',managed_session_id=origin.session_id)
    a=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='next exact',request_id='queued-request')
    assert queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='next exact',request_id='queued-request')['id']==a['id']
    with pytest.raises(Conflict):queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='different',request_id='queued-request')
    b=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='then last',request_id='third-request')
    restarted=PromptQueue(workspace)
    await restarted.tick();assert len(agent.calls)==1
    workspace.agents.update_task(first['id'],status='completed',result='done')
    await asyncio.gather(restarted.tick(),queue.tick())
    current=workspace.store.get('prompt_queue',a['id']);assert current['status']=='dispatched';assert len(agent.calls)==2
    assert agent.calls[-1]['prompt']=='next exact'
    assert workspace.store.get('prompt_queue',b['id'])['status']=='queued'
    workspace.agents.update_task(current['task_id'],status='completed',result='done')
    await restarted.tick();assert len(agent.calls)==3;assert agent.calls[-1]['prompt']=='then last'

@pytest.mark.anyio
async def test_changed_binding_requires_explicit_current_review_and_never_retargets(queued):
    queue,workspace,origin,credentials,row,agent=queued
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='queued',request_id='queued-request')
    workspace.update_session(origin.principal,row['id'],revision=row['revision'],changes={'model':'different'})
    await queue.tick();assert not agent.calls
    blocked=queue.list(origin.principal,row['id'])[0];assert blocked['status']=='blocked'
    with pytest.raises(Conflict):queue.renew(origin.principal,item['id'],sid=origin.session_id,revision=blocked['revision'],target_digest=item['target_digest'])
    current=queue.current(origin.principal,item['id'])
    queue.renew(origin.principal,item['id'],sid=origin.session_id,revision=blocked['revision'],target_digest=current['target_digest'])
    await queue.tick();assert len(agent.calls)==1;assert agent.calls[0]['model']=='different'

@pytest.mark.anyio
async def test_locked_or_revoked_origin_does_not_borrow_other_live_session(queued):
    queue,workspace,origin,credentials,row,agent=queued
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='queued',request_id='queued-request')
    other=await workspace.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='other')
    workspace.state.identity.revoke(origin.session_id,origin.principal.id)
    await queue.tick();assert not agent.calls
    actor=workspace.state.identity.resolve(other.access_token)
    blocked=queue.list(actor.principal,row['id'])[0];assert blocked['status']=='blocked'
    review=queue.current(actor.principal,item['id'])
    queue.renew(actor.principal,item['id'],sid=actor.session_id,revision=blocked['revision'],target_digest=review['target_digest'])
    await queue.tick();assert len(agent.calls)==1

@pytest.mark.anyio
async def test_enqueue_expiry_cancel_policy_and_content_bounds(queued):
    queue,workspace,origin,credentials,row,agent=queued
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='queued',request_id='queued-request')
    queue.clock=lambda:item['expires_at']+1
    await queue.tick();assert not agent.calls
    current=queue.list(origin.principal,row['id'])[0]
    assert current['status']=='blocked' and 'expired' in current['reason']
    queue.cancel(origin.principal,item['id'],current['revision']);await queue.tick();assert not agent.calls
    with pytest.raises(ValueError):queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='image',request_id='image-request',attachments=[{'mime':'image/png','data':'invalid!'}])
    workspace.state.identity.set_scopes(origin.principal.id,['agent-view'])
    with pytest.raises(PermissionError):queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='denied',request_id='scope-request')

@pytest.mark.anyio
async def test_dispatch_receipt_heals_after_restart_without_new_worker(queued):
    queue,workspace,origin,credentials,row,agent=queued
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='exact',request_id='queue-request')
    private=workspace.store.get('prompt_queue',item['id'])
    workspace.store.update('prompt_queue',item['id'],{**private,'status':'dispatching'})
    task=await workspace.send(origin.principal,row['id'],prompt='exact',request_id=private['dispatch_key'],managed_session_id=origin.session_id)
    recovered=PromptQueue(workspace)
    assert recovered.list(origin.principal,row['id'])[0]['task_id']==task['id']
    await recovered.tick();assert len(agent.calls)==1
    workspace.agents.update_task(task['id'],status='completed')
    other=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='ambiguous',request_id='second-queued-request')
    private=workspace.store.get('prompt_queue',other['id']);workspace.store.update('prompt_queue',other['id'],{**private,'status':'dispatching'})
    restarted=PromptQueue(workspace);blocked=restarted.list(origin.principal,row['id'])[-1]
    assert blocked['status']=='blocked';assert workspace.store.get('prompt_queue',other['id'])['ambiguous']
    review=restarted.current(origin.principal,other['id'])
    with pytest.raises(Conflict):restarted.renew(origin.principal,other['id'],sid=origin.session_id,revision=blocked['revision'],target_digest=review['target_digest'])
    await restarted.tick();assert len(agent.calls)==1

@pytest.mark.anyio
async def test_guard_after_hook_preparation_refuses_retarget_without_running(queued):
    queue,workspace,origin,credentials,row,agent=queued
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='queued',request_id='guarded-request')
    async def changing_hook(*args,**kwargs):
        current=workspace.session(origin.principal,row['id'],turns=False)
        workspace.update_session(origin.principal,row['id'],revision=current['revision'],changes={'model':'changed-during-hook'})
    workspace.run_hooks=changing_hook
    await queue.tick();assert not agent.calls
    assert queue.list(origin.principal,row['id'])[0]['status']=='blocked'

@pytest.mark.anyio
async def test_queued_dispatch_preserves_new_draft_and_exact_image_snapshot(queued):
    import base64
    queue,workspace,origin,credentials,row,agent=queued
    image={'name':'original.png','mime':'image/png','data':base64.b64encode(b'original-image-bytes').decode()}
    queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='queued image',request_id='image-queued-request',attachments=[image])
    image['data']=base64.b64encode(b'changed-later').decode()
    current=workspace.session(origin.principal,row['id'],turns=False)
    workspace.update_session(origin.principal,row['id'],revision=current['revision'],changes={'draft_text':'unrelated unsent follow-up'})
    await queue.tick()
    assert agent.calls[0]['attachments'][0]['data']==base64.b64encode(b'original-image-bytes').decode()
    assert workspace.session(origin.principal,row['id'],turns=False)['draft_text']=='unrelated unsent follow-up'
    public=queue.list(origin.principal,row['id'])[0]
    assert 'data' not in public['attachments'][0]

@pytest.mark.anyio
async def test_interrupt_can_only_name_owned_current_conversation_task(queued):
    queue,workspace,origin,credentials,row,agent=queued
    task=await workspace.send(origin.principal,row['id'],prompt='active',request_id='original-request',managed_session_id=origin.session_id)
    other=workspace.create_session(origin.principal,title='Other',project_id='fixture-project',cwd=row['cwd'],provider_id='fixture',model='fixture')
    with pytest.raises(PermissionError):queue.enqueue(origin.principal,other['id'],sid=origin.session_id,prompt='interruption',request_id='interrupt-request',interrupt_task_id=task['id'])
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='interrupt original',request_id='interrupt-request',interrupt_task_id=task['id'])
    assert item['interrupt_task_id']==task['id'];await queue.tick();assert len(agent.calls)==1


def test_reading_anchor_is_durable_and_bound_to_its_canonical_conversation(queued):
    queue,workspace,origin,credentials,session,agent=queued
    owner=origin.principal
    task=workspace.agents.create_task(prompt='reader position',cwd=session['cwd'],provider_id='fixture',model='model-a',limits={},mode='ask')
    turn=workspace.agents.add_conversation_turn(session['id'],prompt='reader position',task_id=task['id'],mode='ask',provider_id='fixture',model='model-a')
    current=workspace.session(owner,session['id'],turns=False)
    saved=workspace.update_session(owner,session['id'],revision=current['revision'],changes={'scroll':5887,'scroll_anchor':{'id':turn['id']+':answer','offset':-15}})
    assert saved['scroll_anchor']=={'id':turn['id']+':answer','offset':-15}
    with pytest.raises(ValueError,match='does not belong'):
        workspace.update_session(owner,session['id'],revision=saved['revision'],changes={'scroll_anchor':{'id':'unknown-turn','offset':-15}})
    with pytest.raises(ValueError,match='Invalid'):
        workspace.update_session(owner,session['id'],revision=saved['revision'],changes={'scroll_anchor':{'id':turn['id'],'offset':float('nan')}})


@pytest.mark.anyio
async def test_queue_policy_revision_changes_block_without_dispatch_and_require_new_review(queued):
    queue,workspace,origin,credentials,row,agent=queued
    rule=workspace.agents.create_policy_rule(effect='allow',scope_type='project',scope_id=row['project_id'],action_type='tool',tool='read_file',fingerprint='fixture-target',expires_at=queue.clock()+300,consent_binding={'principal_id':origin.principal.id,'policy_version':origin.principal.policy_version})
    item=queue.enqueue(origin.principal,row['id'],sid=origin.session_id,prompt='Inspect policy-bound target',request_id='policy-bind-01')
    workspace.agents.edit_policy_consent(rule['id'],version=rule['version'],effect='deny',expires_at=queue.clock()+300,validate=lambda _:None)
    await queue.tick()
    blocked=queue.store.get('prompt_queue',item['id']);assert blocked['status']=='blocked' and not agent.calls
    current=queue.current(origin.principal,item['id']);assert current['target_digest']!=item['target_digest']
    queue.renew(origin.principal,item['id'],sid=origin.session_id,revision=blocked['revision'],target_digest=current['target_digest'])
    await queue.tick();assert len(agent.calls)==1


def test_queue_rule_binding_ignores_foreign_consent_and_contains_no_operation_details(queued):
    queue,workspace,origin,credentials,row,agent=queued
    before=queue.binding(origin.principal,row['id'])
    workspace.agents.create_policy_rule(effect='allow',scope_type='project',scope_id=row['project_id'],action_type='tool',tool='read_file',fingerprint='secret-target',matcher={'private':'must-not-leave'},display='must-not-leave',consent_binding={'principal_id':'foreign'})
    assert queue.binding(origin.principal,row['id'])==before
    rule=workspace.agents.create_policy_rule(effect='allow',scope_type='host',scope_id=None,action_type='tool',tool='read_file',fingerprint='legacy-target',display='must-not-leave')
    binding=queue.binding(origin.principal,row['id']);assert binding!=before and 'must-not-leave' not in str(binding)
    workspace.agents.revoke_policy_rule(rule['id']);assert queue.binding(origin.principal,row['id'])!=binding


def test_queue_reviewer_account_rotation_requires_new_binding_without_exposing_credentials(queued,tmp_path):
    from termx.browser.storage import Records
    from termx.agent.secrets import CredentialStore
    queue,workspace,origin,credentials,row,agent=queued
    records=Records(tmp_path/'browser');workspace.state.browser=SimpleNamespace(records=records)
    workspace.state.credentials=CredentialStore(memory={'reviewer-fixture':'first-private-key'})
    workspace.agents.put_provider('reviewer-fixture',kind='openai',name='Reviewer',base_url='https://fixture.invalid',model='fixture',capabilities=[],secret_configured=True)
    records.put('reviewer-evaluation','evaluation',{'id':'evaluation','created_at':queue.clock()})
    records.put('reviewer-config','active',{'provider_id':'reviewer-fixture','evaluation_id':'evaluation','model':'fixture','version':'v1','configuration_hash':'safe-revision'})
    before=queue.binding(origin.principal,row['id'])
    workspace.state.credentials.set('reviewer-fixture','rotated-private-key')
    after=queue.binding(origin.principal,row['id']);assert after!=before
    assert 'private-key' not in str(after) and 'fixture.invalid' not in str(after)
    records.delete('reviewer-config','active');assert queue.binding(origin.principal,row['id'])!=after
