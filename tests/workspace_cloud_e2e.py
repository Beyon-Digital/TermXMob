"""Rendered desktop -> real host -> mock cloud APIs -> real loopback SSH worker.

Only provider infrastructure and bootstrap are substituted; no billable VMs.
"""
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import sync_playwright, expect
from machine_ssh_fixture import ssh_machine
from cloud_fixtures import cloud, CREDENTIALS, launch_plan


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'steps':[],'errors':[],'cloud_apis':'mocked','worker_transport':'real loopback SSH'}
    with tempfile.TemporaryDirectory(prefix='termx-cloud-browser-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.providers import ProviderTurn
        state=AppState(passcode=None,credentials=CredentialStore(memory={'fixture':'private-cloud-fixture'}))
        owner=state.identity.setup_owner('cloud-fixture','cloud-fixture-password-123')
        folder=root/'project';folder.mkdir()
        project=state.projects.register(str(folder),'Cloud project')
        state.agent_store.put_provider('fixture',kind='openai-compatible',name='Fixture API',base_url='https://fixture.invalid',model='fixture-model',capabilities=['shell'],secret_configured=True)
        providers={kind:cloud(kind) for kind in ('aws','azure','gcp')}
        with ssh_machine(root/'ssh') as config:
            machine_root=Path(config['root']);machine_root.mkdir()
            (machine_root/'.termx-runtime').symlink_to(sys.prefix,target_is_directory=True)
            class Provider:
                async def turn(self,**kwargs):
                    assert kwargs['cwd']==config['root']
                    return ProviderTurn('fixture-turn','Cloud machine worker verified over SSH.',[],{})
            state.agent._adapter=lambda _:Provider()
            app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
            state.cloud.provider_factory=lambda kind,credentials:providers[kind].factory(kind,credentials)
            async def attach(d,connection,keys):
                row=state.runners.machines.register(owner.id,project['id'],{**config,'name':d['plan']['name'],'cloud_deployment':d['id'],'policy_version':owner.policy_version})
                state.cloud.update(owner.id,d['id'],runner_id=row['id'])
                await state.runners.machines.connect(owner.id,row['id'])
            state.cloud.attach=attach
            sessions={kind:state.workspace.create_session(owner,title=kind.upper()+' cloud session',project_id=project['id'],cwd=str(folder),engine='internal',provider_id='fixture',model='fixture-model',mode='ask') for kind in providers}
            listener=socket.socket();listener.bind(('127.0.0.1',0));origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
            worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
            deadline=monotonic()+20
            while not server.started:
                if monotonic()>deadline:raise RuntimeError('Host did not start')
                sleep(.05)
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1100});page.set_default_timeout(30000)
                    expect.set_options(timeout=30000)
                    page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    page.goto(origin)
                    page.get_by_label('Username',exact=True).fill('cloud-fixture')
                    page.get_by_label('Password',exact=True).fill('cloud-fixture-password-123')
                    page.get_by_role('button',name='Continue with password').click()
                    for kind in providers:
                        page.get_by_role('button',name='Models & tools',exact=True).click()
                        page.get_by_role('button',name='Cloud accounts',exact=True).click()
                        page.get_by_label('Account name',exact=True).fill(kind.upper()+' test account')
                        page.get_by_label('Cloud provider',exact=True).select_option(kind)
                        labels={'aws':{'access_key_id':'AWS access key ID','secret_access_key':'AWS secret access key'},'azure':{'tenant_id':'Azure tenant ID','subscription_id':'Azure subscription ID','client_id':'Azure application client ID','client_secret':'Azure client secret'}}
                        if kind=='gcp':page.get_by_label('GCP service-account JSON').fill(json.dumps(CREDENTIALS[kind]))
                        else:
                            for key,label in labels[kind].items():page.get_by_label(label,exact=True).fill(CREDENTIALS[kind][key])
                        page.get_by_role('button',name='Verify and save account',exact=True).click()
                        expect(page.get_by_text('Authentication verified and saved.',exact=False)).to_be_visible()
                        account=next(a for a in state.cloud.accounts(owner.id) if a['provider']==kind)
                        plan=launch_plan(kind,account['id'])
                        page.get_by_role('button',name='Runners',exact=True).click()
                        page.get_by_role('button',name='Create cloud machine',exact=True).click()
                        page.get_by_label('Provider account',exact=True).select_option(account['id'])
                        page.get_by_label('Cloud runner name',exact=True).fill(kind.upper()+' test machine')
                        if page.get_by_label('Execution project',exact=True).is_enabled():page.get_by_label('Execution project',exact=True).select_option(project['id'])
                        for label,key in [('Region','region'),('Availability zone','zone'),('Machine size','size'),('Operating system','image')]:
                            control=page.get_by_label(label,exact=True)
                            expect(control.locator('option[value="'+plan[key]+'"]')).to_be_attached()
                            expect(control).to_be_enabled();control.select_option(plan[key])
                        page.get_by_label('Boot volume type',exact=True).select_option(plan['boot']['type'])
                        page.get_by_role('button',name='Add data volume',exact=True).click()
                        page.get_by_label('Data volume 1 size (GiB)',exact=True).fill('64')
                        page.get_by_label('Allowed SSH source CIDR',exact=True).fill('203.0.113.1/32')
                        page.get_by_role('checkbox',name='Allow agents to use',exact=False).check()
                        page.get_by_role('checkbox',name='I authorize provider usage charges',exact=False).check()
                        page.get_by_role('button',name='Review provisioning',exact=True).click()
                        expect(page.get_by_role('region',name='Review cloud provisioning')).to_be_visible()
                        assert not providers[kind].rows,'Review must not create any resources'
                        page.screenshot(path=str(destination/(kind+'-review.png')),full_page=True)
                        page.get_by_role('button',name='Create reviewed machine',exact=True).click()
                        card=page.locator('.cloud-deployment').filter(has=page.get_by_role('heading',name=kind.upper()+' test machine',exact=True))
                        expect(card.locator('.status-badge')).to_have_text('ready')
                        deployment=next(d for d in state.cloud.list(owner.id) if d['provider']==kind)
                        assert deployment['plan']['volumes'][0]['size_gb']==64
                        page.get_by_role('button',name=kind.upper()+' cloud session',exact=True).click()
                        page.get_by_role('button',name='Session options').click()
                        page.get_by_role('combobox',name='Session execution location').select_option(deployment['runner_id'])
                        page.keyboard.press('Escape')
                        page.get_by_role('textbox',name='Message',exact=True).fill('Verify this cloud machine worker.')
                        page.get_by_role('button',name='Send message',exact=True).click()
                        page.get_by_text('Cloud machine worker verified over SSH.',exact=False).first.wait_for()
                        task=state.workspace.session(owner,sessions[kind]['id'])['turns'][-1]['task']
                        assert task['cwd']==config['root'] and task['status']=='completed'
                        page.get_by_role('button',name='Runners',exact=True).click()
                        card.get_by_role('button',name='Clean up machine',exact=True).click()
                        expect(card.get_by_role('group',name='Review cloud cleanup')).to_be_visible()
                        assert providers[kind].rows
                        card.get_by_role('button',name='Delete managed resources',exact=True).click()
                        expect(card.locator('.status-badge')).to_have_text('deleted')
                        assert not providers[kind].rows
                        page.screenshot(path=str(destination/(kind+'-deleted.png')),full_page=True)
                        report['steps'].append(kind.upper()+': Settings authentication → configuration/review → provision → real SSH agent turn → reviewed cleanup; no owned resources left')
                    assert not report['errors'],report['errors']
                    browser.close()
            finally:
                server.should_exit=True;worker.join(timeout=20);listener.close()
                (destination/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
