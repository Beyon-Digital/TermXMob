"""Exact native window capture. Never falls back to a desktop crop."""
from __future__ import annotations

import ctypes
import ctypes.util
import io
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from termx.desktop.capture import CaptureError


class WindowCapture:
    def capability(self):
        supported = sys.platform in {'darwin', 'win32'} or bool(os.environ.get('DISPLAY') and shutil.which('wmctrl') and shutil.which('import'))
        return {'supported': supported, 'backend': 'coregraphics-window' if sys.platform == 'darwin' else 'print-window' if sys.platform == 'win32' else 'x11-window',
                'reason': '' if supported else 'Exact window capture requires X11 with wmctrl and ImageMagick; a Wayland portal adapter is required on Wayland.'}

    def list(self):
        if sys.platform == 'darwin':
            cg = ctypes.CDLL(ctypes.util.find_library('CoreGraphics'))
            cf = ctypes.CDLL(ctypes.util.find_library('CoreFoundation'))
            cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
            cf.CFPropertyListCreateData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_ulong, ctypes.c_void_p]
            cf.CFPropertyListCreateData.restype = ctypes.c_void_p
            cf.CFDataGetLength.argtypes = [ctypes.c_void_p]; cf.CFDataGetLength.restype = ctypes.c_long
            cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]; cf.CFDataGetBytePtr.restype = ctypes.c_void_p
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            info = cg.CGWindowListCopyWindowInfo(1 | 16, 0)  # on screen, excluding desktop elements
            if not info: raise CaptureError('Window enumeration is unavailable')
            data = None
            try:
                data = cf.CFPropertyListCreateData(None, info, 200, 0, None)
                if not data: raise CaptureError('Window enumeration could not be decoded')
                rows = plistlib.loads(ctypes.string_at(cf.CFDataGetBytePtr(data), cf.CFDataGetLength(data)))
                result = []
                for row in rows:
                    bounds = row.get('kCGWindowBounds', {})
                    if row.get('kCGWindowLayer') != 0 or bounds.get('Width', 0) < 20 or bounds.get('Height', 0) < 20: continue
                    result.append({'id': str(row['kCGWindowNumber']), 'pid': int(row['kCGWindowOwnerPID']), 'app': row.get('kCGWindowOwnerName', ''), 'title': row.get('kCGWindowName', ''),
                                   'x': int(bounds.get('X', 0)), 'y': int(bounds.get('Y', 0)), 'width': int(bounds['Width']), 'height': int(bounds['Height'])})
                return result
            finally:
                if data: cf.CFRelease(data)
                cf.CFRelease(info)
        if sys.platform == 'win32':
            user = ctypes.windll.user32
            rows = []
            callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user.EnumWindows.argtypes = [callback, ctypes.c_void_p]
            user.IsWindowVisible.argtypes = [ctypes.c_void_p]
            user.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
            user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            def visit(hwnd, _):
                if not user.IsWindowVisible(hwnd): return True
                size = user.GetWindowTextLengthW(hwnd)
                if size <= 0: return True
                title = ctypes.create_unicode_buffer(size + 1); user.GetWindowTextW(hwnd, title, size + 1)
                rect = (ctypes.c_long * 4)(); user.GetWindowRect(hwnd, ctypes.byref(rect))
                pid = ctypes.c_ulong(); user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                rows.append({'id': str(hwnd), 'pid': pid.value, 'app': f'Process {pid.value}', 'title': title.value, 'x': rect[0], 'y': rect[1], 'width': rect[2] - rect[0], 'height': rect[3] - rect[1]})
                return True
            user.EnumWindows(callback(visit), 0)
            return rows
        if not self.capability()['supported']: raise CaptureError(self.capability()['reason'])
        result = subprocess.run(['wmctrl', '-lpG'], check=True, capture_output=True, text=True, timeout=5)
        rows = []
        for line in result.stdout.splitlines():
            fields = line.split(None, 8)
            if len(fields) < 9: continue
            identifier, _, pid, x, y, width, height, _, title = fields
            rows.append({'id': str(int(identifier, 16)), 'pid': int(pid), 'app': f'Process {pid}', 'title': title,
                         'x': int(x), 'y': int(y), 'width': int(width), 'height': int(height)})
        return rows

    def current(self, identifier, pid):
        row = next((row for row in self.list() if row['id'] == identifier and row['pid'] == pid), None)
        if not row: raise CaptureError('Selected window closed or changed owner; select it again')
        return row

    def capture(self, row):
        row = self.current(row['id'], row['pid'])
        if sys.platform == 'darwin':
            fd, filename = tempfile.mkstemp(suffix='.jpg'); os.close(fd)
            try:
                result = subprocess.run(['/usr/sbin/screencapture', '-x', '-l', row['id'], '-t', 'jpg', filename], capture_output=True, timeout=8)
                data = Path(filename).read_bytes()
                if result.returncode or not data: raise CaptureError('Window capture requires Screen Recording permission on this host')
                return data
            finally: Path(filename).unlink(missing_ok=True)
        if sys.platform == 'win32':
            from PIL import Image
            from ctypes import wintypes
            user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
            hwnd = int(row['id']); width, height = row['width'], row['height']
            if width * height > 40_000_000: raise CaptureError('Window exceeds capture limit')
            for name in ('GetWindowDC',): getattr(user, name).restype = ctypes.c_void_p
            for name in ('CreateCompatibleDC', 'CreateCompatibleBitmap', 'SelectObject'): getattr(gdi, name).restype = ctypes.c_void_p
            user.GetWindowDC.argtypes = [ctypes.c_void_p]
            user.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            user.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi.DeleteDC.argtypes = [ctypes.c_void_p]; gdi.DeleteObject.argtypes = [ctypes.c_void_p]
            gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            dc = user.GetWindowDC(hwnd); memory = gdi.CreateCompatibleDC(dc); bitmap = gdi.CreateCompatibleBitmap(dc, width, height); previous = gdi.SelectObject(memory, bitmap)
            class Header(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG), ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD), ('image', wintypes.DWORD), ('x', wintypes.LONG), ('y', wintypes.LONG), ('used', wintypes.DWORD), ('important', wintypes.DWORD)]
            try:
                if not user.PrintWindow(hwnd, memory, 2): raise CaptureError('This application does not support exact window capture')
                header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
                pixels = ctypes.create_string_buffer(width * height * 4)
                gdi.SelectObject(memory, previous)
                if not gdi.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0): raise CaptureError('Window pixels unavailable')
                image = Image.frombytes('RGB', (width, height), pixels.raw, 'raw', 'BGRX'); output = io.BytesIO(); image.save(output, 'JPEG', quality=80); return output.getvalue()
            finally:
                gdi.SelectObject(memory, previous); gdi.DeleteObject(bitmap); gdi.DeleteDC(memory); user.ReleaseDC(hwnd, dc)
        result = subprocess.run(['import', '-window', row['id'], 'jpeg:-'], capture_output=True, timeout=8)
        if result.returncode or not result.stdout: raise CaptureError('Exact X11 window capture failed')
        return result.stdout

    def input(self, row, event):
        """Never send keys to an incidental foreground app or click an occluder."""
        current = self.current(row['id'], row['pid'])
        if sys.platform == 'darwin':
            # Get frontmost PID without window titles, script input or shell interpolation.
            result = subprocess.run(['/usr/bin/osascript', '-e', 'tell application "System Events" to unix id of first application process whose frontmost is true'], capture_output=True, text=True, timeout=5)
            if result.returncode: raise CaptureError('Window control requires Accessibility and System Events permission on this host')
            if result.stdout.strip() != str(row['pid']):
                subprocess.run(['/usr/bin/osascript', '-e', 'tell application "System Events" to set frontmost of first application process whose unix id is ' + str(int(row['pid'])) + ' to true'], capture_output=True, timeout=5)
                result = subprocess.run(['/usr/bin/osascript', '-e', 'tell application "System Events" to unix id of first application process whose frontmost is true'], capture_output=True, text=True, timeout=5)
                if result.returncode or result.stdout.strip() != str(row['pid']): raise CaptureError('The selected application could not be focused; check host Accessibility permission')
            first = next(iter(self.list()), None)
            if not first or first['id'] != row['id']: raise CaptureError('Selected window must be the frontmost window')
        elif sys.platform == 'win32':
            ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
            ctypes.windll.user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            if int(ctypes.windll.user32.GetForegroundWindow() or 0) != int(row['id']): ctypes.windll.user32.SetForegroundWindow(int(row['id']))
            if int(ctypes.windll.user32.GetForegroundWindow()) != int(row['id']): raise CaptureError('Bring the selected window to the front before input')
        else:
            subprocess.run(['xdotool', 'windowactivate', '--sync', row['id']], capture_output=True, timeout=5)
            active = subprocess.run(['xdotool', 'getactivewindow'], capture_output=True, text=True, timeout=5)
            if active.returncode or active.stdout.strip() != row['id']: raise CaptureError('Bring the selected window to the front before input')
        from termx.desktop.input import apply_event
        if event['type'] == 'pointer':
            # Explicit absolute coordinates avoid a monitor-dependent scale.
            event = {**event, 'absolute': True, 'x': current['x'] + event['x'] * current['width'], 'y': current['y'] + event['y'] * current['height']}
        apply_event(event)
