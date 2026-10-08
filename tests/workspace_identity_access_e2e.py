"""Production Access UI and actual scoped host effects in an isolated fixture."""
from __future__ import annotations
import json,os,socket,sys,tempfile,threading
from datetime import datetime,timedelta,timezone
from pathlib import Path
from time import monotonic,sleep,time
import httpx,uvicorn
from playwright.sync_api import sync_playwright
from workspace_ui_snapshot import snapshot_ui

PASSWORD='access-fixture-password-123'

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'production-identity-groups-audit','provider_queries':0,'real_host_audit_modified':False,'errors':[],'states':[]}
    with tempfile.TemporaryDirectory(prefix='termx-access-proof-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.authorization import ROLES
        from termx.audit import read_events
        state=AppState(passcode=None);owner=state.identity.setup_owner('access-owner',PASSWORD)
        member=state.identity.create_local_user('access-member',PASSWORD,list(ROLES['viewer']));state.authorization.set_role(member.id,'viewer')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        from termx.identity import Identity
        from termx.identity_adapters import SignedAssertionAdapter
        import jwt,uuid
        issuer='urn:termx:access-fixture';key=Ed25519PrivateKey.generate()
        state.identity.map_identity(Identity(issuer,'fixture-member','admin-binding'),member.id)
        adapter=SignedAssertionAdapter(state.identity,adapter_id='access-assertion',label='Fixture identity',issuer=issuer,audience='termx-access',public_key=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo).decode(),groups_claim='teams',membership_ttl=60)
        state.identity.register_adapter(adapter)
        for name in ('Allowed project','Unrelated project'):(root/name).mkdir()
        project=state.projects.register(str(root/'Allowed project'),name='Allowed project');other=state.projects.register(str(root/'Unrelated project'),name='Unrelated project')
        assets=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))))
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text());app=create_app(state,web_dir=assets)
        conversation=state.workspace.create_session(owner,title='Access proof',project_id=project['id'],cwd=project['path'])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        url=f'http://127.0.0.1:{port}';page=None
        def login(evidence):
            reply=httpx.post(url+'/auth/login',json={**evidence,'transport':'bearer','device_name':'Isolated Access fixture'},trust_env=False)
            assert reply.status_code==200,'Fixture sign-in failed'
            return reply.json()['access_token']
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page()
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                try:
                    page.goto(url+'/?session='+conversation['id']);page.get_by_label('Username',exact=True).fill('access-owner');page.get_by_label('Password',exact=True).fill(PASSWORD);page.get_by_role('button',name='Continue with password',exact=True).click();page.get_by_role('textbox',name='Message',exact=True).wait_for()
                    page.get_by_role('button',name='Managers',exact=True).click();page.get_by_role('button',name='Access & sessions',exact=True).click()
                    groups=page.get_by_role('region',name='Organizations and identity groups',exact=True);groups.wait_for();groups.get_by_text('Add organization',exact=True).click();groups.get_by_label('Organization name',exact=True).fill('Engineering');groups.get_by_role('button',name='Create organization',exact=True).click();groups.get_by_role('status').filter(has_text='Organization created').wait_for()
                    groups.get_by_label('Group name',exact=True).fill('Project readers');groups.get_by_label('Organization',exact=True).select_option(label='Engineering');groups.get_by_role('button',name='Save group',exact=True).click()
                    groups.get_by_role('combobox',name='Group',exact=True).select_option(label='Project readers');groups.get_by_label('Project',exact=True).select_option(project['id']);groups.get_by_label('Allowed action scopes',exact=True).fill('files-read,agent-view');groups.get_by_role('button',name='Save project grant',exact=True).click();groups.get_by_role('status').filter(has_text='Project grant saved').wait_for()
                    groups.get_by_label('Principal',exact=True).select_option(member.id);groups.get_by_role('button',name='Add member',exact=True).click();groups.get_by_role('status').filter(has_text='Membership granted').wait_for();groups.get_by_role('button',name='Remove membership',exact=True).wait_for()
                    credentials=login({'username':'access-member','password':PASSWORD})
                    assert state.authorization.can(credentials,'files-read',project_id=project['id'])
                    assert not state.authorization.can(credentials,'files-read',project_id=other['id'])
                    headers={'Authorization':'Bearer '+credentials}
                    assert httpx.get(url+'/auth/admin/groups',headers=headers,trust_env=False).status_code==403
                    member_context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                    try:
                        member_page=member_context.new_page();member_page.goto(url);member_page.get_by_label('Username',exact=True).fill('access-member');member_page.get_by_label('Password',exact=True).fill(PASSWORD);member_page.get_by_role('button',name='Continue with password',exact=True).click()
                        member_page.get_by_role('button',name='Managers',exact=True).click();member_page.get_by_role('button',name='Access & sessions',exact=True).click();member_page.get_by_role('heading',name='Your sessions',exact=True).wait_for()
                        assert member_page.get_by_role('region',name='Organizations and identity groups',exact=True).count()==0
                        assert member_page.get_by_role('region',name='Audit retention',exact=True).count()==0
                        assert member_page.get_by_role('region',name='Device session lifetimes',exact=True).count()==0
                    finally:member_context.close()
                    groups.get_by_role('button',name='Remove membership',exact=True).click();groups.get_by_role('status').filter(has_text='Membership removed').wait_for()
                    assert httpx.get(url+'/auth/access',headers=headers,trust_env=False).status_code==401
                    report['group_effects']={'organization_created':True,'project_scope_only':True,'member_admin_routes_denied':True,'administrative_controls_hidden_from_member':True,'membership_removal_revoked_existing_session':True}
                    groups.get_by_label('Trusted provider',exact=True).select_option(issuer);groups.get_by_label('Exact group claim value',exact=True).fill('project-readers');groups.get_by_role('button',name='Save claim mapping',exact=True).click();groups.get_by_role('status').filter(has_text='Mapping saved').wait_for()
                    now=int(time());assertion=jwt.encode({'iss':issuer,'aud':'termx-access','sub':'fixture-member','iat':now,'exp':now+60,'jti':uuid.uuid4().hex,'teams':['project-readers','unmapped-administrators']},key,algorithm='EdDSA',headers={'typ':'termx-identity+jwt'})
                    mapped=login({'method':adapter.id,'assertion':assertion})
                    assert state.authorization.can(mapped,'files-read',project_id=project['id']) and not state.authorization.can(mapped,'host-admin')
                    groups.get_by_role('button',name='Remove mapping',exact=True).click();groups.get_by_role('status').filter(has_text='Mapping removed').wait_for();assert httpx.get(url+'/auth/access',headers={'Authorization':'Bearer '+mapped},trust_env=False).status_code==401
                    report['provider_mapping']={'exact_configured_issuer_claim':True,'signed_assertion_verified':True,'unmapped_admin_text_not_authority':True,'mapping_removal_revoked_existing_session':True}
                    group=state.identity.groups.inventory()['groups'][0]
                    groups.get_by_label('Group name',exact=True).fill('My unsaved name')
                    state.identity.groups.save(identifier=group['id'],revision=group['revision'],label='Other administrator name',organization_id=group['organization_id'],role='viewer',actor_id=owner.id)
                    groups.get_by_role('button',name='Refresh group inventory',exact=True).click();groups.get_by_role('alert').wait_for();assert groups.get_by_label('Group name',exact=True).input_value()=='My unsaved name';assert groups.get_by_role('button',name='Save group',exact=True).is_disabled()
                    groups.get_by_role('button',name='Keep my draft using the reviewed current revision',exact=True).click();groups.get_by_role('button',name='Save group',exact=True).click();groups.get_by_role('combobox',name='Group',exact=True).select_option(label='My unsaved name')
                    report['revision_conflict']={'draft_preserved':True,'stale_save_disabled':True,'explicit_current_revision_review':True,'exact_draft_saved':True}
                    audit=page.get_by_role('region',name='Audit retention',exact=True);audit.get_by_label('Keep records for days',exact=True).fill('1');audit.get_by_label('Maximum audit bytes',exact=True).fill('65536');audit.get_by_label('Maximum audit records',exact=True).fill('100');assert audit.get_by_role('button',name='Save limits and prune',exact=True).is_disabled()
                    path=root/'config'/'audit.jsonl';expired=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
                    with path.open('a') as output:output.write(json.dumps({'ts':expired,'kind':'fixture-expired','nested':{'password':'fixture-secret-excluded'}})+'\n')
                    audit.get_by_label('I understand that applying retention removes older records.',exact=True).check();audit.get_by_role('button',name='Save limits and prune',exact=True).click();audit.get_by_role('status').filter(has_text='Removed').wait_for();assert not any(row.get('kind')=='fixture-expired' for row in read_events());assert any(row.get('kind')=='audit_retention_changed' for row in read_events());assert not audit.get_by_label('I understand that applying retention removes older records.',exact=True).is_checked()
                    report['audit_effects']={'exact_limits_saved':True,'acknowledgement_required':True,'expired_fixture_removed':True,'retention_operation_recorded':True,'confirmation_reset':True}
                    lifetimes=page.get_by_role('region',name='Device session lifetimes',exact=True)
                    lifetimes.get_by_label('Idle session limit in seconds',exact=True).fill('600')
                    lifetimes.get_by_label('Absolute session limit in seconds',exact=True).fill('1200')
                    with page.expect_response(lambda response:response.url==url+'/auth/admin/session-policy' and response.request.method=='PUT') as saved_policy:
                        lifetimes.get_by_role('button',name='Save session limits',exact=True).click()
                    assert saved_policy.value.status==200 and saved_policy.value.json()['revision']==2
                    lifetimes.get_by_role('status').filter(has_text='Session limits saved.').wait_for()
                    policy=state.identity.session_policy.inventory()
                    assert policy['revision']==2 and policy['idle_ttl_seconds']==600 and policy['absolute_ttl_seconds']==1200
                    with state.identity._db() as db:
                        cap=dict(db.execute('SELECT expires,idle_ttl_seconds FROM sessions WHERE principal_id=? AND revoked=0 ORDER BY created LIMIT 1',(owner.id,)).fetchone())
                    lifetimes.get_by_label('Idle session limit in seconds',exact=True).fill('700')
                    lifetimes.get_by_label('Absolute session limit in seconds',exact=True).fill('1400')
                    state.identity.session_policy.update(revision=2,idle_ttl_seconds=900,absolute_ttl_seconds=1800,actor_id=owner.id)
                    lifetimes.get_by_role('button',name='Refresh session limits',exact=True).click()
                    lifetimes.get_by_role('alert').wait_for()
                    assert lifetimes.get_by_label('Idle session limit in seconds',exact=True).input_value()=='700'
                    assert lifetimes.get_by_label('Absolute session limit in seconds',exact=True).input_value()=='1400'
                    assert lifetimes.get_by_role('button',name='Save session limits',exact=True).is_disabled()
                    lifetimes.get_by_role('button',name='Keep my draft using the reviewed policy revision',exact=True).click()
                    with page.expect_response(lambda response:response.url==url+'/auth/admin/session-policy' and response.request.method=='PUT') as saved_policy:
                        lifetimes.get_by_role('button',name='Save session limits',exact=True).click()
                    assert saved_policy.value.status==200 and saved_policy.value.json()['revision']==4
                    lifetimes.get_by_role('status').filter(has_text='Session limits saved.').wait_for()
                    policy=state.identity.session_policy.inventory()
                    assert policy['revision']==4 and policy['idle_ttl_seconds']==700 and policy['absolute_ttl_seconds']==1400
                    with state.identity._db() as db:
                        final=dict(db.execute('SELECT expires,idle_ttl_seconds FROM sessions WHERE principal_id=? AND revoked=0 ORDER BY created LIMIT 1',(owner.id,)).fetchone())
                    assert final==cap and cap['idle_ttl_seconds']==600
                    report['session_policy_effects']={'administrator_controls_visible':True,'member_controls_hidden':True,'exact_limits_saved':True,'draft_preserved_on_concurrent_revision':True,'stale_save_disabled':True,'explicit_current_revision_review':True,'existing_device_limits_never_widen':True,'access_ttl_seconds':policy['access_ttl_seconds']}
                    axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                    for zoom in (100,200):
                        page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                        for theme in ('dark','light'):
                            if page.locator('html').get_attribute('data-theme')!=theme:
                                page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                            if page.get_by_role('button',name='Close navigation',exact=True).count():page.get_by_role('button',name='Close navigation',exact=True).click()
                            for control,name in ((groups.get_by_label('Group name',exact=True),'group-editor'),(audit.get_by_label('Keep records for days',exact=True),'audit-retention'),(lifetimes.get_by_label('Idle session limit in seconds',exact=True),'session-lifetimes')):
                                control.scroll_into_view_if_needed();control.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab');assert control.evaluate('(element)=>document.activeElement===element')
                                assert control.evaluate('element=>{const b=element.getBoundingClientRect();return element.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))}'),'Focused control is covered'
                                page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                                entry={'surface':name,'theme':theme,'zoom':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':row['id'],'targets':[node['target'] for node in row['nodes']]} for row in result['violations']],'incomplete':[row['id'] for row in result['incomplete']]};report['states'].append(entry);assert not entry['overflow'] and not entry['violations'],entry;page.screenshot(path=str(destination/f'{name}-{theme}-{zoom}.png'))
                    assert not report['errors'];report['status']='passed'
                except Exception:
                    if page and not page.is_closed():page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                    raise
                finally:context.close();browser.close()
        finally:server.should_exit=True;worker.join(timeout=15);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
