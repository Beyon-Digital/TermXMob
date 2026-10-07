"""Opt-in X11 proof captures only the uniquely named window this test creates."""
import io
import os
import subprocess
import sys
import uuid
from time import monotonic,sleep
import pytest
from PIL import Image
from termx.desktop.windows import WindowCapture

@pytest.mark.skipif(sys.platform!='linux' or os.environ.get('TERMX_TEST_NATIVE_WINDOW')!='1',reason='owned X11 fixture requires an explicit Xvfb/desktop test')
def test_exact_owned_x11_fixture_window_capture():
    title='TermX-owned-X11-'+uuid.uuid4().hex
    process=subprocess.Popen(['xmessage','-title',title,'-geometry','320x200+50+50','TermX isolated capture fixture'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        port=WindowCapture();deadline=monotonic()+15;window=None
        assert port.capability()['supported']
        while monotonic()<deadline:
            if process.poll() is not None:pytest.fail('Owned X11 fixture exited before capture')
            window=next((row for row in port.list() if row['title']==title),None)
            if window:break
            sleep(.1)
        assert window is not None,'Owned X11 fixture did not open'
        image=Image.open(io.BytesIO(port.capture(window)));image.load()
        assert 300<=image.width<=380 and 180<=image.height<=280
        assert image.width<1280 and image.height<800,'Capture must be the exact fixture window, not the Xvfb desktop'
        assert image.format=='JPEG'
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
