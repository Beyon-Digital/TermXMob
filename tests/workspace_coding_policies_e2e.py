"""Real managed typed effects plus production consent/rule controls; no inference."""
from __future__ import annotations
import json,os,socket,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright
from workspace_ui_snapshot import snapshot_ui


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'managed-coding-consent','paid_queries':0,'errors':[],'states':[],'keyboard_checks':[],'native_select_method':'Tab and Shift+Tab actual focus; select_option for native picker values, not OS popup keyboard automation'}
    with tempfile.TemporaryDirectory(prefix='termx-coding-consent-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from test_agent import _FanOutAdapter,_fn
        state=AppState(passcode=None);owner=state.identity.setup_owner('consent-owner','consent-fixture-password-123')
        project=root/'project';project.mkdir();registered=state.projects.register(str(project),name='Consent project')
        adapter=_FanOutAdapter({'Edit code':([[_fn('write-a','write_file',path='owned.py',content='VALUE = 1\n')],[_fn('write-b','write_file',path='owned.py',content='VALUE = 1\n')]],0.)})
        state.agent._adapter_factory=lambda *_:adapter
        from termx.agent.secrets import CredentialStore
        state.credentials=CredentialStore(memory={});state.agent.credentials=state.credentials
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['functions'],api_key='fixture-only')
        app=create_app(state,web_dir=snapshot_ui(root,Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))
        conversation=state.workspace.create_session(owner,title='Consent proof',project_id=registered['id'],cwd=str(project),provider_id='fixture',mode='agent')
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page()
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/?session={conversation["id"]}');page.get_by_label('Username',exact=True).fill('consent-owner');page.get_by_label('Password',exact=True).fill('consent-fixture-password-123');page.get_by_role('button',name='Continue with password',exact=True).click()
                composer=page.get_by_role('textbox',name='Message',exact=True);composer.wait_for();page.wait_for_function('(()=>{const t=document.querySelector("textarea[aria-label=Message]");return t&&!t.disabled})()')
                composer.fill('Edit code');composer.press('Enter');page.get_by_role('button',name='Allow once',exact=True).click()
                scope=page.get_by_role('combobox',name='Remember approval scope',exact=True);scope.wait_for(timeout=30000);scope.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab');assert scope.evaluate('(element)=>document.activeElement===element')
                scope.select_option('conversation');assert not (project/'owned.py').exists();page.get_by_role('button',name='Allow and remember',exact=True).click()
                deadline=monotonic()+30
                while not state.agent_store.list_tasks() or state.agent_store.list_tasks()[0]['status']!='completed':
                    if monotonic()>deadline:raise AssertionError('Managed two-write task did not complete')
                    sleep(.05)
                task=state.agent_store.list_tasks()[0];assert (project/'owned.py').read_text()=='VALUE = 1\n';assert len([a for a in state.agent_store.approvals(task['id']) if a['kind']=='tool'])==1
                decisions=state.browser.records.list('review');assert len(decisions)==2 and any(row.get('decision_source')=='coding-policy' for row in decisions)
                rule=state.agent_store.list_policy_rules()[0];assert rule['consent_binding']['principal_id']==owner.id and rule['scope_type']=='conversation'
                report['managed_effects']={'exact_two_writes':True,'one_human_tool_decision':True,'canonical_conversation_bound':True,'second_audit_source':'coding-policy'}
                page.get_by_role('button',name='Action review',exact=True).click()
                policy=page.get_by_role('article',name='Coding policy '+rule['id'],exact=True);policy.wait_for();policy.get_by_text('Inspect coding policy scope',exact=True).click();policy.get_by_role('region',name='Exact coding policy scope',exact=True).focus();assert page.evaluate('document.activeElement.getAttribute("aria-label")')=='Exact coding policy scope'
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for zoom in (100,200):
                    page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                    for theme in ('dark','light'):
                        # Keep the populated editor while changing actual persisted theme.
                        page.get_by_role('dialog',name='Action review',exact=True).get_by_role('button',name='Close',exact=True).click();page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.get_by_role('button',name='Action review',exact=True).click();policy=page.get_by_role('article',name='Coding policy '+rule['id'],exact=True)
                        policy.get_by_role('button',name='Edit coding policy',exact=True).click();decision=policy.get_by_role('combobox',name='Coding decision',exact=True);decision.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab');assert decision.evaluate('(element)=>document.activeElement===element')
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        entry={'theme':theme,'zoom':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[row['id'] for row in result['violations']],'incomplete':[{'id':row['id'],'targets':[node['target'] for node in row['nodes']]} for row in result['incomplete']]};report['states'].append(entry);assert not entry['overflow'] and not entry['violations'],entry
                        for control in (decision,policy.get_by_label('Coding policy expiry seconds',exact=True),policy.get_by_role('button',name='Save coding policy',exact=True),policy.get_by_role('button',name='Cancel coding policy edit',exact=True),policy.get_by_role('button',name='Revoke coding policy',exact=True)):
                            control.scroll_into_view_if_needed();control.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab')
                            check=control.evaluate('''element=>{const r=element.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {focus:document.activeElement===element,uncovered:hit===element||element.contains(hit),bounds:{x:r.x,y:r.y,width:r.width,height:r.height},inside:r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,label:element.getAttribute('aria-label')||element.textContent||element.tagName}}''')
                            report['keyboard_checks'].append({'theme':theme,'zoom':zoom,**check});assert check['focus'] and check['uncovered'] and check['inside'],check
                        policy.get_by_role('button',name='Save coding policy',exact=True).scroll_into_view_if_needed();policy.get_by_role('button',name='Save coding policy',exact=True).focus()
                        page.screenshot(path=str(destination/f'coding-policy-{theme}-{zoom}.png'))
                        policy.get_by_role('button',name='Cancel coding policy edit',exact=True).click()
                policy.get_by_role('button',name='Edit coding policy',exact=True).click();policy.get_by_role('combobox',name='Coding decision',exact=True).select_option('deny');policy.get_by_label('Coding policy expiry seconds',exact=True).fill('120');policy.get_by_role('button',name='Save coding policy',exact=True).click()
                page.wait_for_function("()=>!document.querySelector('select[aria-label=\"Coding decision\"]')")
                changed=state.agent_store.get_policy_rule(rule['id']);assert changed['version']==2 and changed['effect']=='deny';assert changed['consent_binding']==rule['consent_binding'] and changed['fingerprint']==rule['fingerprint']
                policy=page.get_by_role('article',name='Coding policy '+rule['id'],exact=True);policy.get_by_role('button',name='Revoke coding policy',exact=True).click();page.wait_for_function("()=>document.querySelector('section[aria-label=\"Coding action policies\"]')?.textContent.includes('Revoked')")
                assert state.agent_store.get_policy_rule(rule['id'])['version']==3 and state.agent_store.get_policy_rule(rule['id'])['revoked_at'] is not None
                report['rule_lifecycle']={'inspected':True,'edited_deny_expiry_cas':True,'immutable_binding_fingerprint':True,'revoked':True};assert not report['errors'];context.close();browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    try:print(json.dumps(run(sys.argv[1]),indent=2))
    except Exception as error:raise RuntimeError(type(error).__name__+': '+str(error).split('Call log:')[0]) from None
