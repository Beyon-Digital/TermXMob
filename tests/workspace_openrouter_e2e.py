"""Opt-in real OpenRouter proof against an isolated TermX HTTP host.

Run with this repository's Python environment:
    python tests/workspace_openrouter_e2e.py OUTPUT_DIR

Provision OPENROUTER_API_KEY through the environment's secret settings first.
Only the fixed :free model is allowed; the sixth provider request is refused.
No models are mocked and no credential is written to the proof artifacts.

The team is created by authenticated HTTP /swarm requests. This does not prove
model-initiated spawn_subagent calls, remote Internet access, browser/computer
reasoning, or execution after a host restart. It checks real model/tool work,
continued execution after client disconnect, and HTTP team control separately.
"""
from __future__ import annotations
import argparse,asyncio,json,os,secrets,socket,sys,tempfile,threading,time,traceback
from pathlib import Path
import httpx,uvicorn
REPO_ROOT=Path(__file__).resolve().parents[1]
MODEL='google/gemma-4-31b-it:free'
TERMINAL={'completed','failed','cancelled','recovery_confirmation_required','awaiting_approval','paused'}
PROVIDER_URL='https://openrouter.ai/api/v1/responses'
REQUEST_LIMIT=5

def run(destination):
    destination=Path(destination).expanduser().resolve()
    destination.mkdir(parents=True,exist_ok=True)
    if not os.environ.get('OPENROUTER_API_KEY'):
        report={'status':'awaiting_credential','required_secret':'OPENROUTER_API_KEY','provider_requests':0,'automatic_model_delegation_verified':False}
        (destination/'openrouter-report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
        return 2
    report={'proof':'real-internal-openrouter-host-http','provider_mocks':False,'model':MODEL,'provider_requests':[],'checks':[],'python_executable':sys.executable,'team_creation':'authenticated HTTP /swarm','automatic_model_delegation_verified':False,'scope':'loopback HTTP; no host restart, public remote client, or model browser/computer proof'}
    with tempfile.TemporaryDirectory(prefix='termx-openrouter-live-') as temporary:
        root=Path(temporary);project_dir=root/'project';project_dir.mkdir()
        (project_dir/'inventory.csv').write_text('item,quantity,price\nbolts,7,3\nnuts,11,2\nwashers,5,4\n')
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.providers import ProviderError
        state=AppState(passcode=None,credentials=CredentialStore(memory={}))
        state.store.update_agent({'provider_timeout_s':90})
        state.agent.save_provider(provider_id='openrouter-live-free',kind='openai-compatible',name='OpenRouter free live proof',base_url='https://openrouter.ai/api/v1',model=MODEL,capabilities=['shell','functions'],api_key=os.environ['OPENROUTER_API_KEY'])
        metered_clients=set()
        async def meter_request(request):
            payload=json.loads(request.content)
            if str(request.url)!=PROVIDER_URL or payload.get('model')!=MODEL or not MODEL.endswith(':free'):
                raise ProviderError('Live proof refuses a non-free model or unexpected endpoint')
            if payload.get('store') is not False or payload.get('previous_response_id') or payload.get('max_output_tokens',2200)>2200:
                raise ProviderError('Live proof requires bounded stateless Responses requests')
            if len(report['provider_requests'])>=REQUEST_LIMIT:
                raise ProviderError('Live proof provider request ceiling reached (5)')
            request.extensions['proof_request_index']=len(report['provider_requests'])
            report['provider_requests'].append({'index':len(report['provider_requests'])+1,'model':payload['model'],'stream':bool(payload.get('stream')),'store':payload.get('store'),'time':time.time()})
        async def meter_response(response):
            report['provider_requests'][response.request.extensions['proof_request_index']]['http_status']=response.status_code
        def factory(provider,key):
            adapter=state.agent._default_adapter(provider,key)
            if id(adapter.client) not in metered_clients:
                adapter.client.event_hooks['request'].append(meter_request)
                adapter.client.event_hooks['response'].append(meter_response)
                metered_clients.add(id(adapter.client))
            return adapter
        state.agent._adapter_factory=factory
        fixture_password=secrets.token_urlsafe(24)
        state.identity.setup_owner('live-owner',fixture_password)
        project=state.projects.register(str(project_dir),name='Live free model fixture')
        app=create_app(state)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        worker=threading.Thread(target=lambda:asyncio.run(host.serve(sockets=[listener])),daemon=True);worker.start()
        task_ids=[]
        def wait(predicate,label,timeout=180):
            deadline=time.monotonic()+timeout
            while not predicate():
                if time.monotonic()>deadline:raise RuntimeError(label+' timed out')
                time.sleep(.2)
        def checked(response):
            if response.status_code>=400:raise RuntimeError(f'HTTP {response.status_code}: {response.text[:1500]}')
            return response.json()
        try:
            wait(lambda:host.started,'host startup',40);base=f'http://127.0.0.1:{port}'
            with httpx.Client(base_url=base,trust_env=False,timeout=100) as client:
                assert client.get('/api/workspace/sessions').status_code==401
                login=checked(client.post('/auth/login',json={'username':'live-owner','password':fixture_password,'transport':'bearer'}))
                auth={'Authorization':'Bearer '+login['access_token']};client.headers.update(auth)
                conversation=checked(client.post('/api/workspace/sessions',json={'title':'Live tool execution','project_id':project['id'],'cwd':str(project_dir),'engine':'internal','provider_id':'openrouter-live-free','model':MODEL,'mode':'ask','run_limits':{'max_steps':3,'max_seconds':180,'shell_timeout_s':10,'max_parallel_subagents':2,'max_subagents_total':2}}))
                task=checked(client.post(f'/api/workspace/sessions/{conversation["id"]}/turns',json={'request_id':'openrouter-read-file-001','prompt':'Verify the actual inventory.csv file. Call run_shell exactly once with command cat inventory.csv (no other shell commands or tools). Then calculate the sum of quantity times price for all data rows and return the exact total with each row calculation. Do not answer until you have the real tool output. Keep the answer concise.'}))
                task_ids.append(task['id']);report['reader_id']=task['id']
                assert state.agent_store.get_task(task['id'])['status'] not in TERMINAL, 'Task finished before client disconnect; persistence was not exercised'
            report['client_disconnected_at']=time.time()
            # No connected HTTP client remains while real inference/tool work runs.
            wait(lambda:(state.agent_store.get_task(task['id']) or {}).get('status') in TERMINAL,'real file-reading task')
            finished=state.agent_store.get_task(task['id'])
            events=state.agent_store.events(task['id'])
            report['reader']={k:finished.get(k) for k in ('id','status','result','error')}
            if finished['status']!='completed':raise RuntimeError('Real model task did not complete: '+str(finished.get('error')))
            assert '63' in str(finished.get('result')),finished.get('result')
            assert finished['updated_at']>=report['client_disconnected_at'], 'Completion preceded client disconnect'
            assert any(e['type']=='provider.stream.completed' for e in events),'No completed real provider stream'
            assert any(e['type']=='process.exited' and e['payload'].get('exit_code')==0 for e in events),'No actual successful shell process'
            assert any('bolts,7,3' in json.dumps(e['payload']) for e in events),'No observed fixture output'
            assert len(report['provider_requests'])<=2,'File-reading proof spent the remaining team request allowance'
            report['checks'].append('Real free model called a real read-only shell tool, consumed its output, and completed after HTTP client disconnected')
            with httpx.Client(base_url=base,trust_env=False,timeout=100,headers=auth) as client:
                assert checked(client.get(f'/api/workspace/tasks/{task["id"]}'))['status']=='completed'
                conversation=checked(client.post('/api/workspace/sessions',json={'title':'Live worker team','project_id':project['id'],'cwd':str(project_dir),'engine':'internal','provider_id':'openrouter-live-free','model':MODEL,'mode':'agent','run_limits':{'max_steps':6,'max_seconds':240,'shell_timeout_s':10,'max_parallel_subagents':2,'max_subagents_total':2}}))
                parent=checked(client.post(f'/api/workspace/sessions/{conversation["id"]}/turns',json={'request_id':'openrouter-team-parent-001','prompt':'Plan a two-worker arithmetic verification exercise. One worker will calculate 7 times 3 and another 11 times 2. Do not change files or access network. Wait for user approval before executing.'}))
                task_ids.append(parent['id']);assert parent['status']=='awaiting_approval',parent
                swarm=checked(client.post(f'/api/workspace/tasks/{parent["id"]}/swarm',json={'request_id':'openrouter-two-workers-001','members':[{'agent':'Multiply seven','task':'Calculate 7 multiplied by 3. No tools are needed or permitted for this arithmetic task. Return the computed integer in the exact plain-text format WORKER_A=<integer>.','instructions':'Answer directly in one response without tools.','mode':'ask'},{'agent':'Multiply eleven','task':'Calculate 11 multiplied by 2. No tools are needed or permitted for this arithmetic task. Return the computed integer in the exact plain-text format WORKER_B=<integer>.','instructions':'Answer directly in one response without tools.','mode':'ask'}]}))
                assert swarm['ok'],swarm
                children=[member['task_id'] for member in swarm['members']];task_ids+=children
                steer_message='Keep all work read-only; the remote operator is watching.'
                checked(client.post(f'/api/workspace/tasks/{parent["id"]}/steer',json={'message':steer_message}))
                assert any(e['type']=='task.steered' and e['payload'].get('message')==steer_message for e in state.agent_store.events(parent['id']))
                report['swarm']={'parent_id':parent['id'],'children':children,'creation':'authenticated HTTP team assembly'}
            wait(lambda:all(state.agent_store.get_task(child)['status'] in TERMINAL for child in children),'two live model workers')
            for child,expected in zip(children,['WORKER_A=21','WORKER_B=22']):
                row=state.agent_store.get_task(child)
                assert row['status']=='completed',row.get('error')
                assert expected in str(row.get('result')),row.get('result')
            with httpx.Client(base_url=base,trust_env=False,timeout=30,headers=auth) as remote:
                tree=checked(remote.get(f'/api/workspace/tasks/{parent["id"]}'))
                assert len(tree['children'])==2
                assert tree['status']=='awaiting_approval','Workers must not implicitly approve coordinator execution'
                checked(remote.post(f'/api/workspace/tasks/{parent["id"]}/cancel'))
                assert checked(remote.get(f'/api/workspace/tasks/{parent["id"]}'))['status']=='cancelled'
            report['checks'].append('Two real model workers completed independently under a manually HTTP-created and supervised team')
            report['checks'].append('Remote steering persisted and remote cancellation stopped the awaiting-approval coordinator without approving it')
            report['status']='passed'
        except BaseException as error:
            report['status']='failed';report['error']=str(error);report['traceback']=traceback.format_exc()
        finally:
            report['tasks']=[{k:state.agent_store.get_task(tid).get(k) for k in ('id','status','mode','parent_id','result','error')} for tid in task_ids]
            for tid in task_ids:
                (destination/(tid+'-events.json')).write_text(json.dumps(state.agent_store.events(tid),indent=2))
            host.should_exit=True;worker.join(20)
            report['host_shutdown_complete']=not worker.is_alive()
            if worker.is_alive():
                report['status']='failed';report['shutdown_error']='Host did not finish cleanup within 20 seconds'
            report['request_count']=len(report['provider_requests'])
            (destination/'openrouter-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='passed' else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',nargs='?',type=Path,default=REPO_ROOT/'scratch'/'openrouter-proof',help='Directory for the report and task event artifacts')
    args=parser.parse_args()
    return run(args.destination)

if __name__=='__main__':raise SystemExit(main())
