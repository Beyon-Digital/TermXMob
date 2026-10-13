"""Live daemon executes a real managed-Chromium schedule after client closes.

A deterministic scripted provider lists/observes/clicks via actual agent tools;
no inference vendor is contacted. A scoped scripted reviewer permits only the
fixture native checkbox edit through unchanged production DecisionBroker.
Browser consent is issued over authenticated same-origin HTTP. No per-run
interactive handoff or synthetic interactive session is created.
"""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading,traceback
from pathlib import Path
from time import time,monotonic,sleep
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import sync_playwright
from termx.agent.providers import ProviderCall,ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.auto_review import ReviewVerdict,canonical_hash

class ScriptedModel:
    def __init__(self):self.stages={};self.count=0;self.trace=[]
    async def test(self):return 'No-network scripted browser fixture'
    async def plan(self,prompt,cwd,manifest):return {'summary':prompt,'steps':['List permitted tab','Observe current page','Check fixture preference'],'tools':['browser_tabs','browser_observe','browser_action'],'risks':[]},'fixture-plan'
    async def turn(self,**kwargs):
        outputs={row['call_id']:json.loads(row['output']) for row in kwargs.get('input_items') or [] if row.get('type')=='function_call_output'};phase=3 if 'clicked' in outputs else 2 if 'observed' in outputs else 1 if 'listed' in outputs else 0;calls=[]
        if phase==0:calls=[ProviderCall('function','listed','browser_tabs',{})]
        elif phase==1:
            tabs=outputs['listed']['tabs'];assert len(tabs)==1,tabs;calls=[ProviderCall('function','observed','browser_observe',{'tab_id':tabs[0]['id']})]
        elif phase==2:
            observed=outputs['observed'];calls=[ProviderCall('function','clicked','browser_action',{'tab_id':observed['tab_id'],'action':'click','args':{'selector':'#preference'},'document_revision':observed['document_revision'],'lease_revision':observed['lease_revision']})]
        self.count+=1;response='scripted-response-'+str(self.count);self.stages[response]=phase+1;self.trace.extend(call.name for call in calls)
        return ProviderTurn(response,'Fixture preference updated' if not calls else '',calls,{},[])
class ScopedReviewer:
    version='exact-native-checkbox-fixture'
    def __init__(self,site):self.site=site;self.actions=[]
    async def evaluate(self,action,context):
        allow=(action.tool_id=='browser.click' and action.target==self.site and action.intended_effect=='edit' and action.canonical_args_hash==canonical_hash({'selector':'#preference'}))
        self.actions.append({'tool':action.tool_id,'origin':action.target,'effect':action.intended_effect,'allowed':allow})
        return ReviewVerdict('ALLOW' if allow else 'BLOCK','Exact permitted local checkbox fixture only','',self.version,time()+30)

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'live-daemon-scheduled-managed-chromium-after-client-close','transport':'loopback','model':'scripted-no-network-provider','reviewer':'scoped-scripted-checkbox-only','external_model_calls':0,'external_browser_attached':False,'checks':[]}
    with tempfile.TemporaryDirectory(prefix='termx-scheduled-browser-live-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<!doctype html><html><title>Unattended browser fixture</title><style>body{font:22px system-ui;padding:50px}input{width:30px;height:30px}</style><h1>Explicit scheduled preference</h1><label><input id="preference" type="checkbox">Fixture preference</label><p>This native checkbox has no network or external side effect.</p></html>';self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        site=ThreadingHTTPServer(('127.0.0.1',0),Site);site_thread=threading.Thread(target=site.serve_forever,daemon=True);site_thread.start();origin=f'http://127.0.0.1:{site.server_port}'
        from termx.app import AppState,create_app
        from termx.browser.network import NetworkPolicy
        model=ScriptedModel();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:model);owner=state.identity.setup_owner('browser-daemon-owner','browser-daemon-password-123');folder=root/'project';folder.mkdir();project=state.projects.register(str(folder),name='Unattended browser proof');state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Scripted browser fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='not-a-provider-key')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist');state.browser.network=NetworkPolicy([origin]);reviewer=ScopedReviewer(origin);state.browser.review.reviewer=reviewer;state.browser.review.reviewer_valid=lambda:True
        conversation=state.workspace.create_session(owner,title='Scheduled browser live proof',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];base=f'http://127.0.0.1:{port}';host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));holder={}
        async def serve():holder['loop']=asyncio.get_running_loop();await host.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        def wait(predicate,label):
            deadline=monotonic()+45
            while not predicate():
                if monotonic()>deadline:raise AssertionError(label)
                sleep(.05)
        def execute(coro):return asyncio.run_coroutine_threadsafe(coro,holder['loop']).result(30)
        wait(lambda:host.started,'Host did not start')
        try:
            with sync_playwright() as playwright:
                initiating_browser=playwright.chromium.launch(headless=True);context=initiating_browser.new_context();page=context.new_page();page.goto(base)
                login=page.request.post(base+'/auth/login',headers={'Origin':base},data={'username':'browser-daemon-owner','password':'browser-daemon-password-123','transport':'cookie'});assert login.status==200,login.text();csrf=login.json()['csrf_token'];headers={'Origin':base,'X-Termx-CSRF':csrf}
                profile_response=page.request.post(base+'/api/browser/profiles',headers=headers,data={'project_id':project['id'],'name':'Explicit unattended profile'});assert profile_response.status==200,profile_response.text();profile=profile_response.json()
                tab_response=page.request.post(base+'/api/browser/tabs',headers=headers,data={'profile_id':profile['id'],'url':origin});assert tab_response.status==200,tab_response.text();tab=tab_response.json()
                eligible=page.request.get(base+'/api/workspace/scheduled-browser/tabs?conversation_id='+conversation['id']);assert eligible.status==200 and any(row['id']==tab['id'] for row in eligible.json()['tabs'])
                payload={'request_id':'live-browser-schedule-0001','conversation_id':conversation['id'],'success_criteria':'Check the exact fixture preference through allowed tab','max_runs':1,'limits':{'max_steps':16,'max_seconds':60,'shell_timeout_s':10,'max_parallel_subagents':1,'max_subagents_total':1},'prompt':'Check the authorized fixture preference','spec':{'kind':'once','at':time()+5},'grant_days':1,'missed':'once','overlap':'skip','browser_permission':{'tabs':[{'tab_id':tab['id'],'origins':[origin],'actions':['observe','click']}],'expires_at':time()+600,'max_seconds':60}}
                consent=page.request.post(base+'/api/workspace/automations',headers=headers,data=payload);assert consent.status==200,consent.text();schedule=consent.json();assert state.workspace.store.get('delegation',schedule['grant_id'])['browser_permission']['tabs'][0]['tab_id']==tab['id'];assert not model.trace
                report['checks'].append('Signed-in initiating Chromium client explicitly authorizes exact existing tab, origin, observe/click actions, expiry, per-run browser duration and bounded execution schedule through HTTP')
                page.close();context.close();initiating_browser.close();report['initiating_browser_closed_before_dispatch']=True;closed_at=time()
            wait(lambda:bool(state.workspace.store.list('schedule_run')),'Actual daemon cadence never dispatched schedule');run_record=state.workspace.store.list('schedule_run')[0];wait(lambda:bool((state.workspace.store.get('schedule_run',run_record['id']) or {}).get('task_id')),'Daemon did not enroll task');task_id=state.workspace.store.get('schedule_run',run_record['id'])['task_id'];wait(lambda:state.agent_store.get_task(task_id)['status'] in {'completed','failed','cancelled'},'Agent did not reach terminal state');task=state.agent_store.get_task(task_id);assert task['status']=='completed',task;assert task['created_at']>=closed_at
            managed_page=state.browser._pages[tab['id']];checked=execute(managed_page.locator('#preference').is_checked());assert checked;execute(managed_page.screenshot(path=str(destination/'daemon-browser-preference-checked.png')))
            events=state.agent_store.events(task_id);assert model.trace==['browser_tabs','browser_observe','browser_action'],model.trace;assert not any(event['type']=='task.handoff.required' for event in events);active_grants=[grant for grant in state.browser.records.list('grant') if grant['run_id']==task_id];assert active_grants and all(grant.get('session_id') is None and grant.get('delegation_id')==schedule['grant_id'] for grant in active_grants)
            report['checks'].append('Real daemon scheduler dispatches after initiating browser closes; scripted provider obtains actual browser_tabs and browser_observe results before proposing real browser_action click')
            report['checks'].append('Production DecisionBroker admits only the exact native checkbox edit; managed Chromium checkbox becomes checked without per-run human handoff or synthetic interactive session')
            report.update(status='passed',task_id=task_id,schedule_id=schedule['id'],initiating_client_closed_at=closed_at,task_created_at=task['created_at'],actual_tool_calls=model.trace,reviewed_actions=reviewer.actions,checkbox_checked=True,browser_grants=[{'delegation_id':grant.get('delegation_id'),'session_id':grant.get('session_id'),'origins':grant['origins'],'actions':grant['actions']} for grant in active_grants])
        except BaseException as error:
            report.update(status='failed',error=str(error),traceback=traceback.format_exc(),actual_tool_calls=model.trace)
            if 'task_id' in locals():report['task']=state.agent_store.get_task(task_id);report['event_types']=[event['type'] for event in state.agent_store.events(task_id)]
            raise
        finally:host.should_exit=True;worker.join(15);site.shutdown();site.server_close();(destination/'report.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else '/workspace/termx-live-proof/scheduled-browser')
