#!/usr/bin/env python3
"""Drive an unchanged installed macOS app through its owned OS AX hierarchy.

No DOM/IPC injection, TCC changes, rebuilt application or paid provider. REST
creates a separate fixture observer session only after real GUI owner setup.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def fixture_helpers():
    spec = importlib.util.spec_from_file_location('native_gui_fixture', Path(__file__).with_name('native_gui_smoke.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_pid(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('An exact owned application PID is required')
    return value


def keyboard_script(pid, text=None, close=False):
    pid = owned_pid(pid)
    action = 'keystroke "w" using command down' if close else (
        'keystroke "a" using command down\nkeystroke '+json.dumps(text, ensure_ascii=False))
    return f'''tell application "System Events"
set ownedProcess to first application process whose unix id is {pid}
set frontmost of ownedProcess to true
{action}
end tell'''


def session_observer(origin, username, password):
    result = host_api(origin, '/auth/login', 'POST', {
        'method': 'local-password', 'username': username, 'password': password,
        'transport': 'bearer', 'device_name': 'Mac installed GUI fixture observer',
    })
    return result['access_token'], result['session_id']


def host_api(origin, path, method='GET', data=None, token=None):
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('Observer calls must stay on the isolated host')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = Request(origin+path, data=None if data is None else json.dumps(data).encode(),
                      method=method, headers=headers)
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        body = json.loads(response.read() or b'null')
        if response.status >= 400:
            raise RuntimeError('Fixture observer HTTP '+str(response.status)+': '+str(body.get('detail', 'request denied'))[:200])
        return body


def screen_work_area():
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', '''
ObjC.import('AppKit');
const screen=$.NSScreen.mainScreen,frame=screen.frame,visible=screen.visibleFrame;
JSON.stringify({x:visible.origin.x,y:frame.size.height-visible.origin.y-visible.size.height,
 width:visible.size.width,height:visible.size.height});
'''], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Actual native work-area query failed: '+result.stderr[-300:])
    area = json.loads(result.stdout)
    if not all(isinstance(area.get(key), (int, float)) for key in ['x', 'y', 'width', 'height']):
        raise RuntimeError('Actual native work area was not numeric')
    return area


def reachable_window(position, size, area):
    return (
        position[0] >= area['x']-2 and position[1] >= area['y']-2
        and size[0] > 0 and size[1] > 0
        and position[0]+size[0] <= area['x']+area['width']+2
        and position[1]+size[1] <= area['y']+area['height']+2
    )


class AX:
    """Public Accessibility APIs, rooted only at the launched app's exact PID."""
    def __init__(self, pid):
        self.pid = owned_pid(pid)
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.refs = []
        self.rows = []
        signatures = {
            'CFStringCreateWithCString': (C.c_void_p, [C.c_void_p, C.c_char_p, C.c_uint32]),
            'CFRelease': (None, [C.c_void_p]), 'CFRetain': (C.c_void_p, [C.c_void_p]),
            'CFGetTypeID': (C.c_ulong, [C.c_void_p]), 'CFStringGetTypeID': (C.c_ulong, []),
            'CFBooleanGetTypeID': (C.c_ulong, []), 'CFNumberGetTypeID': (C.c_ulong, []),
            'CFBooleanGetValue': (C.c_bool, [C.c_void_p]),
            'CFNumberGetValue': (C.c_bool, [C.c_void_p, C.c_int, C.c_void_p]),
            'CFStringGetCString': (C.c_bool, [C.c_void_p, C.c_void_p, C.c_long, C.c_uint32]),
            'CFArrayGetCount': (C.c_long, [C.c_void_p]),
            'CFArrayGetValueAtIndex': (C.c_void_p, [C.c_void_p, C.c_long]),
            'CFArrayGetTypeID': (C.c_ulong, []),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.cf, name)
            function.restype, function.argtypes = restype, argtypes
        for name, restype, argtypes in [
            ('AXIsProcessTrusted', C.c_bool, []),
            ('AXUIElementCreateApplication', C.c_void_p, [C.c_int]),
            ('AXUIElementCopyAttributeValue', C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]),
            ('AXUIElementSetAttributeValue', C.c_int, [C.c_void_p, C.c_void_p, C.c_void_p]),
            ('AXUIElementPerformAction', C.c_int, [C.c_void_p, C.c_void_p]),
            ('AXValueGetType', C.c_int, [C.c_void_p]),
            ('AXValueGetTypeID', C.c_ulong, []),
            ('AXValueGetValue', C.c_bool, [C.c_void_p, C.c_int, C.c_void_p]),
        ]:
            function = getattr(self.ax, name)
            function.restype, function.argtypes = restype, argtypes
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError('Actual macOS Accessibility authorization is unavailable; no TCC mutation is attempted')
        self.app = self.ax.AXUIElementCreateApplication(self.pid)
        if not self.app:
            raise RuntimeError('Could not address the owned native process')

    def string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)

    def attribute(self, element, name):
        key, value = self.string(name), C.c_void_p()
        try:
            code = self.ax.AXUIElementCopyAttributeValue(element, key, C.byref(value))
        finally:
            self.cf.CFRelease(key)
        return value.value if code == 0 else None

    def scalar(self, element, name):
        value = self.attribute(element, name)
        if not value:
            return None
        try:
            kind = self.cf.CFGetTypeID(value)
            if kind == self.cf.CFStringGetTypeID():
                buffer = C.create_string_buffer(65536)
                return buffer.value.decode(errors='replace') if self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100) else '<large native text>'
            if kind == self.cf.CFBooleanGetTypeID():
                return bool(self.cf.CFBooleanGetValue(value))
            if kind == self.cf.CFNumberGetTypeID():
                number = C.c_double()
                return number.value if self.cf.CFNumberGetValue(value, 13, C.byref(number)) else None
            if kind == self.ax.AXValueGetTypeID():
                value_type = self.ax.AXValueGetType(value)
                if value_type in {1, 2}:  # CGPoint / CGSize
                    pair = (C.c_double * 2)()
                    return list(pair) if self.ax.AXValueGetValue(value, value_type, pair) else None
            return None
        finally:
            self.cf.CFRelease(value)

    def array(self, element, name):
        value = self.attribute(element, name)
        if not value:
            return []
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFArrayGetTypeID():
                raise RuntimeError('Owned AX children attribute was not an array')
            count = self.cf.CFArrayGetCount(value)
            if count < 0 or count > 5000:
                raise RuntimeError('Owned AX hierarchy exceeded fixture bounds')
            result = []
            for index in range(count):
                node = self.cf.CFArrayGetValueAtIndex(value, index)
                if node:
                    self.cf.CFRetain(node)
                    self.refs.append(node)
                    result.append(node)
            return result
        finally:
            self.cf.CFRelease(value)

    def snapshot(self):
        self.release_rows()
        self.rows = []
        def walk(element, window, depth):
            if depth > 40 or len(self.rows) >= 5000:
                raise RuntimeError('Owned native UI hierarchy exceeded fixture bounds')
            role, subrole = self.scalar(element, 'AXRole'), self.scalar(element, 'AXSubrole')
            row = {
                'ref': element, 'window_ref': window, 'role': role, 'subrole': subrole,
                'title': self.scalar(element, 'AXTitle'),
                'description': self.scalar(element, 'AXDescription'),
                'help': self.scalar(element, 'AXHelp'),
                'enabled': self.scalar(element, 'AXEnabled'),
                'window_title': self.scalar(window, 'AXTitle'),
            }
            if subrole != 'AXSecureTextField' and role != 'AXSecureTextField':
                row['value'] = self.scalar(element, 'AXValue')
            self.rows.append(row)
            for child in self.array(element, 'AXChildren'):
                walk(child, window, depth+1)
        for window in self.array(self.app, 'AXWindows'):
            walk(window, window, 0)
        return self.rows

    def find(self, text, roles=None, window=None):
        for row in self.snapshot():
            if (roles is None or row['role'] in roles) and (window is None or row['window_title'] == window):
                if any(row.get(key) == text for key in ['title', 'description', 'help', 'value']):
                    return row
        return None

    def action(self, element, name):
        key = self.string(name)
        try:
            code = self.ax.AXUIElementPerformAction(element, key)
        finally:
            self.cf.CFRelease(key)
        if code:
            raise RuntimeError('Owned native AX action '+name+' failed: '+str(code))

    def focus(self, row):
        self.action(row['window_ref'], 'AXRaise')
        true = C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue').value
        for element, attribute in [(self.app, 'AXFrontmost'), (row['ref'], 'AXFocused')]:
            key = self.string(attribute)
            try:
                code = self.ax.AXUIElementSetAttributeValue(element, key, true)
            finally:
                self.cf.CFRelease(key)
            if code:
                raise RuntimeError('Owned native focus failed: '+attribute+' '+str(code))

    def click(self, text, window=None):
        row = wait(lambda: self.find(text, {'AXButton'}, window), 'native button '+text)
        if row['enabled'] is False:
            row = wait(lambda: (found if found and found['enabled'] is not False else None)
                       if (found := self.find(text, {'AXButton'}, window)) else None, 'enabled native button '+text)
        self.action(row['window_ref'], 'AXRaise')
        self.action(row['ref'], 'AXPress')

    def type(self, text, value, window=None):
        roles = {'AXTextField', 'AXTextArea', 'AXSecureTextField', 'AXComboBox'}
        row = wait(lambda: self.find(text, roles, window), 'native field '+text)
        self.focus(row)
        result = subprocess.run(['/usr/bin/osascript', '-e', keyboard_script(self.pid, value)],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError('Owned native typing failed: '+result.stderr[-300:])

    def close_window(self, title):
        row = wait(lambda: self.find(title, {'AXWindow'}), 'owned window '+title)
        self.action(row['ref'], 'AXRaise')
        key = self.string('AXCloseButton')
        button = C.c_void_p()
        try:
            code = self.ax.AXUIElementCopyAttributeValue(row['ref'], key, C.byref(button))
        finally:
            self.cf.CFRelease(key)
        if code or not button.value:
            raise RuntimeError('Owned native window has no close button')
        try:
            self.action(button.value, 'AXPress')
        finally:
            self.cf.CFRelease(button.value)

    def windows(self):
        return [row for row in self.snapshot() if row['role'] == 'AXWindow']

    def evidence(self, path):
        rows = [{key: value for key, value in row.items() if key not in {'ref', 'window_ref'}}
                for row in self.snapshot()]
        path.write_text(json.dumps(rows, indent=2)+'\n')

    def release_rows(self):
        for ref in reversed(self.refs):
            self.cf.CFRelease(ref)
        self.refs = []

    def close(self):
        self.release_rows()
        if self.app:
            self.cf.CFRelease(self.app)
            self.app = None


def wait(check, description, timeout=90):
    deadline, last = monotonic()+timeout, None
    while monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (RuntimeError, OSError) as error:
            last = str(error)
        sleep(.3)
    raise RuntimeError('Timed out waiting for '+description+': '+str(last))


def stop(process):
    # Capture only descendants of this launched app before it exits.
    listing = subprocess.run(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True, text=True, check=True)
    rows = [tuple(map(int, line.split())) for line in listing.stdout.splitlines() if line.strip()]
    descendants, frontier = [], {process.pid}
    while frontier:
        frontier = {pid for pid, parent in rows if parent in frontier}
        descendants.extend(frontier)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    for pid in reversed(descendants):
        with suppress(ProcessLookupError):
            os.kill(pid, 15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Installed native macOS qualification runs on macOS CI')
    app = args.app.resolve()
    binary = app/'Contents/MacOS/termx-desktop'
    if not binary.is_file() or not args.package.is_file():
        parser.error('An installed unchanged native app and its DMG are required')
    helpers = fixture_helpers()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'platform': 'darwin', 'binary_sha256': helpers.file_digest(binary),
              'package_sha256': helpers.file_digest(args.package), 'steps': {},
              'ipc_mocked': False, 'dom_injected': False, 'tcc_changed': False,
              'provider_account_used': False, 'native_cookie_cleared': False,
              'limits': ['No microphone hardware or native OIDC TLS journey is exercised.',
                         'Keyring restart is verified through the native bearer bridge; macOS WKWebView cookies are not manually cleared.',
                         'This CI fixture uses the runner OS account with isolated native host/config/project data; no real user account is accessed.']}
    process, controller, observer, origin = None, None, None, None
    with tempfile.TemporaryDirectory(prefix='termx-installed-macos-gui-') as directory:
        root = Path(directory)
        environment = helpers.fixture_environment(root)
        log = (output/'native-app.log').open('ab')
        try:
            def launch():
                nonlocal process, controller, origin
                process = subprocess.Popen([str(binary)], env=environment, stdout=log, stderr=log)
                controller = AX(process.pid)
                wait(lambda: controller.windows(), 'actual installed native app window', 180)
                config_path = root/'native/desktop.json'
                wait(lambda: config_path.is_file(), 'isolated native bootstrap configuration')
                if not json.loads(config_path.read_text()).get('onboarded'):
                    # The production welcome dialog is delayed by two seconds.
                    # Exercise its explicit skip action rather than edit config.
                    wait(lambda: controller.find('Skip setup', {'AXButton'}), 'native permission welcome dialog')
                    controller.click('Skip setup')
                    report['steps']['native_permission_wizard_explicitly_skipped'] = True
                pid_path = root/'native/backend.pid'
                wait(lambda: pid_path.is_file(), 'installed frozen backend readiness', 180)
                state = json.loads(pid_path.read_text())
                origin = 'http://127.0.0.1:'+str(state['port'])
                wait(lambda: host_api(origin, '/api/health'), 'frozen native host health', 180)
                if controller.find('Termx — Machine Status', {'AXWindow'}):
                    controller.close_window('Termx — Machine Status')

            def field_value(text, value, window='Termx'):
                row = controller.find(text, {'AXTextField', 'AXTextArea', 'AXComboBox'}, window)
                return row and row.get('value') == value

            def signed_in():
                return controller.find('Sign out', {'AXButton'}, 'Termx')

            def native_session():
                rows = host_api(origin, '/auth/sessions', token=observer)
                live = [row for row in rows if row['device_name'] == 'TermX desktop' and not row['revoked']]
                assert len(live) == 1, 'Expected exactly one live native fixture session'
                return live[0]['id']

            launch()
            wait(lambda: controller.find('Set up your workspace', window='Termx'), 'actual native owner setup')
            username, password = 'mac-native-fixture-owner', 'isolated-mac-native-fixture-password-123'
            controller.type('Username', username, 'Termx')
            controller.type('Password', password, 'Termx')
            controller.click('Create owner account', 'Termx')
            wait(signed_in, 'actual GUI owner unlock')
            observer, observer_id = session_observer(origin, username, password)
            first_native = native_session()
            report['steps']['native_gui_owner_setup'] = True
            controller.evidence(output/'owner-workspace-ax.json')
            data_dir = root/'native'
            assert data_dir.stat().st_mode & 0o077 == 0
            assert (data_dir/'desktop.json').stat().st_mode & 0o077 == 0
            project = root/'project'
            project.mkdir()
            seed = 'print("TERMX_NATIVE_MAC_EDITOR")\n'
            (project/'main.py').write_text(seed)
            controller.click('New chat', 'Termx')
            controller.type('Conversation title', 'Native Mac fixture conversation', 'Termx')
            controller.type('Add a project folder', str(project), 'Termx')
            controller.click('Create conversation', 'Termx')
            def conversation():
                rows = host_api(origin, '/api/workspace/sessions', token=observer)
                rows = rows.get('sessions', rows) if isinstance(rows, dict) else rows
                return next((row for row in rows if row['title'] == 'Native Mac fixture conversation'), None)
            session = wait(conversation, 'GUI-created canonical conversation')
            session_id = session['id']
            draft = 'Native Mac GUI draft'
            controller.type('Message', draft, 'Termx')
            wait(lambda: host_api(origin, '/api/workspace/sessions/'+session_id, token=observer)['draft_text'] == draft,
                 'native GUI canonical draft persistence')
            controller.click('Workbench', 'Termx')
            wait(lambda: controller.find('main.py', {'AXButton'}, 'Termx'), 'actual workbench project tree')
            controller.click('main.py', 'Termx')
            wait(lambda: controller.find('Editor for main.py', window='Termx'), 'file-backed native workbench editor')
            changed = 'print("TERMX_NATIVE_MAC_DIRTY_HANDOFF")\n'
            controller.type('Editor for main.py', changed, 'Termx')
            assert (project/'main.py').read_text() == seed, 'Native edit unexpectedly wrote the file'
            report['steps']['native_workbench_loaded_file_and_dirty_buffer'] = True
            controller.click('Detach workspace', 'Termx')
            wait(lambda: controller.find('Return to main workspace', {'AXButton'}, 'TermX workspace'), 'real native detached workspace')
            controller.evidence(output/'detached-workspace-ax.json')
            controller.click('Return to main workspace', 'TermX workspace')
            wait(lambda: len(controller.windows()) == 1, 'acknowledged native re-dock')
            wait(lambda: field_value('Editor for main.py', changed), 'dirty editor survives actual native handoff')
            assert (project/'main.py').read_text() == seed
            report['steps']['acknowledged_redock_preserves_unsaved_editor'] = True
            controller.click('Lock workspace','Termx')
            wait(lambda:controller.find('Workspace locked',window='Termx'),'explicit native session Lock')
            assert native_session()==first_native
            controller.type('Password',password,'Termx')
            controller.click('Unlock workspace','Termx')
            wait(signed_in,'same-session native password unlock')
            assert native_session()==first_native
            wait(lambda:field_value('Editor for main.py',changed),'dirty buffer preserved through native Lock')
            assert (project/'main.py').read_text()==seed
            report['steps']['lock_unlock_same_session_preserves_dirty_editor']=True
            controller.click('Chat', 'Termx')
            wait(lambda: field_value('Message', draft), 'draft survives workbench re-dock')
            controller.click('Detach workspace', 'Termx')
            wait(lambda: controller.find('Return to main workspace', {'AXButton'}, 'TermX workspace'), 'detached chat')
            controller.close_window('TermX workspace')
            wait(lambda: len(controller.windows()) == 1, 'close actual detached window')
            placements = wait(lambda: list(data_dir.glob('workspace-window-workspace-chat-*.json')), 'independent native detached placement')
            assert len(placements) == 1
            placement_name = placements[0].name
            controller.click('Detach workspace', 'Termx')
            wait(lambda: field_value('Message', draft, 'TermX workspace'), 'same canonical draft after native reopen')
            controller.close_window('TermX workspace')
            wait(lambda: len(controller.windows()) == 1, 'reopened detached window closes')
            assert [path.name for path in data_dir.glob('workspace-window-workspace-chat-*.json')] == [placement_name]
            report['steps']['native_detach_close_reopen_stable_placement'] = True
            controller.close()
            controller = None
            stop(process)
            process = None
            (data_dir/'workspace-window-main.json').write_text(json.dumps({
                'x': -20000, 'y': -20000, 'width': 6000, 'height': 4000, 'scale': 1,
                'frame_width': 16, 'frame_height': 40,
                'monitor': {'name': 'Removed CI fixture monitor', 'x': -20000, 'y': -20000,
                            'width': 6000, 'height': 4000, 'scale': 1},
            }))
            launch()
            wait(signed_in, 'OS-keyring native bearer restoration after process restart')
            observer, observer_id = session_observer(origin, username, password)
            assert native_session() == first_native, 'Native restart created another session instead of restoring its OS credential'
            main_window = wait(lambda: controller.find('Termx', {'AXWindow'}), 'restored native main window')
            position = controller.scalar(main_window['ref'], 'AXPosition')
            size = controller.scalar(main_window['ref'], 'AXSize')
            area = screen_work_area()
            assert position and size and reachable_window(position, size, area), 'Restored native window escaped the actual monitor work area'
            report['steps']['native_keyring_restart_and_removed_monitor_recovery'] = {'position': position, 'size': size, 'work_area': area}
            controller.evidence(output/'restored-workspace-ax.json')
            host_api(origin, '/auth/sessions/'+first_native, 'DELETE', token=observer)
            controller.click('New chat', 'Termx')  # Actual protected native transport forces a live check.
            wait(lambda: controller.find('Continue with password', {'AXButton'}, 'Termx'), 'actual revoked native session locks UI', 40)
            report['steps']['native_live_session_revocation'] = True
            controller.type('Username', username, 'Termx')
            controller.type('Password', password, 'Termx')
            controller.click('Continue with password', 'Termx')
            wait(signed_in, 'native password reauthentication')
            controller.click('Sign out', 'Termx')
            wait(lambda: controller.find('Continue with password', {'AXButton'}, 'Termx'), 'native logout')
            controller.close()
            controller = None
            stop(process)
            process = None
            launch()
            wait(lambda: controller.find('Continue with password', {'AXButton'}, 'Termx'), 'native logout remains locked after restart')
            report['steps']['native_logout_revokes_os_refresh_on_restart'] = True
            controller.type('Username',username,'Termx')
            controller.type('Password',password,'Termx')
            controller.click('Continue with password','Termx')
            wait(signed_in,'fixture login before graceful Stop host')
            observer,observer_id=session_observer(origin,username,password)
            current_host=host_api(origin,'/auth/me',token=observer)['host_id']
            pid_path=root/'native/backend.pid'
            last_pid_record=json.loads(pid_path.read_text())
            controller.click('Stop host','Termx')
            wait(lambda:controller.find('Stop this host',window='Termx'),'explicit native host shutdown effects')
            acknowledgement=wait(lambda:controller.find('I understand these effects and want to stop this host.',{'AXCheckBox'},'Termx'),'owned shutdown acknowledgement')
            controller.action(acknowledgement['ref'],'AXPress')
            controller.click('Stop host now','Termx')
            def unavailable():
                try:host_api(origin,'/auth/methods')
                except URLError as error:return isinstance(error.reason,ConnectionRefusedError)
                return False
            wait(unavailable,'gracefully stopped installed host',40)
            # Beyond the first native automatic-restart interval. The unchanged
            # shell/window must remain alive while the fixture host stays down.
            deadline=monotonic()+15
            while monotonic()<deadline:
                assert process.poll() is None,'Stop host quit the native shell'
                assert unavailable(),'Accepted Stop host was automatically restarted'
                assert not pid_path.exists() or json.loads(pid_path.read_text())==last_pid_record,'Stop host launched another native backend'
                sleep(.5)
            assert controller.windows(),'Native workspace window disappeared during host-only shutdown'
            report['steps']['accepted_host_stop_does_not_respawn_native_backend']=True
            # Remove only this random fixture host's OS-stored credential. The
            # host has exited, so the normal API logout can no longer do this.
            cleanup=subprocess.run(['/usr/bin/security','delete-generic-password','-s','com.jaexxxy.termx.workspace.session','-a',current_host],capture_output=True,text=True,timeout=15)
            assert cleanup.returncode in {0,44},'Could not remove the isolated fixture OS credential'
            helpers.verify_unchanged_assets(report, binary, args.package)
            report['passed'] = True
        except Exception as error:
            report['passed'], report['failure'] = False, str(error)[-1500:]
            if controller:
                with suppress(Exception):
                    controller.evidence(output/'failure-ax.json')
            raise
        finally:
            if origin and observer:
                with suppress(Exception):
                    rows = host_api(origin, '/auth/sessions', token=observer)
                    for row in sorted(rows, key=lambda row: row['id'] == observer_id):
                        if not row['revoked']:
                            host_api(origin, '/auth/sessions/'+row['id'], 'DELETE', token=observer)
            if controller:
                controller.close()
            if process:
                stop(process)
            log.close()
            (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
