#!/usr/bin/env python3
"""Probe hosted macOS GUI control using a synthetic, owned native window.

This is a CI fixture, not a TermX delivery build or installed-app proof. It
does not grant permissions, edit TCC, read other windows or capture the screen.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import ctypes
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from time import monotonic, sleep
import uuid


SWIFT_SOURCE = r'''
import AppKit
import Foundation

let readyURL = URL(fileURLWithPath: CommandLine.arguments[1])
let fixtureTitle = CommandLine.arguments[2]
final class Fixture: NSObject {
    var window: NSWindow!
    func record(_ clicked: Bool) {
        let frame = window.frame
        let state: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier,
            "title": fixtureTitle, "visible": window.isVisible, "clicked": clicked,
            "frame": ["x": frame.origin.x, "y": frame.origin.y,
                      "width": frame.width, "height": frame.height]
        ]
        let bytes = try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys])
        try! bytes.write(to: readyURL, options: [.atomic])
    }
    @objc func exercise(_ sender: NSButton) {
        sender.title = "Fixture action completed"
        record(true)
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let fixture = Fixture()
let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 220),
                      styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
fixture.window = window
window.title = fixtureTitle
window.isReleasedWhenClosed = false
let button = NSButton(title: "Exercise owned fixture", target: fixture, action: #selector(Fixture.exercise(_:)))
button.frame = NSRect(x: 70, y: 70, width: 280, height: 42)
window.contentView!.addSubview(button)
window.center()
window.makeKeyAndOrderFront(nil)
application.activate(ignoringOtherApps: true)
fixture.record(false)
application.run()
'''


def ui_script(pid: int) -> str:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError('An owned positive fixture PID is required')
    return f'''tell application "System Events"
repeat 20 times
try
set ownedProcess to first application process whose unix id is {pid}
tell ownedProcess
set frontmost to true
if (count of windows) > 0 then
click button "Exercise owned fixture" of window 1
return "Owned native action completed"
end if
end tell
on error failureText number failureNumber
if failureText contains "not allowed" or failureText contains "not authorized" then error failureText number failureNumber
end try
delay 0.25
end repeat
error "Owned native window did not expose its fixture button"
end tell'''


def read_state(path: Path):
    if path.is_file() and path.stat().st_size <= 2048:
        return json.loads(path.read_text())
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This fixture runs only on macOS CI')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {
        'platform': sys.platform, 'runner_ci': os.environ.get('CI') == 'true',
        'installed_termx_exercised': False, 'delivery_build': False,
        'tcc_changed': False, 'screen_captured': False,
        'scope': 'One synthetic owned AppKit window, addressed by its exact PID',
        'limitations': ['This probe does not qualify TermX setup, sessions, re-dock, monitor recovery or microphone hardware.'],
    }
    process = None
    try:
        services = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        report['python_ax_trusted'] = bool(services.AXIsProcessTrusted())
        # The actual osascript action below decides its own Automation/AX access.
        with tempfile.TemporaryDirectory(prefix='termx-owned-macos-gui-') as directory:
            root = Path(directory)
            bundle = root/'TermX Owned GUI Fixture.app'
            executable = bundle/'Contents/MacOS/termx-owned-gui-fixture'
            executable.parent.mkdir(parents=True)
            title = 'TermX owned GUI fixture '+uuid.uuid4().hex
            source, ready = root/'fixture.swift', root/'ready.json'
            source.write_text(SWIFT_SOURCE)
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'org.termx.gui-fixture.'+uuid.uuid4().hex,
                'CFBundleExecutable': executable.name, 'CFBundleName': 'TermX Owned GUI Fixture',
                'CFBundlePackageType': 'APPL', 'NSHighResolutionCapable': True,
            }))
            compilation = subprocess.run(['/usr/bin/swiftc', str(source), '-o', str(executable)],
                                         capture_output=True, text=True, timeout=90)
            if compilation.returncode:
                raise RuntimeError('Synthetic AppKit fixture compilation failed: '+compilation.stderr[-1600:])
            with (output/'owned-fixture.log').open('wb') as log:
                process = subprocess.Popen([str(executable), str(ready), title], stdout=log, stderr=log)
                deadline = monotonic()+15
                state = None
                while monotonic() < deadline:
                    state = read_state(ready)
                    if state or process.poll() is not None:
                        break
                    sleep(.1)
                if not state:
                    report['native_window_available'] = False
                    report['unavailable_reason'] = 'Owned AppKit window did not become ready; process exit='+str(process.poll())
                else:
                    if state.get('pid') != process.pid or state.get('title') != title:
                        raise RuntimeError('Synthetic window identity did not match the owned process')
                    report['native_window_available'] = bool(state.get('visible'))
                    report['owned_window'] = state
                    try:
                        action = subprocess.run(['/usr/bin/osascript', '-e', ui_script(process.pid)],
                                                capture_output=True, text=True, timeout=25)
                    except subprocess.TimeoutExpired:
                        report['ui_control_available'] = False
                        report['unavailable_reason'] = 'Owned System Events UI action timed out; a native permission prompt may require interaction'
                    else:
                        report['osascript_exit'] = action.returncode
                        # Only diagnostics about the owned action, never other windows/accounts.
                        report['osascript_diagnostic'] = (action.stderr or action.stdout)[-1600:]
                        deadline = monotonic()+5
                        observed = read_state(ready) or {}
                        while not observed.get('clicked') and monotonic() < deadline:
                            sleep(.1)
                            observed = read_state(ready) or {}
                        report['ui_control_available'] = action.returncode == 0 and bool(observed.get('clicked'))
                        if not report['ui_control_available']:
                            report['unavailable_reason'] = 'Actual owned native button action was denied or unobserved; see osascript diagnostic'
                    report['gui_automation_feasible'] = report['native_window_available'] and report.get('ui_control_available', False)
    except Exception as error:
        report['probe_error'] = str(error)[-1800:]
        raise
    finally:
        if process:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({key: report.get(key) for key in ['native_window_available', 'ui_control_available', 'gui_automation_feasible', 'unavailable_reason', 'probe_error']}))


if __name__ == '__main__':
    main()
