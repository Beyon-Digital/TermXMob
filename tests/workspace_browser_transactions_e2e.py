"""Immutable production transaction/manual/network cards; localhost only."""
from __future__ import annotations
import asyncio,json,os,re,socket,sys,tempfile,threading,traceback
from pathlib import Path
from time import monotonic,sleep
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import expect,sync_playwright
from workspace_ui_snapshot import snapshot_ui


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-browser-transaction-cards','paid_queries':0,'external_transactions':0,'errors':[],'states':[],'keyboard':[],'reading':[],'dialogs':[],'focus_returns':[],
            'zoom_method':'Fixed1440x1000 physical display:1440x1000CSS/dpr1 at100%,720x500CSS/dpr2 at200%; same managed-session cookies across isolated renderer contexts.',
            'limitations':['No native OS popup, physical input, live reviewer or authentic merchant qualification. Page transaction labels are untrusted. Local host adapter supplies deterministic tool calls; no model network requests.']}
    with tempfile.TemporaryDirectory(prefix='termx-transactions-render-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderCall,ProviderTurn
        from termx.agent.secrets import CredentialStore
        from termx.browser.network import NetworkPolicy
        from termx.auto_review import ReviewRequired,ActionBlocked
        from test_agent import FakeAdapter
        finish=threading.Event()
        class Adapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1
                if self.turns==1:return ProviderTurn('handoff','',[ProviderCall('function','wait','browser_wait_for_handoff',{'seconds':300})],{},[])
                if self.turns==2:
                    row=next(row for row in state.browser.records.list('tab') if row['state']=='agent')
                    return ProviderTurn('buy','',[ProviderCall('function','buy-original','browser_action',{'tab_id':row['id'],'action':'click','args':{'selector':'#buy'},'document_revision':row['document_revision'],'lease_revision':row['lease_revision']})],{},[])
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('finish','Fixture finished',[],{},[])
        state=AppState(passcode=None,credentials=CredentialStore({}),adapter_factory=lambda *_:Adapter())
        # One actual task adapter retains its sequence across turns.
        adapter=Adapter();state.agent._adapter_factory=lambda *_:adapter
        owner=state.identity.setup_owner('transaction-owner','transaction-fixture-password-123')
        project=state.projects.register(str(root),name='Transaction fixture')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network controlled fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='fixture-only')
        observed_requests=[]
        class Checkout(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'''<!doctype html><html><title>Checkout fixture</title><style>body{font:20px system-ui;padding:30px}input,button{font:inherit;padding:10px;margin:8px}</style><h1>Local exact checkout</h1><form data-merchant="Fixture merchant" data-amount="12.50" data-currency="EUR" data-account="customer@example.test"><label>Order item<input name=item value="Sample order"></label><input type=hidden name=csrf value="NEVER_PUBLISH_PROTOCOL_SECRET"><button id=buy>Buy order</button></form><button id=unknown>Unknown operation</button><script>window.effects=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.effects++;document.querySelector('h1').innerText='Exact order performed'}</script></html>'''
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        class Other(BaseHTTPRequestHandler):
            def do_GET(self):
                observed_requests.append(self.path);body=b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="green"/></svg>'
                self.send_response(200);self.send_header('Content-Type','image/svg+xml');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_POST(self):self.do_GET()
            def log_message(self,*_):pass
        sites=[ThreadingHTTPServer(('127.0.0.1',0),handler) for handler in (Checkout,Other)]
        for site in sites:threading.Thread(target=site.serve_forever,daemon=True).start()
        origin,other=[f'http://127.0.0.1:{site.server_port}' for site in sites]
        app=create_app(state,web_dir=snapshot_ui(root,Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))
        state.browser.network=NetworkPolicy([origin,other]);report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        expected_bundle=os.environ.get('TERMX_TRANSACTION_UI_BUNDLE','index-Cq30cxjA.js')
        assert any(expected_bundle in asset for asset in report['UI_asset_snapshot']), 'Proof requires the named immutable coherent bundle'
        conversation=state.workspace.create_session(owner,title='Exact transaction proof',project_id=project['id'],cwd=str(root),provider_id='fixture',mode='agent')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));loops=[]
        async def serve():loops.append(asyncio.get_running_loop());await server.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        deadline=monotonic()+30
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Isolated host startup timed out')
            sleep(.05)
        def host_call(coro):return asyncio.run_coroutine_threadsafe(coro,loops[0]).result(timeout=45)
        page=None;context=None;browser=None;playwright=None
        try:
            playwright=sync_playwright().start()
            browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page()
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            url=f'http://127.0.0.1:{port}/?session={conversation["id"]}'
            page.goto(url);page.get_by_label('Username',exact=True).fill('transaction-owner');page.get_by_label('Password',exact=True).fill('transaction-fixture-password-123');page.get_by_role('button',name='Continue with password',exact=True).click()
            page.wait_for_function('()=>{const e=document.querySelector("textarea[aria-label=Message]");return e&&!e.disabled}',timeout=60000)
            page.get_by_role('textbox',name='Message',exact=True).fill('Review the exact local order');page.get_by_role('button',name='Send message',exact=True).click();page.get_by_role('button',name='Review action',exact=True).click();page.get_by_role('dialog',name='Review action',exact=True).get_by_role('button',name='Allow once',exact=True).click()
            page.get_by_role('button',name='Appearance',exact=True).focus();page.keyboard.press('Control+3')
            page.get_by_role('button',name='Create a browser profile',exact=True).click();page.get_by_label('New profile name',exact=True).fill('Fixture account profile');page.get_by_role('button',name='Create profile',exact=True).click()
            page.get_by_label('Page address',exact=True).fill(origin);page.get_by_role('button',name='Go',exact=True).click();page.get_by_text('Live browser connected',exact=True).wait_for(timeout=30000)
            page.get_by_role('button',name='Let agent use',exact=True).click();page.get_by_role('button',name='Hand over & observe',exact=True).click()
            page.get_by_role('button',name='Approve once',exact=True).wait_for(timeout=30000)
            tab=next(row for row in state.browser.records.list('tab') if row['state']=='agent');grant=state.browser.records.get('grant',tab['grant_id']);task=state.agent_store.get_task(grant['run_id']);original=state.browser.records.get('review',task['id']+':buy-original')
            assert original and original['human_preview']['amount']=='12.50'
            def current_action(identifier,selector='#buy'):
                current=state.browser.get(tab['id'],owner.id)
                return state.browser.action(tab['id'],owner.id,session_id=grant['session_id'],run_id=task['id'],grant_id=grant['id'],policy_version=grant['policy_version'],action_id=identifier,action='click',args={'selector':selector},document_revision=current['document_revision'],lease_revision=current['lease_revision'])
            async def site_eval(source,arg=None):return await state.browser._pages[tab['id']].evaluate(source,arg)
            axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
            active_theme='dark';active_zoom=100
            def seek(control,label):
                control.wait_for(state='visible',timeout=30000);assert control.is_enabled(),label
                for _ in range(220):
                    if control.evaluate('e=>document.activeElement===e'):break
                    page.keyboard.press('Tab')
                else:
                    page.screenshot(path=str(destination/'failure-keyboard.png'));report['failure_body']=page.locator('body').inner_text()
                    raise AssertionError('Tab cannot reach '+label)
                check=control.evaluate('''async e=>{
                    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                    const i=await new Promise(resolve=>{const o=new IntersectionObserver(rows=>{o.disconnect();const b=rows[0].intersectionRect;resolve({left:b.left,right:b.right,top:b.top,bottom:b.bottom})});o.observe(e)});
                    const h=document.elementFromPoint((i.left+i.right)/2,(i.top+i.bottom)/2);
                    const extent=Math.max(0,(parseFloat(s.outlineWidth)||0)+(parseFloat(s.outlineOffset)||0));
                    const ring={left:r.left-extent,right:r.right+extent,top:r.top-extent,bottom:r.bottom+extent};
                    const clip={left:0,right:innerWidth,top:0,bottom:innerHeight};
                    for(let p=e.parentElement;p;p=p.parentElement){
                        const ps=getComputedStyle(p),b=p.getBoundingClientRect();
                        if(/^(auto|scroll|hidden|clip)$/.test(ps.overflowX)){clip.left=Math.max(clip.left,b.left+p.clientLeft);clip.right=Math.min(clip.right,b.left+p.clientLeft+p.clientWidth)}
                        if(/^(auto|scroll|hidden|clip)$/.test(ps.overflowY)){clip.top=Math.max(clip.top,b.top+p.clientTop);clip.bottom=Math.min(clip.bottom,b.top+p.clientTop+p.clientHeight)}
                    }
                    return {browser_approval_focus:!!e.closest('.browser-workspace .browser-approval'),focus:document.activeElement===e,outline:s.outlineStyle,outline_width:s.outlineWidth,outline_offset:s.outlineOffset,focus_ring:ring,ancestor_clip:clip,focus_ring_fully_visible:ring.left>=clip.left-1&&ring.right<=clip.right+1&&ring.top>=clip.top-1&&ring.bottom<=clip.bottom+1,uncovered:h===e||e.contains(h),visible_width:i.right-i.left,visible_height:i.bottom-i.top,bounds:{x:r.x,y:r.y,width:r.width,height:r.height}}
                }''')
                # Collect every ring boundary before the final strict gate;
                # one unrelated control must not hide other clipping findings.
                report['keyboard'].append({'theme':active_theme,'zoom':active_zoom,'label':label,**check});assert check['focus'] and check['outline']!='none' and check['outline_width']!='0px' and check['uncovered'] and check['visible_width']>=check['bounds']['width']-1 and check['visible_height']>=check['bounds']['height']-1,check
                if check['browser_approval_focus']:assert check['outline_offset']=='-3px',check
            def button(label):seek(page.get_by_role('button',name=label,exact=True),label);page.keyboard.press('Enter')
            def layout(name):seek(page.get_by_role('button',name='Appearance',exact=True),'Layout shortcut origin');page.keyboard.press('Control+'+('3' if name=='browser' else '1'))
            def read_evidence(control,label):
                control.scroll_into_view_if_needed();check=control.evaluate('''async e=>{const b=e.getBoundingClientRect();const i=await new Promise(resolve=>{const o=new IntersectionObserver(rows=>{o.disconnect();const r=rows[0].intersectionRect;resolve({left:r.left,right:r.right,top:r.top,bottom:r.bottom})});o.observe(e)});const h=document.elementFromPoint((i.left+i.right)/2,(i.top+i.bottom)/2);return {height:b.height,width:b.width,visible_height:i.bottom-i.top,visible_width:i.right-i.left,uncovered:h===e||e.contains(h)}}''');report['reading'].append({'theme':active_theme,'zoom':active_zoom,'label':label,**check});assert check['height']>=12 and check['visible_height']>=check['height']-1 and check['visible_width']>=check['width']-1 and check['uncovered'],check
            def matrix(label,surface,controls,amount=None,manual=False):
                nonlocal page,context,active_theme,active_zoom
                for zoom in (100,200):
                    if active_zoom!=zoom:
                        cookies=context.storage_state();context.close();context=browser.new_context(storage_state=cookies,viewport={'width':1440*100//zoom,'height':1000*100//zoom},device_scale_factor=zoom/100,reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)));page.goto(url);page.get_by_role('button',name='Appearance',exact=True).wait_for(timeout=60000);active_zoom=zoom
                    layout(surface)
                    for theme in ('dark','light'):
                        active_theme=theme;button('Appearance');seek(page.locator('input[name="theme-preference"]:checked'),'Theme group keyboard entry')
                        desired=page.get_by_role('radio',name=theme.title(),exact=True)
                        for _ in range(3):
                            if desired.is_checked():break
                            page.keyboard.press('ArrowRight')
                        assert desired.is_checked();seek(desired,'Appearance '+theme);page.keyboard.press('Space');button('Close')
                        trigger=None
                        if surface=='chat':
                            trigger=page.get_by_role('region',name='Conversation',exact=True).get_by_role('button',name='Review browser operation',exact=True);seek(trigger,'Review browser operation');page.keyboard.press('Enter');surface_root=page.get_by_role('dialog',name='Review browser operation',exact=True)
                            # The dialog correctly removes the background composer
                            # from the accessibility tree. Inspect retained bytes,
                            # without claiming that hidden background is reachable.
                            assert page.locator('textarea[aria-label="Message"]').input_value()=='Retained unsent transaction draft'
                            dialog_bounds=surface_root.evaluate('e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,viewport_width:innerWidth,viewport_height:innerHeight,scroll_height:e.scrollHeight,client_height:e.clientHeight}}')
                            report['dialogs'].append({'theme':theme,'zoom':zoom,**dialog_bounds})
                            assert dialog_bounds['x']>=0 and dialog_bounds['y']>=0 and dialog_bounds['x']+dialog_bounds['width']<=dialog_bounds['viewport_width']+1 and dialog_bounds['y']+dialog_bounds['height']<=dialog_bounds['viewport_height']+1,dialog_bounds
                        else:surface_root=page.get_by_role('region',name='Built-in browser workspace',exact=True)
                        preview=surface_root.get_by_role('region',name='Host-observed browser operation',exact=True)
                        if amount:
                            preview.wait_for()
                            for value in ('Fixture merchant',amount+' EUR','Sample order','Fixture account profile','customer@example.test'):read_evidence(preview.get_by_text(value,exact=True),value)
                            page.screenshot(path=str(destination/f'{label}-evidence-{theme}-{zoom}.png'))
                            summary=preview.get_by_text('Exact host evidence reference',exact=True);seek(summary,'Exact evidence reference')
                            if not preview.locator('code').is_visible():page.keyboard.press('Enter')
                            assert preview.locator('code').is_visible() and preview.locator('code').inner_text()==original['human_preview']['document_hash'];assert 'NEVER_PUBLISH_PROTOCOL_SECRET' not in preview.inner_text()
                            read_evidence(preview.locator('code'),'Exact document hash')
                        if manual:assert not surface_root.get_by_role('button',name='Approve once',exact=True).count() and preview.get_by_text('Take over / Private login to complete this operation. Agent approval is unavailable.',exact=True).is_visible()
                        for control in controls:seek(surface_root.get_by_role('button',name=control,exact=True),control)
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        entry={'state':label,'surface':surface,'theme':theme,'zoom':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'violations':[{'id':r['id'],'targets':[n['target'] for n in r['nodes']]} for r in result['violations']],'incomplete':[{'id':r['id'],'targets':[n['target'] for n in r['nodes']]} for r in result['incomplete']]};report['states'].append(entry);page.screenshot(path=str(destination/f'{label}-{theme}-{zoom}.png'));assert not entry['overflow'] and entry['reduced_motion'] and not entry['violations'],entry;assert not any(r['id']=='aria-prohibited-attr' for r in entry['incomplete']),entry
                        if trigger:
                            button('Close');surface_root.wait_for(state='hidden');expect(trigger).to_be_focused(timeout=3000);report['focus_returns'].append({'theme':theme,'zoom':zoom,'returned_to_exact_review_trigger':True});assert page.get_by_role('textbox',name='Message',exact=True).input_value()=='Retained unsent transaction draft','Closing review changed unsent draft'
            matrix('exact-browser-approval','browser',['Approve once','Deny'],amount='12.50')
            layout('chat');page.get_by_role('textbox',name='Message',exact=True).fill('Retained unsent transaction draft')
            deadline=monotonic()+20
            while state.workspace.record(owner,'conversation',conversation['id']).get('draft_text')!='Retained unsent transaction draft':
                if monotonic()>deadline:raise AssertionError('Explicit unsent draft did not persist before renderer context reload')
                page.wait_for_timeout(50)
            matrix('exact-chat-approval','chat',['Allow once','Deny'],amount='12.50')
            host_call(site_eval("()=>document.querySelector('form').dataset.amount='13.50'"))
            try:host_call(current_action(original['id']));raise AssertionError('Changed amount reused old review')
            except ValueError as error:assert 'document changed' in str(error)
            assert host_call(site_eval('()=>window.effects'))==0
            # Deny settles/cancels the canonical old task. A fresh task and
            # explicit handoff are required; no liveness bypass or invented
            # task continuation is used to test the new amount.
            layout('chat');button('Review browser operation');button('Deny');deadline=monotonic()+20
            while state.agent_store.get_task(task['id'])['status']!='cancelled':
                if monotonic()>deadline:raise AssertionError('Denied old task was not cancelled')
                sleep(.05)
            report['denied_old_canonical_task_cancelled']=True;old_task_id=task['id']
            page.get_by_role('button',name='Send message',exact=True).wait_for(timeout=30000)
            page.get_by_role('textbox',name='Message',exact=True).fill('Review the fresh local order');button('Send message');button('Review action');button('Allow once')
            deadline=monotonic()+20
            while True:
                task=next((row for row in state.agent_store.list_tasks() if row['id']!=old_task_id and row['status']=='running'),None)
                if task:break
                if monotonic()>deadline:raise AssertionError('Fresh explicitly started task not running')
                sleep(.05)
            layout('browser');button('Take over');page.get_by_role('button',name='Let agent use',exact=True).wait_for();button('Let agent use');button('Hand over & observe');page.get_by_role('button',name='Take over',exact=True).wait_for();grant=state.browser.records.get('grant',state.browser.get(tab['id'],owner.id)['grant_id'])
            assert grant['run_id']==task['id'] and grant['session_id']==original['session_id']
            fresh_id='ui-exact-fresh'
            try:host_call(current_action(fresh_id));raise AssertionError('Fresh purchase skipped exact review')
            except ReviewRequired as fresh:assert fresh.record['human_preview']['amount']=='13.50'
            layout('browser');page.get_by_text('13.50 EUR',exact=True).wait_for(timeout=15000);button('Approve once');deadline=monotonic()+15
            while state.browser.records.get('review',fresh_id)['status']!='approved_once':
                if monotonic()>deadline:raise AssertionError('Fresh UI decision not persisted')
                sleep(.05)
            host_call(current_action(fresh_id));assert host_call(site_eval('()=>window.effects'))==1
            try:host_call(current_action('manual-unknown','#unknown'));raise AssertionError('Unknown operation acquired approval')
            except ActionBlocked as blocked:assert blocked.record['human_preview']['manual_required']
            matrix('manual-only-takeover','browser',['Take over this operation'],manual=True)
            button('Take over this operation');page.get_by_role('button',name='Let agent use',exact=True).wait_for();assert not state.browser.get(tab['id'],owner.id).get('transport_restricted')
            button('Let agent use');button('Hand over & observe');grant=state.browser.records.get('grant',state.browser.get(tab['id'],owner.id)['grant_id'])
            async def refused_fetch():
                target=state.browser._pages[tab['id']]
                async with target.expect_event('requestfailed',predicate=lambda request:request.url==other+'/leak'):
                    await target.evaluate('(url)=>{void fetch(url,{method:"POST",mode:"no-cors",body:"FIXTURE_DATA"}).catch(()=>{})}',other+'/leak')
            host_call(refused_fetch());host_call(refused_fetch());assert '/leak' not in observed_requests
            page.get_by_text('Page requests paused · take over to continue',exact=True).wait_for(timeout=15000)
            matrix('network-request-paused','browser',['Take over'])
            button('Take over');page.get_by_role('button',name='Let agent use',exact=True).wait_for();assert not state.browser.get(tab['id'],owner.id).get('transport_restricted')
            report['effects']={'old_amount_rejected_before_effect':True,'fresh_review_amount':'13.50 EUR','exact_purchase_effects':host_call(site_eval('()=>window.effects')),'unknown_approval_unavailable':True,'explicit_manual_takeover':True,'repeated_ungranted_POST_contacted_site':False,'retained_pause_explicitly_released':True,'same_canonical_active_task':state.agent_store.get_task(task['id'])['status']=='running'}
            report['clipped_focus_rings']=[row for row in report['keyboard'] if not row['focus_ring_fully_visible']]
            assert not report['clipped_focus_rings'],report['clipped_focus_rings']
            assert not report['errors'];finish.set();context.close();browser.close()
        except Exception as error:
            report['failure']={'kind':type(error).__name__,'frames':[{'file':Path(frame.filename).name,'line':frame.lineno} for frame in traceback.extract_tb(error.__traceback__)[-4:]]}
            if page and not page.is_closed():
                try:page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content());report['failure_body']=page.locator('body').inner_text()
                except Exception:pass
            raise
        finally:
            if browser:
                try:browser.close()
                except Exception:pass
            if playwright:
                try:playwright.stop()
                except Exception:pass
            finish.set();server.should_exit=True;worker.join(timeout=20);listener.close()
            for site in sites:site.shutdown();site.server_close()
            (destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    try:
        report=run(sys.argv[1]);print(json.dumps({'states':len(report['states']),'keyboard_checks':len(report['keyboard']),'immutable_assets':len(report['UI_asset_snapshot']),'errors':report['errors'],'effects':report['effects']},indent=2))
    except Exception as error:raise RuntimeError(type(error).__name__+': '+str(error).split('Call log:')[0]) from None
