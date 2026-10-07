"""Combined provider-free load proof against actual host, PTY and browser workers.

Opt in with: .venv/bin/python tests/workspace_load_e2e.py /tmp/termx-load-proof
Only temporary host data and a scoped local fixture origin are used. A manually
owned canonical task supplies control authority; no inference is fabricated.
"""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import sync_playwright

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'fixture':{'sessions':500,'turns':10000,'windows':2,'provider_queries':0,'authority':'explicitly owned canonical task fixture'},'errors':[],'interactions':[],'terminal_frames':0,'terminal_bytes':0,'browser_frames':0,'browser_bytes':0,'agent_actions':0,'agent_action_latencies_ms':[]}
    with tempfile.TemporaryDirectory(prefix='termx-combined-load-') as temporary:
        root=Path(temporary).resolve();os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.browser.network import NetworkPolicy
        state=AppState(passcode=None);owner=state.identity.setup_owner('fixture-owner','combined-load-password-123')
        project=state.projects.register(str(root),name='Combined load fixture')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        conversation=state.workspace.create_session(owner,title='Large conversation fixture',project_id=project['id'],cwd=str(root))
        for index in range(499):state.workspace.create_session(owner,title=f'Conversation fixture {index:03d}',project_id=project['id'],cwd=str(root))
        with state.agent_store._lock:
            state.agent_store._db.executemany('INSERT INTO conversation_turns VALUES(?,?,?,?,?,?,?,?,?)',[(f'fixture-turn-{i}',conversation['id'],i+1,None,f'Fixture message {i}: inspect the workspace state.','ask',None,None,time()) for i in range(10000)])
            state.agent_store._db.commit()
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<html><title>Combined local fixture</title><style>body{font:20px system-ui;background:#fff;color:#173a28;height:5000px;padding:30px}#tick{position:fixed;right:20px;top:20px}</style><h1>Live controlled fixture</h1><p id="tick"></p><script>let n=0;setInterval(()=>document.querySelector("#tick").textContent="Rendered frame "+(++n),50)</script></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        site=ThreadingHTTPServer(('127.0.0.1',0),Site);threading.Thread(target=site.serve_forever,daemon=True).start();origin=f'http://127.0.0.1:{site.server_port}'
        state.browser.network=NetworkPolicy([origin])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));loops=[]
        async def serve():loops.append(asyncio.get_running_loop());await server.serve(sockets=[listener])
        thread=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);thread.start();deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Temporary host did not start')
            sleep(.05)
        def host(coroutine):return asyncio.run_coroutine_threadsafe(coroutine,loops[0]).result(timeout=45)
        page=None;other=None;pty=None;stress=None;heartbeat=None;watchdog_stop=threading.Event();watchdog=None;loop_clock={'last':monotonic()}
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--enable-precise-memory-info'])
                context=browser.new_context(viewport={'width':2560,'height':1080},reduced_motion='reduce')
                def attach_page(value):
                    value.on('pageerror',lambda error:report['errors'].append(str(error)))
                    def websocket(ws):
                        if '/pty' in ws.url:kind='terminal'
                        elif '/api/browser/' in ws.url:kind='browser'
                        else:return
                        def frame(payload):
                            if isinstance(payload,bytes):report[kind+'_frames']+=1;report[kind+'_bytes']+=len(payload)
                        ws.on('framereceived',frame)
                    value.on('websocket',websocket)
                page=context.new_page();attach_page(page);start=monotonic();page.goto(f'http://127.0.0.1:{port}/?session={conversation["id"]}')
                page.get_by_label('Username',exact=True).fill('fixture-owner');page.get_by_label('Password',exact=True).fill('combined-load-password-123');page.get_by_role('button',name='Continue with password').click()
                page.wait_for_function('(()=>{const e=document.querySelector("textarea[aria-label=Message]");return !!e&&!e.disabled})()',timeout=90000)
                page.get_by_text('Fixture message 9999: inspect the workspace state.',exact=True).wait_for(timeout=90000)
                page.get_by_text('Conversation fixture 498',exact=True).wait_for(timeout=90000)
                report['authenticated_load_ms']=round((monotonic()-start)*1000)
                cookie=next(cookie['value'] for cookie in context.cookies() if cookie['name']=='termx_access')
                managed=state.identity.resolve(cookie)
                async def prepare():
                    nonlocal heartbeat
                    nonlocal watchdog
                    report['browser_phases']={};report['host_loop_max_lag_ms']=0;report['host_lag_stacks']=[]
                    def sample_host():
                        while not watchdog_stop.wait(.1):
                            delay=monotonic()-loop_clock['last']
                            if delay<.2:continue
                            frame=sys._current_frames().get(thread.ident);stack=[]
                            while frame:
                                stack.append(Path(frame.f_code.co_filename).name+':'+frame.f_code.co_name+':'+str(frame.f_lineno));frame=frame.f_back
                            samples=report['host_lag_stacks'];samples.append({'pending_ms':round(delay*1000),'stack':stack[:12]})
                            if len(samples)>300:del samples[:-300]
                    watchdog=threading.Thread(target=sample_host,daemon=True);watchdog.start()
                    def instrument(obj,name):
                        original=getattr(obj,name)
                        async def timed(*args,**kwargs):
                            started=monotonic()
                            try:return await original(*args,**kwargs)
                            finally:
                                values=report['browser_phases'].setdefault(name,[])
                                values.append(round((monotonic()-started)*1000))
                                if len(values)>500:del values[:-500]
                        setattr(obj,name,timed)
                    for phase in ('_document_hash','_perform','frame'):instrument(state.browser,phase)
                    async def monitor_loop():
                        last=monotonic()
                        while True:
                            await asyncio.sleep(.1);current=monotonic();loop_clock['last']=current
                            report['host_loop_max_lag_ms']=max(report['host_loop_max_lag_ms'],round(max(0,current-last-.1)*1000));last=current
                    heartbeat=asyncio.create_task(monitor_loop())
                    task=state.agent_store.create_task(prompt='Bounded fixture control authority',cwd=str(root),provider_id='fixture-no-inference',model='fixture',limits={'max_steps':100,'max_seconds':120},mode='agent')
                    state.agent_store.update_task(task['id'],status='running')
                    state.workspace.store.create('task',owner.id,{'conversation_id':conversation['id'],'cwd':str(root)},project['id'],task['id'])
                    state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id'])
                    state.browser.records.put('agent-task-authority',task['id'],{'id':task['id'],'principal_id':owner.id,'session_id':managed.session_id,'project_id':project['id'],'policy_version':owner.policy_version})
                    with state.agent_store._lock:
                        state.agent_store._db.execute('UPDATE conversation_turns SET task_id=? WHERE id=?',(task['id'],'fixture-turn-9999'));state.agent_store._db.commit()
                    profile=state.browser.create_profile(owner.id,project['id'],'Load fixture profile')
                    tab=await state.browser.create_tab(owner.id,managed.session_id,profile['id'],origin)
                    instrument(state.browser._pages[tab['id']].mouse,'wheel')
                    grant=await state.browser.handoff(tab['id'],owner.id,managed.session_id,run_id=task['id'],origins=[origin],actions=['observe','capture','scroll'],expires_in=300,policy_version=owner.policy_version)
                    noisy="import time,sys\nfor i in range(5000):\n print(('Noisy fixture line %d '%i)+'x'*180,flush=True)\n time.sleep(.02)"
                    terminal=state.sessions.create(argv=[sys.executable,'-u','-c',noisy],cwd=str(root),title='Bounded noisy PTY fixture')
                    state.authorization.claim_principal(owner,'terminal',terminal.id,project_id=project['id'])
                    gate=asyncio.Event()
                    async def activity():
                        await gate.wait()
                        for i in range(120):
                            current=state.browser.get(tab['id'],owner.id)
                            action_start=monotonic()
                            await state.browser.action(tab['id'],owner.id,session_id=managed.session_id,run_id=task['id'],grant_id=grant['grant']['id'],action_id='load-scroll-'+str(i),action='scroll',args={'x':0,'y':25 if i%2==0 else -25},document_revision=current['document_revision'],lease_revision=current['lease_revision'],policy_version=owner.policy_version,authority=lambda:bool(state.identity.session_by_id(managed.session_id)))
                            report['agent_action_latencies_ms'].append(round((monotonic()-action_start)*1000));report['agent_actions']+=1;await asyncio.sleep(.15)
                    return terminal,(asyncio.create_task(activity()),gate)
                pty,controls=host(prepare());stress,gate=controls
                page.keyboard.press('Control+2');page.get_by_label('Terminal session').wait_for(timeout=90000);page.get_by_label('Terminal session').select_option(pty.id)
                page.get_by_role('button',name='Commands',exact=True).click();page.get_by_role('button',name='Focus browser',exact=False).click()
                page.get_by_text('Live browser connected',exact=True).wait_for(timeout=90000)
                other=context.new_page();attach_page(other);other.goto(f'http://127.0.0.1:{port}/?session={conversation["id"]}')
                other.wait_for_function('(()=>{const e=document.querySelector("textarea[aria-label=Message]");return !!e&&!e.disabled})()',timeout=90000)
                other.get_by_label('Terminal session').wait_for(timeout=90000);other.get_by_label('Terminal session').select_option(pty.id)
                other.get_by_role('button',name='Commands',exact=True).click();other.get_by_role('button',name='Focus browser',exact=False).click()
                other.get_by_text('Live browser connected',exact=True).wait_for(timeout=90000)
                loops[0].call_soon_threadsafe(gate.set)
                warmup_start=monotonic();warmup_deadline=warmup_start+45
                while report['agent_actions']<2 and not stress.done() and monotonic()<warmup_deadline:page.wait_for_timeout(100)
                report['control_warmup_ms']=round((monotonic()-warmup_start)*1000)
                report['control_warmup_latencies_ms']=report['agent_action_latencies_ms'][:]
                assert report['agent_actions']>=2,report
                report['agent_actions']=0;report['agent_action_latencies_ms']=[]
                report['terminal_frames']=0;report['terminal_bytes']=0;report['browser_frames']=0;report['browser_bytes']=0
                workload_start=monotonic()
                for i in range(10):
                    start=monotonic();page.get_by_label('Search conversations',exact=True).fill('fixture 123');page.get_by_text('Conversation fixture 123',exact=True).wait_for(timeout=15000)
                    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    report['interactions'].append({'kind':'filter_500_sessions_with_streams','ms':round((monotonic()-start)*1000)})
                    page.get_by_label('Search conversations',exact=True).fill('');page.get_by_text('Conversation fixture 498',exact=True).wait_for(timeout=15000)
                    page.get_by_role('textbox',name='Message',exact=True).fill('Unsent simultaneous-load draft '+str(i));page.wait_for_timeout(80)
                deadline=monotonic()+30
                while (report['agent_actions']<12 or report['browser_frames']<12) and not stress.done() and monotonic()<deadline:page.wait_for_timeout(200)
                if stress.done() and not stress.cancelled() and stress.exception():report['agent_action_error']=repr(stress.exception())
                page.wait_for_timeout(500)
                report['combined_workload_ms']=round((monotonic()-workload_start)*1000)
                report['memory']=[view.evaluate('performance.memory?{used:performance.memory.usedJSHeapSize,total:performance.memory.totalJSHeapSize}:null') for view in (page,other)]
                report['dom_nodes']=[view.locator('*').count() for view in (page,other)]
                report['max_interaction_ms']=max(sample['ms'] for sample in report['interactions'])
                report['mounted_message_rows']=[view.locator('.message-row').count() for view in (page,other)]
                assert report['terminal_bytes']>50000 and report['browser_frames']>=10 and report['agent_actions']>=10,report
                assert max(report['mounted_message_rows'])<100 and not report['errors'],report
                assert report['max_interaction_ms']<2000,report
                page.screenshot(path=str(destination/'combined-load.png'))
                report['no_accidental_turn']=len(state.agent_store.workspace_turns_page(conversation['id'],after_sequence=9999,limit=201))==1
                assert report['no_accidental_turn']
                context.close();browser.close()
        except Exception:
            if other and not other.is_closed():
                try:other.screenshot(path=str(destination/'other-failure.png'));(destination/'other-failure.html').write_text(other.content())
                except Exception:pass
            if page and not page.is_closed():
                try:page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                except Exception:pass
            raise
        finally:
            watchdog_stop.set()
            if watchdog:watchdog.join(timeout=1)
            if stress:loops[0].call_soon_threadsafe(stress.cancel)
            if heartbeat:loops[0].call_soon_threadsafe(heartbeat.cancel)
            if pty:state.sessions.kill(pty.id)
            server.should_exit=True;thread.join(timeout=20);listener.close();site.shutdown();site.server_close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
