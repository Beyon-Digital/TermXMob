"""Opt-in real-host remote-control transport proof with independent clients.

Transport is live loopback HTTP and graphql-transport-ws, not an external phone,
WAN tunnel, or paid model. The provider is deterministic/no-network; the actual
host manager, auth, ownership checks, delegation, isolation, replay and controls
run unchanged. Evidence never records session credentials.
"""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading,traceback
from pathlib import Path
from time import monotonic,sleep
import httpx,uvicorn,websockets
from websockets.exceptions import ConnectionClosed
from termx.agent.providers import ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.tokens import SCOPES

class Adapter:
    def __init__(self):self.release=threading.Event()
    async def test(self):return 'No-network remote-control fixture'
    async def plan(self,prompt,cwd,manifest):return {'summary':prompt,'steps':['Inspect enrolled fixture'],'tools':[],'risks':[]},'remote-plan'
    async def turn(self,**kwargs):
        while not self.release.is_set():await asyncio.sleep(.05)
        return ProviderTurn('remote-fixture','Fixture complete',[],{},[])

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'independent-clients-live-authenticated-http-websocket','transport':'loopback','provider':'deterministic-no-network-fixture','external_phone_tested':False,'wan_tested':False,'paid_model_calls':0,'checks':[]}
    with tempfile.TemporaryDirectory(prefix='termx-remote-proof-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        adapter=Adapter();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:adapter)
        owner=state.identity.setup_owner('remote-owner','remote-fixture-password-123');state.identity.create_local_user('foreign-user','foreign-fixture-password-123',list(SCOPES));folder=root/'project';folder.mkdir();(folder/'read.txt').write_text('Remote fixture only\n');project=state.projects.register(str(folder),name='Remote proof')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network remote fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='fixture-not-a-provider-key')
        app=create_app(state)
        session=state.workspace.create_session(owner,title='Remote independent-client proof',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];base=f'http://127.0.0.1:{port}';wsbase=f'ws://127.0.0.1:{port}';host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:asyncio.run(host.serve(sockets=[listener])),daemon=True);worker.start();deadline=monotonic()+30
        while not host.started:
            if monotonic()>deadline:raise AssertionError('Live host did not start')
            sleep(.05)
        async def exercise():
            async def login(client,username,password):
                response=await client.post('/auth/login',json={'username':username,'password':password,'transport':'bearer','device_name':'live-proof-client'});assert response.status_code==200,response.text;credentials=response.json();client.headers['Authorization']='Bearer '+credentials['access_token'];return credentials
            async def settle(predicate):
                for _ in range(400):
                    if predicate():return
                    await asyncio.sleep(.025)
                raise AssertionError('Live host did not reach required state')
            async def socket_for(token,task,after=0):
                ws=await websockets.connect(wsbase+'/graphql',subprotocols=['graphql-transport-ws']);await ws.send(json.dumps({'type':'connection_init','payload':{'authorization':'Bearer '+token}}));assert json.loads(await asyncio.wait_for(ws.recv(),5))['type']=='connection_ack';await ws.send(json.dumps({'id':'task','type':'subscribe','payload':{'query':'subscription($id:String!,$after:Int!){agent_task_events(task_id:$id,after:$after){sequence type task_id payload}}','variables':{'id':task,'after':after}}}));return ws
            async def event(ws,kind=None):
                for _ in range(500):
                    value=json.loads(await asyncio.wait_for(ws.recv(),5))
                    if value['type']=='next':
                        row=value['payload']['data']['agent_task_events']
                        if kind is None or row['type']==kind:return row
                    elif value['type']=='error':raise AssertionError(value)
                raise AssertionError('Expected live task event missing')
            async with httpx.AsyncClient(base_url=base,timeout=20) as controller,httpx.AsyncClient(base_url=base,timeout=20) as observer,httpx.AsyncClient(base_url=base,timeout=20) as foreign,httpx.AsyncClient(base_url=base,timeout=20) as anonymous:
                first=await login(controller,'remote-owner','remote-fixture-password-123');second=await login(observer,'remote-owner','remote-fixture-password-123');alien=await login(foreign,'foreign-user','foreign-fixture-password-123')
                response=await controller.post('/api/workspace/sessions/'+session['id']+'/turns',json={'prompt':'Coordinate remote workers','request_id':'live-remote-parent-0001'});assert response.status_code==200,response.text;parent=response.json();parent_id=parent['id'];report['task_id']=parent_id
                for client,expected in [(anonymous,{401}),(foreign,{403,404})]:
                    for path,payload in [('steer',{'message':'Denied instruction'}),('cancel',{}),('swarm',{'request_id':'denied-swarm-0001','members':[{'agent':'Denied','task':'Must not start','mode':'ask'}]})]:
                        denied=await client.post('/api/workspace/tasks/'+parent_id+'/'+path,json=payload);assert denied.status_code in expected,(path,denied.status_code,denied.text)
                    denied=await client.get('/api/workspace/tasks/'+parent_id);assert denied.status_code in expected,denied.text
                assert not state.agent_store.children(parent_id);report['checks'].append('Missing authentication and authenticated foreign ownership reject read, steer, cancel and swarm before effects')
                ws=await socket_for(second['access_token'],parent_id);initial=await event(ws);cursor=initial['sequence'];await ws.close();await observer.aclose();await controller.aclose();assert state.agent_store.get_task(parent_id)['status']=='awaiting_approval';report['checks'].append('Disconnecting both HTTP and event-stream clients retains the host task and pending approval')
                async with httpx.AsyncClient(base_url=base,headers={'Authorization':'Bearer '+first['access_token']},timeout=20) as reconnect:
                    payload={'request_id':'live-remote-team-0001','members':[{'agent':'Remote researcher','task':'Inspect remote fixture','mode':'ask'},{'agent':'Remote editor','task':'Review copied fixture','mode':'agent'}]}
                    created=await reconnect.post('/api/workspace/tasks/'+parent_id+'/swarm',json=payload);assert created.status_code==200,created.text;team=created.json();assert team['ok'],team
                    repeated=await reconnect.post('/api/workspace/tasks/'+parent_id+'/swarm',json=payload);assert repeated.status_code==200 and repeated.json()==team
                    tree=(await reconnect.get('/api/workspace/tasks/'+parent_id)).json();assert len(tree['children'])==2;reader=next(child for child in tree['children'] if child['mode']=='ask');editor=next(child for child in tree['children'] if child['mode']=='agent');assert reader['cwd']==str(folder) and editor['cwd']!=str(folder);assert Path(editor['cwd'],'read.txt').read_text()=='Remote fixture only\n';report['checks'].append('Reconnected independent client creates actual read-only and snapshot-isolated editable workers; HTTP retry returns the same two workers')
                    ws=await socket_for(second['access_token'],parent_id,cursor);replayed=await event(ws);assert replayed['sequence']>cursor;report['replay']={'before_disconnect':cursor,'after_reconnect':replayed['sequence']};report['checks'].append('GraphQL reconnect resumes strictly after persisted event cursor')
                    steered=await reconnect.post('/api/workspace/tasks/'+reader['id']+'/steer',json={'message':'Remote steering after reconnect'});assert steered.status_code==200,steered.text;await settle(lambda:any(row['type']=='task.steered' and row['payload']['message']=='Remote steering after reconnect' for row in state.agent_store.events(reader['id'])));report['checks'].append('Reconnected HTTP client steers the exact selected child')
                    # Revocation is exercised through public HTTP, not a private session-store shortcut.
                    revoke=await reconnect.delete('/auth/sessions/'+second['session_id']);assert revoke.status_code==200,revoke.text
                    try:
                        while True:await asyncio.wait_for(ws.recv(),3)
                    except ConnectionClosed as closed:assert closed.code==4401,closed.code
                    report['checks'].append('Revoking a second client session over HTTP closes its idle authenticated WebSocket with 4401')
                    revoked=await reconnect.get('/api/workspace/tasks/'+parent_id,headers={'Authorization':'Bearer '+second['access_token']});assert revoked.status_code==401,revoked.text
                    denied_socket=False
                    try:
                        async with websockets.connect(wsbase+'/graphql?k='+first['access_token'],subprotocols=['graphql-transport-ws']):pass
                    except websockets.exceptions.InvalidStatus as error:denied_socket=error.response.status_code==403
                    assert denied_socket;report['checks'].append('JWT session credentials in URL query cannot admit a WebSocket')
                    cancelled=await reconnect.post('/api/workspace/tasks/'+parent_id+'/cancel',json={});assert cancelled.status_code==200,cancelled.text;await settle(lambda:all(state.agent_store.get_task(identifier)['status']=='cancelled' for identifier in [parent_id,reader['id'],editor['id']]));report['checks'].append('Independent reconnected controller cancels parent and all real descendants')
                    report['task_states']={identifier:state.agent_store.get_task(identifier)['status'] for identifier in [parent_id,reader['id'],editor['id']]}
                    terminal=await reconnect.post('/graphql',json={'query':'mutation($input:CreateSessionInput!){create_session(input:$input){id}}','variables':{'input':{'cwd':str(folder),'shell':'/bin/bash','title':'Actual remote PTY proof'}}});data=terminal.json();assert not data.get('errors'),data;terminal_id=data['data']['create_session']['id']
                    headers={'Authorization':'Bearer '+first['access_token']}
                    async with websockets.connect(wsbase+'/api/sessions/'+terminal_id+'/pty',additional_headers=headers) as terminal_ws:
                        await terminal_ws.send(json.dumps({'type':'input','data':"export TERMX_REMOTE_PROOF=retained; printf 'first\\n' > remote-proof.txt\n"}));await settle(lambda:(folder/'remote-proof.txt').exists())
                    assert (folder/'remote-proof.txt').read_text()=='first\n';assert not state.sessions.get(terminal_id).exited
                    async with websockets.connect(wsbase+'/api/sessions/'+terminal_id+'/pty',additional_headers=headers) as terminal_ws:
                        replay=await asyncio.wait_for(terminal_ws.recv(),5);assert replay
                        await terminal_ws.send(json.dumps({'type':'input','data':"printf '%s\\n' \"$TERMX_REMOTE_PROOF\" >> remote-proof.txt\n"}));await settle(lambda:(folder/'remote-proof.txt').read_text()=='first\nretained\n')
                    terminal_denied=False
                    try:
                        async with websockets.connect(wsbase+'/api/sessions/'+terminal_id+'/pty',additional_headers={'Authorization':'Bearer '+alien['access_token']}):pass
                    except websockets.exceptions.InvalidStatus as error:terminal_denied=error.response.status_code==403
                    assert terminal_denied
                    destroyed=await reconnect.post('/graphql',json={'query':'mutation($id:String!){delete_session(session_id:$id){ok}}','variables':{'id':terminal_id}});assert destroyed.json()['data']['delete_session']['ok'];report['checks'].append('Real Bash PTY executes only fixture-local writes, survives WebSocket disconnect/reconnect with shell environment and replay intact, rejects foreign ownership, and closes through authorized API')
                    report['terminal']={'actual_shell':'/bin/bash','isolated_fixture_file':True,'reconnected_environment':'retained','foreign_socket_rejected':True,'destroyed_via_api':True}

                async with httpx.AsyncClient(base_url=base,timeout=20) as cookie_client:
                    response=await cookie_client.post('/auth/login',headers={'Origin':base},json={'username':'remote-owner','password':'remote-fixture-password-123','transport':'cookie'});assert response.status_code==200,response.text;csrf=response.json()['csrf_token']
                    denied=await cookie_client.post('/api/workspace/tasks/'+parent_id+'/cancel',json={});assert denied.status_code==403
                    denied=await cookie_client.post('/api/workspace/tasks/'+parent_id+'/cancel',headers={'Origin':'https://foreign.invalid','X-Termx-CSRF':csrf},json={});assert denied.status_code==403
                    allowed=await cookie_client.post('/api/workspace/tasks/'+parent_id+'/cancel',headers={'Origin':base,'X-Termx-CSRF':csrf},json={});assert allowed.status_code==200,allowed.text;report['checks'].append('Cookie controls require same origin and valid CSRF; bearer clients use independent canonical sessions')
        try:asyncio.run(exercise());report['status']='passed'
        except BaseException as error:report['status']='failed';report['error']=str(error);report['traceback']=traceback.format_exc();raise
        finally:adapter.release.set();host.should_exit=True;worker.join(15);(destination/'report.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else '/workspace/termx-live-proof/remote')
