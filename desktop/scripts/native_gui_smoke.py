#!/usr/bin/env python3
"""Exercise an unchanged installed Tauri binary through native W3C WebDriver.

No IPC mocking, dev server, production test endpoints or provider accounts.
All app/profile state lives below a fresh fixture directory. The platform's
actual credential service stores a credential for that fixture's unique host.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ELEMENT = 'element-6066-11e4-a52e-4f735466cecf'


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait(check, description, timeout=90):
    deadline = monotonic() + timeout
    last = None
    while monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (RuntimeError, OSError) as error:
            last = str(error)
        sleep(.25)
    raise RuntimeError(f'Timed out waiting for {description}: {last or "condition unmet"}')


def fixture_environment(root: Path):
    root = root.resolve()
    environment = dict(os.environ)
    paths = {'XDG_DATA_HOME': root/'data', 'XDG_CONFIG_HOME': root/'config',
             'XDG_CACHE_HOME': root/'cache', 'APPDATA': root/'roaming',
             'LOCALAPPDATA': root/'local', 'WEBVIEW2_USER_DATA_FOLDER': root/'webview'}
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    environment['TERMX_DESKTOP_DATA_DIR'] = str(root/'native')
    # Keep the caller's HOME, OS keyring and D-Bus session unchanged.
    return environment


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


class NativeDriver:
    def __init__(self, binary, driver, native_driver, environment, output):
        self.binary = binary
        self.driver = driver
        self.native_driver = native_driver
        self.environment = environment
        self.output = output
        self.session = None
        self.process = None
        self.log = None

    def call(self, method, path, data=None, session=True):
        if session:
            path = '/session/'+self.session+path
        body = None if data is None else json.dumps(data).encode()
        request = Request(self.origin+path, data=body, method=method,
                          headers={'Content-Type':'application/json'})
        try:
            response = urlopen(request, timeout=180)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read() or b'{}')
        value = result.get('value')
        if isinstance(value, dict) and value.get('error'):
            raise RuntimeError(value.get('message', value['error'])[:1000])
        return value

    def start(self):
        port, native_port = free_port(), free_port()
        while native_port == port:
            native_port = free_port()
        self.origin = f'http://127.0.0.1:{port}'
        args = [str(self.driver), '--port', str(port), '--native-port', str(native_port)]
        if self.native_driver:
            args += ['--native-driver', str(self.native_driver)]
        self.log = (self.output/'webdriver.log').open('ab')
        self.process = subprocess.Popen(args, env=self.environment, stdout=self.log,
                                        stderr=self.log, start_new_session=os.name != 'nt')
        wait(lambda:self.call('GET', '/status', session=False).get('ready'), 'native driver startup', 30)
        result = self.call('POST', '/session', {'capabilities':{'alwaysMatch':{
            'browserName':'wry', 'tauri:options':{'application':str(self.binary)}}}}, session=False)
        self.session = result['sessionId']
        self.call('POST', '/timeouts', {'script':60000, 'pageLoad':90000, 'implicit':0})
        wait(lambda:self.script('return location.hostname === "127.0.0.1" && !!window.__TAURI_INTERNALS__ && !!document.querySelector(".sign-in,.workspace-root")'),
             'installed host workspace', 180)

    def script(self, script, *args):
        return self.call('POST', '/execute/sync', {'script':script, 'args':list(args)})

    def invoke(self, command, payload):
        result = self.call('POST', '/execute/async', {'script':
            'const done=arguments[arguments.length-1];'
            'window.__TAURI_INTERNALS__.invoke(arguments[0],arguments[1]).then('
            'value=>done({ok:true,value}),error=>done({ok:false,error:String(error)}));',
            'args':[command,payload]})
        if not result['ok']:
            raise RuntimeError(result['error'])
        return result['value']

    def api(self, path, method='GET', body=None):
        return self.invoke('workspace_request', {'path':path, 'method':method, 'data':body})

    def element(self, value, using='css selector'):
        found = self.call('POST', '/elements', {'using':using,'value':value})
        return found[0][ELEMENT] if found else None

    def click(self, value, using='css selector'):
        element = wait(lambda:self.element(value,using), 'control '+value)
        self.call('POST', '/element/'+element+'/click', {})

    def type(self, selector, text):
        element = wait(lambda:self.element(selector), 'input '+selector)
        self.call('POST', '/element/'+element+'/clear', {})
        self.call('POST', '/element/'+element+'/value', {'text':text})

    def screenshot(self, name):
        pixels = base64.b64decode(self.call('GET', '/screenshot'))
        assert pixels.startswith(b'\x89PNG'), 'Native screenshot must contain actual pixels'
        (self.output/(name+'.png')).write_bytes(pixels)

    def stop(self):
        # Capture descendants before stopping the driver: the sidecar starts a
        # separate process group but still belongs to this fixture's app tree.
        owned = []
        if self.process and os.name != 'nt':
            listing = subprocess.run(['ps','-A','-o','pid=,ppid='],capture_output=True,text=True,check=True)
            rows = [tuple(map(int,line.split())) for line in listing.stdout.splitlines() if line.strip()]
            frontier = {self.process.pid}
            while frontier:
                children = {pid for pid,parent in rows if parent in frontier}
                owned.extend(children);frontier = children
        if self.session:
            with suppress(Exception):self.call('DELETE', '')
            self.session = None
        if self.process:
            if os.name == 'nt':
                subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True)
            else:
                for pid in [*reversed(owned),self.process.pid]:
                    with suppress(ProcessLookupError):os.kill(pid,signal.SIGTERM)
                sleep(.5)
                for pid in [*reversed(owned),self.process.pid]:
                    with suppress(ProcessLookupError):os.kill(pid,signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):self.process.wait(timeout=5)
            self.process = None
        if self.log:self.log.close();self.log=None


def assert_workspace(driver):
    wait(lambda:driver.element('.workspace-root'), 'authenticated native workspace')
    result = driver.api('/auth/me')
    assert result['status'] == 200 and result['body']['session_id']
    assert not driver.script('return /[?&]k=/.test(location.href)'), 'Credential in native window URL'
    return result['body']


def password_signin(driver, username, password, setup=False):
    wait(lambda:driver.element('.sign-in'), 'password entry screen')
    driver.type('input[autocomplete="username"]',username)
    driver.type('input[type="password"][autocomplete]',password)
    driver.click('//button[normalize-space()="'+('Create owner account' if setup else 'Continue with password')+'"]','xpath')
    return assert_workspace(driver)


def terminal_transport(driver, session_id):
    """Actual installed restricted terminal and cookie-authenticated webview WS."""
    created = driver.api('/api/workspace/sessions/'+session_id+'/terminal', 'POST')
    assert created['status'] == 200, 'Installed restricted terminal unavailable: '+str(created)
    terminal_id = created['body']['id']
    try:
        result = driver.call('POST', '/execute/async', {'script': '''
const id=arguments[0],done=arguments[arguments.length-1];
const marker='TERMX_NATIVE_RESTRICTED_CONSOLE_OK';
let settled=false,text='';
const socket=new WebSocket(location.origin.replace(/^http/,'ws')+'/api/sessions/'+encodeURIComponent(id)+'/pty');
socket.binaryType='arraybuffer';
const timer=setTimeout(()=>finish({ok:false,error:'Authenticated terminal output deadline'}),25000);
function finish(value){if(settled)return;settled=true;clearTimeout(timer);socket.close();done(value)}
socket.onopen=()=>{socket.send(JSON.stringify({type:'resize',cols:100,rows:30}));socket.send(JSON.stringify({type:'input',data:'echo '+marker+'\\r'}))};
socket.onmessage=event=>{if(typeof event.data==='string')return;text+=new TextDecoder().decode(event.data);const plain=text.replace(/\\x1b\\[[0-?]*[ -/]*[@-~]/g,'');if(plain.split(/[\\r\\n]+/).some(line=>line.trim()===marker))finish({ok:true,connected:true,bytes:text.length})};
socket.onerror=()=>finish({ok:false,error:'Native authenticated terminal socket error'});
socket.onclose=event=>{if(!settled)finish({ok:false,error:'Native terminal socket closed '+event.code})};
''', 'args':[terminal_id]})
        assert result.get('ok') and result.get('connected'), str(result)
        return {'authenticated_webview_socket':True,'restricted_console_output_bytes':result['bytes']}
    finally:
        removed = driver.api('/graphql','POST',{'query':
            'mutation($id:String!){deleteSession(sessionId:$id){ok}}',
            'variables':{'id':terminal_id}})
        assert removed['status'] == 200 and not removed['body'].get('errors'), str(removed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--package',required=True,type=Path)
    parser.add_argument('--driver',required=True,type=Path)
    parser.add_argument('--native-driver',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    if sys.platform not in {'linux','win32'}:
        parser.error('Native platform WebDriver supports unchanged Linux/Windows binaries only')
    for path in [args.binary,args.package,args.driver]:
        if not path.is_file():parser.error(f'Required installed fixture asset missing: {path}')
    output = args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    report = {'platform':sys.platform,'installed_binary':str(args.binary.resolve()),
        'binary_sha256':file_digest(args.binary),
        'package_sha256':file_digest(args.package),
        'ipc_mocked':False,'provider_account_used':False,'steps':{},
        'limitations':['Native OIDC TLS/webview journey and macOS GUI require separate direct qualification']}
    with tempfile.TemporaryDirectory(prefix='termx-native-gui-') as directory:
        root = Path(directory)
        driver = NativeDriver(args.binary.resolve(),args.driver.resolve(),args.native_driver,
                              fixture_environment(root),output)
        try:
            driver.start()
            assert driver.script('return document.querySelector("h1")?.textContent') == 'Set up your workspace'
            username,password = 'native-fixture-owner','isolated-native-fixture-password-123'
            account = password_signin(driver,username,password,setup=True)
            report['steps']['fresh_native_owner_setup'] = True
            driver.screenshot('owner-workspace')
            config_files = list(root.rglob('desktop.json'))
            assert len(config_files) == 1, 'Native app state escaped the isolated fixture directories'
            data_dir = config_files[0].parent
            if os.name != 'nt':
                assert data_dir.stat().st_mode & 0o077 == 0, 'Native fixture directory is not private'
            report['steps']['isolated_native_configuration'] = True
            # Every host account and cached browser cookie is fixture-specific.
            driver.call('DELETE','/cookie')
            driver.stop()
            (data_dir/'workspace-window-main.json').write_text(json.dumps({
                'x':-20000,'y':-20000,'width':6000,'height':4000,'scale':1,
                'frame_width':16,'frame_height':40,'monitor':{
                    'name':'Removed fixture monitor','x':-20000,'y':-20000,
                    'width':6000,'height':4000,'scale':1}}))
            driver.start()
            restored = assert_workspace(driver)
            assert restored['principal']['id'] == account['principal']['id']
            report['steps']['cookie_cleared_os_keyring_restart'] = True
            rect = driver.call('GET','/window/rect')
            screen = driver.script('return {x:screen.availLeft||0,y:screen.availTop||0,width:screen.availWidth,height:screen.availHeight}')
            assert rect['x'] >= screen['x'] and rect['y'] >= screen['y']
            assert rect['x']+rect['width'] <= screen['x']+screen['width']+2
            assert rect['y']+rect['height'] <= screen['y']+screen['height']+2
            report['steps']['removed_monitor_titlebar_recovery'] = {'window':rect,'work_area':screen}
            created = driver.api('/api/workspace/sessions','POST',{'title':'Native fixture conversation','engine':'internal','mode':'ask'})
            assert created['status'] == 200
            session_id = created['body']['id']
            report['steps']['installed_restricted_terminal_transport'] = terminal_transport(driver, session_id)
            origin = driver.script('return location.origin')
            driver.call('POST','/url',{'url':origin+'/?'+urlencode({'session':session_id,'layout':'chat'})})
            assert_workspace(driver)
            wait(lambda:driver.script('const input=document.querySelector("textarea[aria-label=Message]");return !!input&&!input.disabled'),'selected conversation composer')
            driver.type('textarea[aria-label="Message"]','Native shared conversation draft')
            wait(lambda:driver.api('/api/workspace/sessions/'+session_id)['body'].get('draft_text') == 'Native shared conversation draft','canonical saved native draft')
            original = driver.call('GET','/window')
            label = driver.invoke('workspace_detach',{'sessionId':session_id,'panel':'chat'})
            handles = wait(lambda:(values if len(values)>1 else None) if (values:=driver.call('GET','/window/handles')) else None,'detached native window')
            detached = next(handle for handle in handles if handle != original)
            driver.call('POST','/window',{'handle':detached})
            assert_workspace(driver)
            assert driver.script('return new URL(location.href).searchParams.get("session")') == session_id
            assert driver.script('return window.__TAURI_INTERNALS__.metadata.currentWindow.label') == label
            wait(lambda:driver.script('return document.querySelector("textarea[aria-label=Message]")?.value === "Native shared conversation draft"'),'same draft in detached window')
            driver.screenshot('detached-workspace')
            driver.call('DELETE','/window')
            driver.call('POST','/window',{'handle':original})
            wait(lambda:(data_dir/('workspace-window-'+label+'.json')).is_file(),'independent detached placement file')
            reopened = driver.invoke('workspace_detach',{'sessionId':session_id,'panel':'chat'})
            assert reopened == label
            report['steps']['detach_close_reopen_stable_placement'] = True
            handles = driver.call('GET','/window/handles')
            detached = next(handle for handle in handles if handle != original)
            driver.call('POST','/window',{'handle':detached})
            wait(lambda:driver.script('return document.querySelector("textarea[aria-label=Message]")?.value === "Native shared conversation draft"'),'draft ready before return')
            draft='Native detached draft returned through acknowledged handoff'
            driver.type('textarea[aria-label="Message"]',draft)
            # Exercise the product's real two-phase handoff. The source renderer
            # persists/flushed state, main accepts, then native closes source.
            driver.click('//button[normalize-space()="Return to main workspace"]',using='xpath')
            wait(lambda:len(driver.call('GET','/window/handles'))==1,'acknowledged native re-dock closes detached window')
            driver.call('POST','/window',{'handle':original})
            wait(lambda:driver.script('return document.querySelector("textarea[aria-label=Message]")?.value === arguments[0]',draft),'main workspace retains detached draft')
            report['steps']['acknowledged_redock_preserves_draft'] = True
            driver.screenshot('redocked-workspace')
            current = assert_workspace(driver)
            revoked = driver.api('/auth/sessions/'+current['session_id'],'DELETE')
            assert revoked['status'] == 200
            try:
                denied = driver.api('/api/workspace/sessions')
            except RuntimeError as denial:
                assert any(word in str(denial).lower() for word in ['expired','revoked','sign in']), 'Unexpected native transport failure'
            else:
                assert denied['status'] in {401,403}, 'Revoked native access still authorizes protected data'
            driver.call('POST','/refresh',{})
            password_signin(driver,username,password)
            report['steps']['live_native_session_revocation'] = True
            driver.click('[aria-label="Sign out"]')
            wait(lambda:driver.element('.sign-in'),'native logout')
            driver.call('DELETE','/cookie')
            driver.stop();driver.start()
            wait(lambda:driver.element('.sign-in'),'logout remains revoked after restart')
            report['steps']['native_logout_clears_os_refresh'] = True
            driver.screenshot('signed-out')
            report['passed'] = True
        except Exception as error:
            report['passed'] = False
            report['failure'] = str(error)[:1200]
            with suppress(Exception):driver.screenshot('failure')
            raise
        finally:
            # Log out any live fixture account before deleting its configuration.
            with suppress(Exception):driver.invoke('workspace_logout',{})
            driver.stop()
            (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':main()
