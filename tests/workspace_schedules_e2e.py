"""Standalone desktop -> real host scheduler -> agent ledger, fixture provider.

Build desktop/workspace first, then run with TERMX_CHROMIUM_EXECUTABLE set when
using system Chromium. No paid model calls or real project commands are issued.
"""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from datetime import datetime, timezone
from time import time, monotonic, sleep

import uvicorn
from playwright.sync_api import sync_playwright, expect


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'steps':[],'errors':[],'provider':'deterministic fixture; no billing'}
    with tempfile.TemporaryDirectory(prefix='termx-schedules-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.providers import ProviderTurn
        state=AppState(passcode=None,credentials=CredentialStore(memory={'fixture':'private-fixture'}))
        owner=state.identity.setup_owner('scheduler','scheduler-fixture-password-123')
        folder=root/'project';folder.mkdir()
        project=state.projects.register(str(folder),'Scheduled project')
        state.agent_store.put_provider('fixture',kind='openai-compatible',name='Fixture API',base_url='https://fixture.invalid',model='fixture-model',capabilities=['shell'],secret_configured=True)
        class Provider:
            async def turn(self,**kwargs): return ProviderTurn('schedule-fixture','Scheduled verification completed.',[],{})
        state.agent._adapter=lambda _:Provider()
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        clock=[time()];state.automation.clock=lambda:clock[0]
        chat=state.workspace.create_session(owner,title='Scheduled verification',project_id=project['id'],cwd=str(folder),engine='internal',provider_id='fixture',model='fixture-model',mode='ask')
        listener=socket.socket();listener.bind(('127.0.0.1',0));origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+20
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('TERMX_CHROMIUM_EXECUTABLE') or None,args=['--no-sandbox','--disable-dev-shm-usage'])
                context=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='UTC')
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(origin)
                page.get_by_label('Username',exact=True).fill('scheduler')
                page.get_by_label('Password',exact=True).fill('scheduler-fixture-password-123')
                page.get_by_role('button',name='Continue with password',exact=True).click()
                page.get_by_role('button',name='Automations',exact=True).first.click()
                page.get_by_role('button',name='New scheduled task').click()
                expect(page.get_by_role('form',name='Create scheduled task')).to_be_visible()
                page.get_by_role('combobox',name='Conversation',exact=True).select_option(chat['id'])
                page.get_by_label('Success criteria',exact=True).fill('Report the scheduled result')
                page.get_by_label('Task prompt',exact=True).fill('Scheduled fixture checks')
                page.get_by_role('combobox',name='Frequency',exact=False).select_option('cron')
                page.get_by_label('Cron expression',exact=True).fill('*/10 * * * *')
                page.get_by_label('Timezone',exact=True).fill('UTC')
                page.get_by_role('button',name='Preview next runs',exact=True).click()
                expect(page.get_by_text('Upcoming runs (shown in this device’s timezone)')).to_be_visible()
                page.get_by_label('Allow unattended runs within these limits.',exact=False).check()
                page.get_by_role('button',name='Authorize and create schedule').click()
                expect(page.get_by_role('form',name='Create scheduled task')).to_have_count(0)
                expect(page.get_by_role('cell',name='*/10 * * * * · UTC',exact=True)).to_be_visible()
                report['steps'].append('Rendered cron editor previews timezone-aware occurrences and creates an explicitly authorized schedule')
                page.get_by_role('button',name='Pause',exact=True).click()
                expect(page.get_by_role('button',name='Resume',exact=True)).to_be_visible()
                page.get_by_role('button',name='Run now',exact=True).click()
                page.get_by_role('button',name='Scheduled fixture checks',exact=True).click()
                expect(page.locator('.schedule-run')).to_contain_text('completed',timeout=30000)
                assert state.workspace.session(owner,chat['id'])['turns'][-1]['task']['status']=='completed'
                report['steps'].append('Run now executes through the real host agent ledger while leaving the recurrence paused')
                page.reload()
                page.get_by_role('button',name='Automations',exact=True).first.click()
                expect(page.get_by_role('button',name='Resume',exact=True)).to_be_visible()
                page.get_by_role('button',name='Edit',exact=True).click()
                page.get_by_role('combobox',name='Frequency',exact=False).select_option('once')
                once=int((clock[0]+120)//60)*60
                page.get_by_label('Date and time (this device)',exact=True).fill(datetime.fromtimestamp(once,timezone.utc).strftime('%Y-%m-%dT%H:%M'))
                page.get_by_role('combobox',name='When the host misses a run',exact=False).select_option('once')
                page.get_by_role('button',name='Save schedule',exact=True).click()
                expect(page.get_by_role('form',name='Edit scheduled task')).to_have_count(0)
                page.get_by_role('button',name='Resume',exact=True).click()
                expect(page.get_by_role('button',name='Pause',exact=True)).to_be_visible()
                page.goto('about:blank')
                clock[0]=once+1
                deadline=monotonic()+30
                while len(state.workspace.session(owner,chat['id'])['turns'])<2:
                    if monotonic()>deadline:raise AssertionError('Scheduled dispatch did not run without the UI')
                    sleep(.1)
                schedule=state.workspace.store.list('schedule',owner.id)[0]
                assert not schedule['enabled'] and schedule['next_run'] is None
                report['steps'].append('Edited one-time schedule dispatches from the daemon after the browser leaves and automatically stops recurrence')
                page.goto(origin)
                page.get_by_role('button',name='Automations',exact=True).first.click()
                page.get_by_role('button',name='Scheduled fixture checks',exact=True).click()
                page.get_by_role('button',name='Archive schedule').click()
                expect(page.get_by_role('button',name='Run now',exact=True)).to_have_count(0)
                page.get_by_label('Show archived schedules',exact=True).check()
                expect(page.get_by_role('cell',name='archived',exact=True)).to_be_visible()
                page.get_by_label('Show archived schedules',exact=True).uncheck()
                page.get_by_role('button',name='New scheduled task').click()
                page.get_by_role('combobox',name='Task type',exact=False).select_option('command')
                page.get_by_role('combobox',name='Conversation',exact=True).select_option(chat['id'])
                page.get_by_label('Success criteria',exact=True).fill('Exit zero')
                page.get_by_label('Task prompt',exact=True).fill('Shell cron proof')
                page.get_by_label('Shell command',exact=True).fill('printf shell-cron-browser-proof')
                page.get_by_role('combobox',name='Frequency',exact=False).select_option('cron')
                page.get_by_label('Cron expression',exact=True).fill('*/10 * * * *')
                page.get_by_label('Allow unattended runs within these limits.',exact=False).check()
                page.get_by_role('button',name='Authorize and create schedule').click()
                expect(page.get_by_role('form',name='Create scheduled task')).to_have_count(0)
                page.get_by_role('button',name='Run now',exact=True).click()
                page.get_by_role('button',name='Shell cron proof',exact=True).click()
                expect(page.locator('.schedule-run')).to_contain_text('shell-cron-browser-proof',timeout=30000)
                report['steps'].append('Direct shell cron job runs a real harmless command and displays persisted output without a model call')
                page.screenshot(path=str(destination/'schedules-desktop.png'))
                report['steps'].append('Reload preserves state; archive removes future execution while retaining history')
                assert not report['errors'],report['errors']
                browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=20);listener.close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1]),indent=2))
