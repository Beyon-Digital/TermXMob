"""Explicit opt-in proof captures only an application created by this test."""
import io
import os
import subprocess
import sys
import signal
import uuid
from time import monotonic, sleep

import pytest
from PIL import Image
from termx.desktop.windows import WindowCapture


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('TERMX_TEST_NATIVE_WINDOW') != '1', reason='opt-in owned macOS fixture window')
def test_exact_owned_macos_fixture_window_capture(tmp_path):
    title = 'TermX owned capture fixture ' + uuid.uuid4().hex
    source = tmp_path / 'fixture.js'
    source.write_text('''ObjC.import('AppKit');
const app = $.NSApplication.sharedApplication;
app.setActivationPolicy(0);
const window = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(100,100,320,200),3,2,false);
window.title = 'TITLE_TOKEN';
window.backgroundColor = $.NSColor.greenColor;
window.makeKeyAndOrderFront(null);
app.activateIgnoringOtherApps(true);
app.run;
'''.replace('TITLE_TOKEN', title))
    log = tmp_path / 'appkit.log'
    with log.open('wb') as output:
        process = subprocess.Popen(['/usr/bin/osascript', '-l', 'JavaScript', str(source)], stdout=output, stderr=output, start_new_session=True)
        try:
            port = WindowCapture(); deadline = monotonic() + 15; window = None
            while monotonic() < deadline:
                if process.poll() is not None: pytest.fail('Owned fixture exited: ' + log.read_text()[:1500])
                def owned(row):
                    try: return os.getpgid(row['pid']) == process.pid
                    except ProcessLookupError: return False
                window = next((row for row in port.list() if owned(row)), None)
                if window: break
                sleep(.2)
            assert window is not None, 'Owned fixture window did not open'
            data = port.capture(window)
            image = Image.open(io.BytesIO(data)); image.load()
            assert 300 <= image.width <= 700 and 180 <= image.height <= 500
            assert data[:2] == b'\xff\xd8'
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
