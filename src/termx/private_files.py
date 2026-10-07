"""Owner-controlled authentication storage on Unix and Windows.

Windows uses a protected DACL, including the OS SYSTEM and administrators
principals (the equivalents of privileged root access), rather than chmod's
read-only bit. All Win32 failures are fatal before authentication state is used.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path


@lru_cache(maxsize=1)
def _api():
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ptr = ctypes.c_void_p
    signatures = {
        'OpenProcessToken': ([wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        'GetTokenInformation': ([wintypes.HANDLE, ctypes.c_int, ptr, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'ConvertSidToStringSidW': ([ptr, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
        'ConvertStringSecurityDescriptorToSecurityDescriptorW': ([wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'GetSecurityDescriptorDacl': ([ptr, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ptr), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        'GetSecurityDescriptorControl': ([ptr, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'SetNamedSecurityInfoW': ([wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ptr, ptr, ptr, ptr], wintypes.DWORD),
        'GetNamedSecurityInfoW': ([wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr)], wintypes.DWORD),
        'GetAce': ([ptr, wintypes.DWORD, ctypes.POINTER(ptr)], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(advapi, name); function.argtypes = arguments; function.restype = result
    kernel.GetCurrentProcess.argtypes = []; kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]; kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ptr]; kernel.LocalFree.restype = ptr
    return advapi, kernel


def _require(ok):
    if not ok:
        raise PermissionError('Windows authentication storage permissions could not be verified')


def _sid(pointer) -> str:
    advapi, kernel = _api(); value = wintypes.LPWSTR()
    _require(advapi.ConvertSidToStringSidW(pointer, ctypes.byref(value)))
    try: return value.value
    finally: kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))


def _current_sid() -> str:
    advapi, kernel = _api(); token = wintypes.HANDLE()
    _require(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
    try:
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        _require(length.value > 0)
        data = ctypes.create_string_buffer(length.value)
        _require(advapi.GetTokenInformation(token, 1, data, length.value, ctypes.byref(length)))
        return _sid(ctypes.c_void_p.from_buffer(data).value)
    finally: kernel.CloseHandle(token)


def protect_private_path(path: Path, *, directory: bool = False) -> None:
    """Replace permissions with an owner-only policy, preserving OS admin access."""
    if os.name != 'nt':
        path.chmod(0o700 if directory else 0o600)
        return
    advapi, kernel = _api(); descriptor = ctypes.c_void_p()
    current = _current_sid()
    existing_owner, existing_descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    _require(advapi.GetNamedSecurityInfoW(str(path.resolve()), 1, 0x00000001,
        ctypes.byref(existing_owner), None, None, None, ctypes.byref(existing_descriptor)) == 0)
    try:
        if not existing_owner.value or _sid(existing_owner) not in {current, 'S-1-5-32-544'}:
            raise PermissionError('Refusing to alter authentication storage owned by another Windows user')
    finally: kernel.LocalFree(existing_descriptor)
    inheritance = 'OICI' if directory else ''
    text = 'D:P' + ''.join(f'(A;{inheritance};FA;;;{sid})' for sid in [current, 'SY', 'BA'])
    _require(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(text, 1, ctypes.byref(descriptor), None))
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL(); acl = ctypes.c_void_p()
        _require(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
        _require(present.value and acl.value)
        _require(advapi.SetNamedSecurityInfoW(str(path.resolve()), 1, 0x80000004, None, None, acl, None) == 0)
    finally: kernel.LocalFree(descriptor)
    if not private_path_permissions(path):
        raise PermissionError('Windows authentication storage has an unsafe access policy')


def private_path_permissions(path: Path, *, require_protected: bool = True) -> bool:
    """Prove ordinary other users have no allow ACE; Unix checks owner-only mode."""
    if os.name != 'nt':
        return path.stat().st_mode & 0o077 == 0
    advapi, kernel = _api(); descriptor, acl, owner = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    _require(advapi.GetNamedSecurityInfoW(str(path.resolve()), 1, 0x00000005,
        ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor)) == 0)
    try:
        actor = _current_sid()
        if not acl.value or not owner.value or _sid(owner) not in {actor, 'S-1-5-32-544'}:
            return False
        control, revision = wintypes.WORD(), wintypes.DWORD()
        _require(advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
        if require_protected and not control.value & 0x1000: return False  # protected from parent ACL changes
        # ACL header: revision byte, reserved byte, size WORD, ACE count WORD.
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        allowed, has_actor = {actor, 'S-1-5-18', 'S-1-5-32-544'}, False
        for index in range(count):
            ace = ctypes.c_void_p(); _require(advapi.GetAce(acl, index, ctypes.byref(ace)))
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind == 1: continue  # ACCESS_DENIED_ACE cannot disclose data
            if kind != 0: return False  # unsupported allow/callback/object ACE
            sid = _sid(ace.value + 8)  # ACCESS_ALLOWED_ACE Header + Mask
            if sid not in allowed: return False
            has_actor |= sid == actor
        return has_actor
    finally: kernel.LocalFree(descriptor)
