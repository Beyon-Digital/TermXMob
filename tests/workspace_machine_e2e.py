"""Rendered desktop -> real host -> loopback SSH machine; no cloud billing."""
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


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'steps':[],'errors':[]}
    with tempfile.TemporaryDirectory(prefix='termx-machine-browser-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.providers import ProviderTurn
        state=AppState(passcode=None,credentials=CredentialStore(memory={'fixture':'private-ssh-fixture'}))
        owner=state.identity.setup_owner('machine-fixture','machine-fixture-password-123')
        folder=root/'project';folder.mkdir()
        project=state.projects.register(str(folder),'Machine project')
        state.agent_store.put_provider('fixture',kind='openai-compatible',name='Fixture API',base_url='https://fixture.invalid',model='fixture-model',capabilities=['shell'],secret_configured=True)
        with ssh_machine(root/'ssh') as config:
            machine_root=Path(config['root']);machine_root.mkdir()
            (machine_root/'.termx-runtime').symlink_to(sys.prefix,target_is_directory=True)
            (machine_root/'persistent.txt').write_text('kept between runs')
            class Provider:
                async def turn(self,**kwargs):
                    assert kwargs['cwd']==config['root']
                    return ProviderTurn('fixture-turn','Verified reply from the SSH machine.',[],{})
            state.agent._adapter=lambda _:Provider()
            app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
            session=state.workspace.create_session(owner,title='Machine session',project_id=project['id'],cwd=str(folder),engine='internal',provider_id='fixture',model='fixture-model',mode='ask')
            listener=socket.socket();listener.bind(('127.0.0.1',0));origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
            worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
            deadline=monotonic()+20
            while not server.started:
                if monotonic()>deadline:raise RuntimeError('Host did not start')
                sleep(.05)
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(executable_path=os.environ.get('TERMX_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1100});page.set_default_timeout(30000)
                    page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    page.goto(origin)
                    page.get_by_label('Username',exact=True).fill('machine-fixture')
                    page.get_by_label('Password',exact=True).fill('machine-fixture-password-123')
                    page.get_by_role('button',name='Continue with password').click()
                    page.get_by_role('button',name='Runners',exact=True).click()
                    page.get_by_role('button',name='Add machine',exact=True).click()
                    page.get_by_label('Machine name',exact=True).fill(config['name'])
                    page.get_by_label('Machine project',exact=True).select_option(project['id'])
                    page.get_by_label('SSH hostname or IP',exact=True).fill(config['host'])
                    page.get_by_label('SSH user',exact=True).fill(config['user'])
                    page.get_by_label('SSH port',exact=True).fill(str(config['port']))
                    page.get_by_label('Persistent workspace directory',exact=True).fill(config['root'])
                    page.get_by_text('SSH authentication on the control plane',exact=True).click()
                    page.get_by_label('Private key file path',exact=True).fill(config['identity_file'])
                    page.get_by_label('Known hosts file path',exact=True).fill(config['known_hosts_file'])
                    page.get_by_role('checkbox').check()
                    page.screenshot(path=str(destination/'machine-enrollment.png'))
                    page.get_by_role('button',name='Register machine',exact=True).click()
                    page.get_by_role('button',name='Check connection',exact=True).click()
                    expect(page.locator('.machine-card .status-badge')).to_have_text('ready')
                    row=state.runners.list(owner.id)[0]
                    report['steps'].append('Rendered enrollment persists SSH settings and verifies the real remote worker')
                    page.screenshot(path=str(destination/'machine-ready.png'))
                    page.get_by_role('button',name='Machine session',exact=True).click()
                    page.get_by_role('button',name='Session options').click()
                    target=page.get_by_role('combobox',name='Session execution location')
                    target.select_option(row['id']);expect(target).to_have_value(row['id'])
                    expect(page.get_by_text('Commands use the machine’s SSH account permissions.',exact=False)).to_be_visible()
                    page.keyboard.press('Escape')
                    page.get_by_role('textbox',name='Message',exact=True).fill('Check the remote machine connection.')
                    page.get_by_role('button',name='Send message',exact=True).click()
                    page.get_by_text('Verified reply from the SSH machine.',exact=False).first.wait_for()
                    task=state.workspace.session(owner,session['id'])['turns'][-1]['task']
                    assert task['cwd']==config['root'] and task['status']=='completed'
                    report['steps'].append('Session selector dispatches an actual SSH agent turn with the configured workspace')
                    page.get_by_role('button',name='Runners',exact=True).click()
                    page.get_by_role('button',name='Disable execution',exact=True).click()
                    expect(page.locator('.machine-card .status-badge')).to_have_text('stopped')
                    assert (machine_root/'persistent.txt').read_text()=='kept between runs'
                    page.get_by_role('button',name='Check connection',exact=True).click()
                    expect(page.locator('.machine-card .status-badge')).to_have_text('ready')
                    page.get_by_role('button',name='Remove connection',exact=True).click()
                    page.get_by_role('button',name='Confirm remove',exact=True).click()
                    expect(page.locator('.machine-card .status-badge')).to_have_text('removed')
                    assert (machine_root/'persistent.txt').exists()
                    report['steps'].append('Disable, reconnect and remove preserve machine files and require no cloud lifecycle effects')
                    page.screenshot(path=str(destination/'machine-removed.png'))
                    assert not report['errors'],report['errors']
                    browser.close()
            finally:
                server.should_exit=True;worker.join(timeout=20);listener.close()
                (destination/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
