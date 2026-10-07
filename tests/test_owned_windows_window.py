"""Actual PrintWindow proof captures only the process/window this test owns."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
import uuid
import pytest
from PIL import Image
from termx.desktop.capture import CaptureError
from termx.desktop.windows import WindowCapture

pytestmark=pytest.mark.skipif(sys.platform!='win32' or os.environ.get('TERMX_TEST_NATIVE_WINDOW')!='1',
    reason='Actual owned Win32 fixture requires explicit isolated desktop qualification')

FIXTURE=r'''
import ctypes
from ctypes import wintypes as w
import sys,os,json
u=ctypes.WinDLL('user32',use_last_error=True);g=ctypes.WinDLL('gdi32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
callback=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
class Rect(ctypes.Structure):_fields_=[('left',w.LONG),('top',w.LONG),('right',w.LONG),('bottom',w.LONG)]
class Paint(ctypes.Structure):_fields_=[('hdc',w.HDC),('erase',w.BOOL),('rect',Rect),('restore',w.BOOL),('update',w.BOOL),('reserved',w.BYTE*32)]
class Class(ctypes.Structure):
 _fields_=[('style',w.UINT),('proc',callback),('class_extra',ctypes.c_int),('window_extra',ctypes.c_int),('instance',w.HINSTANCE),('icon',w.HANDLE),('cursor',w.HANDLE),('brush',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
k.GetModuleHandleW.argtypes=[w.LPCWSTR];k.GetModuleHandleW.restype=w.HMODULE
g.CreateSolidBrush.argtypes=[w.DWORD];g.CreateSolidBrush.restype=w.HBRUSH
g.DeleteObject.argtypes=[w.HANDLE];g.DeleteObject.restype=w.BOOL
u.GetClientRect.argtypes=[w.HWND,ctypes.POINTER(Rect)];u.GetClientRect.restype=w.BOOL
u.FillRect.argtypes=[w.HDC,ctypes.POINTER(Rect),w.HBRUSH];u.FillRect.restype=ctypes.c_int
u.BeginPaint.argtypes=[w.HWND,ctypes.POINTER(Paint)];u.BeginPaint.restype=w.HDC
u.EndPaint.argtypes=[w.HWND,ctypes.POINTER(Paint)];u.EndPaint.restype=w.BOOL
u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.DefWindowProcW.restype=ctypes.c_ssize_t
u.PostQuitMessage.argtypes=[ctypes.c_int]
brush=g.CreateSolidBrush(0x0000ff00)
@callback
def procedure(hwnd,msg,wp,lp):
 if msg in (15,792):
  paint=Paint();dc=u.BeginPaint(hwnd,ctypes.byref(paint)) if msg==15 else w.HDC(wp)
  rect=Rect();u.GetClientRect(hwnd,ctypes.byref(rect));u.FillRect(dc,ctypes.byref(rect),brush)
  if msg==15:u.EndPaint(hwnd,ctypes.byref(paint))
  return 0
 if msg==2:u.PostQuitMessage(0);return 0
 return u.DefWindowProcW(hwnd,msg,wp,lp)
instance=k.GetModuleHandleW(None);name=sys.argv[1]
cls=Class(0,procedure,0,0,instance,None,None,brush,None,name)
u.RegisterClassW.argtypes=[ctypes.POINTER(Class)];u.RegisterClassW.restype=w.ATOM
assert u.RegisterClassW(ctypes.byref(cls))
u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,w.LPVOID];u.CreateWindowExW.restype=w.HWND
hwnd=u.CreateWindowExW(0,name,name,0x00cf0000,50,50,360,240,None,None,instance,None);assert hwnd
with open(sys.argv[2],'w') as ready:json.dump({'pid':os.getpid(),'parent':os.getppid(),'nonce':name},ready)
u.ShowWindow.argtypes=[w.HWND,ctypes.c_int];u.UpdateWindow.argtypes=[w.HWND]
u.ShowWindow(hwnd,5);u.UpdateWindow(hwnd)
u.GetMessageW.argtypes=[ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT];u.GetMessageW.restype=w.BOOL
u.TranslateMessage.argtypes=[ctypes.POINTER(w.MSG)];u.DispatchMessageW.argtypes=[ctypes.POINTER(w.MSG)];u.DispatchMessageW.restype=ctypes.c_ssize_t
message=w.MSG()
while u.GetMessageW(ctypes.byref(message),None,0,0)>0:u.TranslateMessage(ctypes.byref(message));u.DispatchMessageW(ctypes.byref(message))
g.DeleteObject(brush)
'''


def test_exact_owned_printwindow_fixture(tmp_path):
    title='TermX-owned-Win32-'+uuid.uuid4().hex
    source=tmp_path/'owned_window.py';source.write_text(FIXTURE)
    ready=tmp_path/('ready-'+uuid.uuid4().hex+'.json')
    log=tmp_path/'fixture.log'
    with log.open('wb') as output:
        process=subprocess.Popen([sys.executable,'-I',str(source),title,str(ready)],stdout=output,stderr=output)
        port=WindowCapture();window=None
        try:
            assert port.capability()['supported']
            deadline=monotonic()+15
            while monotonic()<deadline:
                if process.poll() is not None:pytest.fail('Owned Win32 fixture exited: '+log.read_text()[:2000])
                if not ready.exists():sleep(.1);continue
                raw=ready.read_bytes()
                if len(raw)>512:pytest.fail('Owned Win32 fixture readiness exceeds limit')
                try:identity=json.loads(raw)
                except json.JSONDecodeError:sleep(.1);continue
                pid=identity.get('pid')
                assert identity.get('nonce')==title and type(pid) is int and 1<=pid<2**32
                assert pid==process.pid or identity.get('parent')==process.pid,'Window must belong to the explicitly launched child tree'
                window=next((row for row in port.list() if row['pid']==pid and row['title']==title),None)
                if window:break
                sleep(.1)
            assert window is not None,'Owned Win32 fixture did not open'
            data=port.capture(window);image=Image.open(io.BytesIO(data));image.load()
            assert image.format=='JPEG'
            assert image.size==(window['width'],window['height'])
            assert 300<=image.width<=500 and 200<=image.height<=400
            red,green,blue=image.getpixel((image.width//2,image.height//2))
            assert green>220 and red<40 and blue<40,'Captured content must be the known owned green window'
            with pytest.raises(CaptureError,match='closed or changed owner'):
                port.capture({**window,'pid':window['pid']+1})
            evidence=os.environ.get('TERMX_NATIVE_WINDOW_EVIDENCE')
            if evidence:
                path=Path(evidence);path.mkdir(parents=True,exist_ok=True)
                (path/'owned-printwindow.jpg').write_bytes(data)
                (path/'owned-printwindow.json').write_text(json.dumps({'backend':'PrintWindow','owned_pid':window['pid'],
                    'exact_window_dimensions':list(image.size),'center_rgb':[red,green,blue],
                    'foreign_owner_refused':True,'user_desktop_captured':False},indent=2)+'\n')
        finally:
            if window:
                # Close only the window with the proven child-tree PID. This
                # also exits its real interpreter behind a venv redirector.
                import ctypes
                from ctypes import wintypes
                user=ctypes.windll.user32
                user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
                try:port.current(window['id'],window['pid'])
                except CaptureError:pass
                else:user.PostMessageW(int(window['id']),0x0010,0,0)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                process.wait(timeout=5)
        with pytest.raises(CaptureError,match='closed or changed owner'):port.capture(window)
