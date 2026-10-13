"""Real Chromium acceptance for explicitly scoped unattended browser authority.

The model/reviewer are deterministic fixtures. Grants, delegation validation,
managed pages, request isolation and effect execution use production services.
"""
import asyncio,os
from time import time
import pytest
from termx.app import AppState,create_app
from termx.agent.providers import ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.auto_review import ReviewVerdict,canonical_hash
from termx.browser.network import NetworkPolicy
from termx.browser.tools import BrowserToolAdapter
from test_managed_browser import website

pytestmark=[pytest.mark.anyio,pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='real managed Chromium artifact required')]
@pytest.fixture
def anyio_backend():return 'asyncio'

class Adapter:
    async def test(self):return 'No-network fixture'
    async def plan(self,prompt,cwd,manifest):return {'summary':prompt,'steps':['Inspect permitted page'],'tools':['browser_observe'],'risks':[]},'fixture-plan'
    async def turn(self,**kwargs):return ProviderTurn('fixture','Complete',[],{},[])
class Reviewer:
    version='scripted-allow-fixture'
    def __init__(self,site):self.site=site
    async def evaluate(self,action,context):
        allow=action.tool_id=='browser.click' and action.target==self.site and action.intended_effect=='edit' and action.canonical_args_hash==canonical_hash({'selector':'#preference'})
        return ReviewVerdict('ALLOW' if allow else 'BLOCK','Exact native checkbox fixture only','',self.version,time()+30)

@pytest.fixture
async def browser_runtime(tmp_path):
    from termx.workspace.scheduled_browser import ScheduledBrowserPermissions
    server,site=await website();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:Adapter());create_app(state)
    state.scheduled_browser=ScheduledBrowserPermissions(state.workspace);state.browser.delegation_valid=state.scheduled_browser.valid;state.browser.delegation_release=state.scheduled_browser.release;state.browser.delegation_completed=state.scheduled_browser.completed;state.browser.network=NetworkPolicy([site]);state.browser.review.reviewer=Reviewer(site);state.browser.review.reviewer_valid=lambda:True
    owner=state.identity.setup_owner('scheduled-browser-owner','scheduled-browser-password-123');credentials=await state.identity.login('local-password',{'username':'scheduled-browser-owner','password':'scheduled-browser-password-123'},peer='browser-fixture')
    folder=tmp_path/'project';folder.mkdir();project=state.projects.register(str(folder),name='Scheduled browser fixture');state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='not-a-provider-key')
    conversation=state.workspace.create_session(owner,project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent');goal=state.automation.goal(owner,conversation_id=conversation['id'],success_criteria='Inspect exact permitted fixture tab',max_runs=3,limits=None);grant=state.automation.grant(owner,goal_id=goal['id'],expires_at=time()+1800,max_runs=3,limits=None)
    profile=state.browser.create_profile(owner.id,project['id'],'Explicit fixture profile');state.authorization.claim_principal(owner,'browser-profile',profile['id'],project_id=project['id'])
    tab=await state.browser.create_tab(owner.id,credentials.session_id,profile['id'],site);await state.browser._pages[tab['id']].evaluate("()=>document.body.insertAdjacentHTML('beforeend','<label><input id=preference type=checkbox>Fixture preference</label>')");state.authorization.claim_principal(owner,'browser-tab',tab['id'],project_id=project['id'])
    yield state,owner,credentials,conversation,grant,tab,site
    await state.browser.close();await state.automation.close();await state.workspace.close();await state.agent.close();state.workspace.store.close();state.agent_store.close();server.close();await server.wait_closed()

def selection(tab,site):return {'tabs':[{'tab_id':tab['id'],'origins':[site],'actions':['observe','click','navigate']}],'expires_at':time()+600,'max_seconds':60}
async def activate(runtime):
    state,owner,_,conversation,grant,tab,site=runtime
    await state.scheduled_browser.authorize(owner,grant['id'],selection(tab,site))
    task=await state.workspace.send(owner,conversation['id'],prompt='Inspect scheduled page',request_id='scheduled-browser-activation',delegation_id=grant['id'])
    if not state.browser.get(tab['id'],owner.id).get('grant_id'):await state.scheduled_browser.activate(task['id'])
    current=state.browser.get(tab['id'],owner.id);active=state.browser.records.get('grant',current['grant_id']);return task,active
async def click(runtime,task,active,identifier='allowed-click'):
    state,owner,_,_,_,tab,_=runtime;current=state.browser.get(tab['id'],owner.id)
    return await BrowserToolAdapter(state.browser).execute('browser_action',{'tab_id':tab['id'],'action':'click','args':{'selector':'#preference'},'document_revision':current['document_revision'],'lease_revision':current['lease_revision']},task_id=task['id'],principal_id=owner.id,session_id=None,call_id=identifier,authority=lambda:state.scheduled_browser.valid(active))

async def test_unattended_authority_is_distinct_from_interactive_session_and_survives_client_logout(browser_runtime):
    state,owner,credentials,_,_,tab,_=browser_runtime;task,active=await activate(browser_runtime);assert active.get('delegation_id') and active.get('session_id') is None
    state.identity.revoke(credentials.session_id,owner.id)
    def no_interactive_session(*args):raise AssertionError('Unattended execution must not fabricate an interactive session')
    state.browser.session_valid=no_interactive_session
    await click(browser_runtime,task,active);assert await state.browser._pages[tab['id']].locator('#preference').is_checked()

@pytest.mark.parametrize('change',['revoke','expiry','disabled','policy','takeover','private'])
async def test_changed_authority_blocks_real_page_effect_before_execution(browser_runtime,change):
    state,owner,_,_,grant,tab,_=browser_runtime;task,active=await activate(browser_runtime)
    if change=='revoke':
        row=state.workspace.store.get('delegation',grant['id']);state.workspace.store.update('delegation',grant['id'],{**row,'revoked':True})
    elif change=='expiry':
        row=state.browser.records.get('grant',active['id']);state.browser.records.put('grant',active['id'],{**row,'expires_at':time()-1})
    elif change=='disabled':state.identity.disable(owner.id)
    elif change=='policy':state.identity.set_scopes(owner.id,['agent-view'])
    elif change=='takeover':state.browser.takeover(tab['id'],owner.id)
    else:state.browser.takeover(tab['id'],owner.id,private=True)
    with pytest.raises((PermissionError,KeyError,ValueError)):await click(browser_runtime,task,active,'blocked-'+change)
    assert not await state.browser._pages[tab['id']].locator('#preference').is_checked()

@pytest.mark.parametrize('bad',['duplicate','missing_observe','lifecycle','foreign','wrong_origin','too_long','past_expiry'])
async def test_consent_rejects_escalating_or_ambiguous_selection(browser_runtime,bad):
    state,owner,_,_,grant,tab,site=browser_runtime;scope=selection(tab,site)
    if bad=='duplicate':scope['tabs']*=2
    elif bad=='missing_observe':scope['tabs'][0]['actions']=['click']
    elif bad=='lifecycle':scope['tabs'][0]['actions'].append('open_tab')
    elif bad=='wrong_origin':scope['tabs'][0]['origins']=['http://127.0.0.1:1']
    elif bad=='too_long':scope['max_seconds']=3601
    elif bad=='past_expiry':scope['expires_at']=time()-1
    else:
        alien=state.identity.create_local_user('browser-foreign','foreign-browser-password-123',list(owner.scopes));profile=state.browser.create_profile(alien.id,tab['project_id'],'Foreign browser');foreign=await state.browser.create_tab(alien.id,'foreign-session',profile['id'],site);scope['tabs'][0]['tab_id']=foreign['id']
    with pytest.raises((PermissionError,KeyError,ValueError)):await state.scheduled_browser.authorize(owner,grant['id'],scope)
    assert not state.browser.get(tab['id'],owner.id).get('grant_id')

async def test_unselected_tab_and_origin_cannot_borrow_scheduled_grant(browser_runtime):
    state,owner,credentials,_,_,tab,site=browser_runtime;task,active=await activate(browser_runtime);alternate=await state.browser.create_tab(owner.id,credentials.session_id,tab['profile_id'],site)
    with pytest.raises(PermissionError):await BrowserToolAdapter(state.browser).execute('browser_observe',{'tab_id':alternate['id']},task_id=task['id'],principal_id=owner.id,session_id=None,call_id='unselected')
    current=state.browser.get(tab['id'],owner.id)
    with pytest.raises(PermissionError):await BrowserToolAdapter(state.browser).execute('browser_action',{'tab_id':tab['id'],'action':'navigate','args':{'url':'https://outside.invalid'},'document_revision':current['document_revision'],'lease_revision':current['lease_revision']},task_id=task['id'],principal_id=owner.id,session_id=None,call_id='outside-origin')
    assert state.browser.get(tab['id'],owner.id)['url'].startswith(site)
async def test_activation_retry_is_idempotent_and_completed_run_releases_only_its_consented_lease(browser_runtime):
    state,owner,_,conversation,grant,tab,_=browser_runtime;task,active=await activate(browser_runtime)
    repeated=await state.scheduled_browser.activate(task['id']);assert len(repeated)==1 and repeated[0]['grant']['id']==active['id']
    await state.agent.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
    async def wait_released():
        while state.browser.get(tab['id'],owner.id)['state']!='human':await asyncio.sleep(.01)
    await asyncio.wait_for(wait_released(),3);assert state.agent_store.get_task(task['id'])['status']=='completed'
    consent=state.workspace.store.get('delegation',grant['id'])['browser_permission'];assert consent['tabs'][0]['lease_revision']==state.browser.get(tab['id'],owner.id)['lease_revision']
    second=await state.workspace.send(owner,conversation['id'],prompt='Second scheduled inspection',request_id='scheduled-browser-second-run',delegation_id=grant['id']);activated=await state.scheduled_browser.activate(second['id']);assert activated[0]['grant']['id']!=active['id'];assert activated[0]['grant']['session_id'] is None;assert activated[0]['grant']['run_id']==second['id']

@pytest.mark.parametrize('private',[False,True])
async def test_explicit_takeover_invalidates_consent_for_future_runs(browser_runtime,private):
    state,owner,_,conversation,grant,tab,_=browser_runtime;task,_=await activate(browser_runtime);state.browser.takeover(tab['id'],owner.id,private=private);state.agent.cancel(task['id'])
    second=await state.workspace.send(owner,conversation['id'],prompt='Must not regrant after takeover',request_id='scheduled-browser-after-takeover',delegation_id=grant['id'])
    with pytest.raises(PermissionError):await state.scheduled_browser.activate(second['id'])
    assert state.browser.get(tab['id'],owner.id)['state']==('private' if private else 'human')

async def test_delegation_revocation_during_review_prevents_checkbox_effect(browser_runtime):
    from termx.auto_review import ActionBlocked
    state,owner,_,_,grant,tab,site=browser_runtime;task,active=await activate(browser_runtime)
    class RevokingReviewer(Reviewer):
        async def evaluate(self,action,context):
            row=state.workspace.store.get('delegation',grant['id']);state.workspace.store.update('delegation',grant['id'],{**row,'revoked':True});return await super().evaluate(action,context)
    state.browser.review.reviewer=RevokingReviewer(site)
    with pytest.raises((PermissionError,ActionBlocked)):await click(browser_runtime,task,active,'review-race-click')
    assert not await state.browser._pages[tab['id']].locator('#preference').is_checked()

async def test_normal_interactive_handoff_remains_bound_to_the_real_login_session(browser_runtime):
    state,owner,credentials,conversation,_,tab,site=browser_runtime
    task=await state.workspace.send(owner,conversation['id'],prompt='Interactive inspection',request_id='ordinary-interactive-browser')
    handed=await state.browser.handoff(tab['id'],owner.id,credentials.session_id,run_id=task['id'],origins=[site],actions=['observe','click'],policy_version=owner.policy_version)
    assert not handed['grant'].get('delegation_id');assert handed['grant']['session_id']==credentials.session_id;assert state.browser._grant_valid(state.browser.get(tab['id'],owner.id),handed['grant'])
    state.identity.revoke(credentials.session_id,owner.id);assert not state.browser._grant_valid(state.browser.get(tab['id'],owner.id),handed['grant'])
async def restart_browser(state,site):
    from termx.browser.service import BrowserService
    from termx.workspace.scheduled_browser import ScheduledBrowserPermissions
    old=state.browser;await old.close();fresh=BrowserService(old.records.root,development_origins=[site],session_valid=old.session_valid);fresh.task_live=old.task_live;fresh.task_summary=old.task_summary;fresh.claim_tab=old.claim_tab;state.browser=fresh;state.agent.browser=fresh;state.scheduled_browser=ScheduledBrowserPermissions(state.workspace);fresh.delegation_valid=state.scheduled_browser.valid;fresh.delegation_release=state.scheduled_browser.release;fresh.delegation_completed=state.scheduled_browser.completed;fresh.review.reviewer=Reviewer(site);fresh.review.reviewer_valid=lambda:True

@pytest.mark.parametrize('change',['normal','takeover','private','closed'])
async def test_real_broker_restart_reopens_only_unmodified_human_consent(browser_runtime,change):
    state,owner,_,conversation,grant,tab,site=browser_runtime;await state.scheduled_browser.authorize(owner,grant['id'],selection(tab,site))
    if change in {'takeover','private'}:state.browser.takeover(tab['id'],owner.id,private=change=='private')
    elif change=='closed':await state.browser.close_tab(tab['id'],owner.id)
    await restart_browser(state,site);task=await state.workspace.send(owner,conversation['id'],prompt='Post-restart browser inspection',request_id='scheduled-browser-restarted',delegation_id=grant['id'])
    if change!='normal':
        with pytest.raises(PermissionError):await state.scheduled_browser.activate(task['id'])
        assert not state.browser._pages
    else:
        result=await state.scheduled_browser.activate(task['id']);assert len(result)==1;reopened=result[0]['tab'];assert reopened['id']!=tab['id'];assert reopened['profile_id']==tab['profile_id'];assert reopened['url'].startswith(site);assert result[0]['grant']['session_id'] is None
        observed=await BrowserToolAdapter(state.browser).execute('browser_observe',{'tab_id':reopened['id']},task_id=task['id'],principal_id=owner.id,session_id=None,call_id='restart-observe');assert observed['elements']
        ownership=state.authorization.resource_owner('browser-tab',reopened['id']);assert ownership['principal_id']==owner.id
async def test_completed_run_shutdown_before_monitor_can_resume_explicit_browser_consent(browser_runtime):
    state,owner,_,conversation,grant,tab,site=browser_runtime;task,_=await activate(browser_runtime)
    state.browser._monitor.cancel();await asyncio.gather(state.browser._monitor,return_exceptions=True);state.browser._monitor=None
    await state.agent.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
    async def completed():
        while state.agent_store.get_task(task['id'])['status']!='completed':await asyncio.sleep(.005)
    await asyncio.wait_for(completed(),3);assert state.browser.get(tab['id'],owner.id)['state']=='agent'
    await restart_browser(state,site);second=await state.workspace.send(owner,conversation['id'],prompt='Resume explicit browser scope after completion shutdown race',request_id='scheduled-browser-shutdown-race',delegation_id=grant['id']);result=await state.scheduled_browser.activate(second['id']);assert result[0]['tab']['profile_id']==tab['profile_id'];assert result[0]['grant']['session_id'] is None

async def test_restart_bootstrap_redirect_cannot_contact_another_origin(browser_runtime):
    state,owner,credentials,conversation,grant,tab,site=browser_runtime;sink_requests=[];sink,sink_origin=await website(requests=sink_requests);redirect=[False]
    async def handler(reader,writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            if redirect[0]:writer.write(f'HTTP/1.1 302 Found\r\nLocation: {sink_origin}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())
            else:
                body=b'<html><title>Authorized source</title><h1>Source origin</h1></html>';writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
            await writer.drain()
        finally:writer.close()
    source=await asyncio.start_server(handler,'127.0.0.1',0);source_origin=f'http://127.0.0.1:{source.sockets[0].getsockname()[1]}'
    try:
        state.browser.network=NetworkPolicy([site,source_origin,sink_origin]);selected=await state.browser.create_tab(owner.id,credentials.session_id,tab['profile_id'],source_origin);state.browser.claim_tab(owner.id,selected)
        await state.scheduled_browser.authorize(owner,grant['id'],selection(selected,source_origin));await restart_browser(state,site);state.browser.network=NetworkPolicy([site,source_origin,sink_origin]);redirect[0]=True
        task=await state.workspace.send(owner,conversation['id'],prompt='Scoped restarted source',request_id='scheduled-browser-bootstrap-redirect',delegation_id=grant['id'])
        with pytest.raises(Exception):await state.scheduled_browser.activate(task['id'])
        await asyncio.sleep(.05);assert sink_requests==[];assert not any(row['state']=='agent' for row in state.browser.records.list('tab'))
    finally:source.close();sink.close();await source.wait_closed();await sink.wait_closed()
