"""Verify a console process before its first instruction runs."""
import ctypes
import ctypes.wintypes as wt
from termx.sandbox import _win_shim as win
kernel = win._kernel32
win._advapi32.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
win._advapi32.ConvertSidToStringSidW.restype = wt.BOOL
def _check(ok):
    if not ok: raise win._last_error()

def verify_child_token(process, expected):
    child = wt.HANDLE()
    _check(win._advapi32.OpenProcessToken(process, 8, ctypes.byref(child)))
    try:
        length = wt.DWORD()
        restricted = wt.DWORD()
        _check(win._advapi32.GetTokenInformation(child, 21, ctypes.byref(restricted),
            ctypes.sizeof(restricted), ctypes.byref(length)))
        if not restricted.value: raise PermissionError('Console child token is not filtered')
        def token_sid(token, kind):
            needed = wt.DWORD()
            win._advapi32.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
            _check(needed.value >= ctypes.sizeof(win._TOKEN_MANDATORY_LABEL))
            buffer = ctypes.create_string_buffer(needed.value)
            _check(win._advapi32.GetTokenInformation(token, kind, buffer, len(buffer), ctypes.byref(needed)))
            # TOKEN_USER and TOKEN_MANDATORY_LABEL both start with the
            # same SID_AND_ATTRIBUTES structure; buffer owns its SID.
            label = win._TOKEN_MANDATORY_LABEL.from_buffer(buffer)
            sid = wt.LPWSTR()
            _check(win._advapi32.ConvertSidToStringSidW(label.Label.Sid, ctypes.byref(sid)))
            try: return sid.value
            finally: kernel.LocalFree(ctypes.cast(sid, wt.LPVOID))
        if token_sid(child, win._TOKEN_INTEGRITY_LEVEL) != win._LOW_IL_SID:
            raise PermissionError('Console child token integrity changed')
        if token_sid(child, 1) != token_sid(expected, 1):
            raise PermissionError('Console child identity differs from its restricted parent')
        # The child may never recover a host privilege through parent
        # selection. Compare the complete LUID+attribute list, not a flag
        # also set on ordinary UAC-filtered medium-integrity tokens.
        def privileges(token):
            needed = wt.DWORD()
            win._advapi32.GetTokenInformation(token, 3, None, 0, ctypes.byref(needed))
            _check(needed.value >= ctypes.sizeof(wt.DWORD))
            data = ctypes.create_string_buffer(needed.value)
            _check(win._advapi32.GetTokenInformation(token, 3, data, len(data), ctypes.byref(needed)))
            count = wt.DWORD.from_buffer(data).value
            if 4 + count * ctypes.sizeof(win._LUID_AND_ATTRIBUTES) > len(data):
                raise PermissionError('Console token privilege buffer is invalid')
            entries = (win._LUID_AND_ATTRIBUTES * count).from_buffer(data, 4)
            return sorted((item.Luid.HighPart, item.Luid.LowPart, item.Attributes) for item in entries)
        if privileges(child) != privileges(expected):
            raise PermissionError('Console child token privileges differ from its restricted parent')
    finally: kernel.CloseHandle(child)

