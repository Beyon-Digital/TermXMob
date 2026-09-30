"""Windows sandbox launch shim (stdlib ctypes only).

Started by ``windows_runner`` as a normal ``asyncio.subprocess`` — the parent
therefore keeps an ordinary ``asyncio.subprocess.Process`` surface
(stdout/stdin pipes, wait, terminate) while the real child runs under a
restricted low-integrity token inside a Job Object this shim owns.

Protocol:

* argv[1] is a JSON spawn-request file written by the runner; the shim reads
  then deletes it.
* The shim builds a restricted primary token at low integrity
  (``S-1-16-4096``), creates inheritable stdio pipes, spawns the target
  suspended via ``CreateProcessWithTokenW`` (Secondary Logon — no special
  privilege needed), assigns it to a Job Object with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` plus the requested limits, resumes
  the main thread, then pumps stdio in both directions.
* Exit code is relayed: the shim ``os._exit``s with the child's real code.
* If the shim is terminated (parent ``terminate()``), its job handle closes,
  ``KILL_ON_JOB_CLOSE`` fires, and the entire restricted tree dies — a real
  kernel-enforced process-tree kill.

This module is intentionally import-light: it runs as
``python -m termx.sandbox._win_shim`` inside a clean environment.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover - module is win32-only
    raise RuntimeError("_win_shim is Windows-only")

import ctypes
import ctypes.wintypes as wt
import os
import subprocess  # noqa: F401  (kept for symmetry/tools importing shim)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_LPHANDLE = ctypes.POINTER(wt.HANDLE)

_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetStdHandle.restype = wt.HANDLE
_kernel32.GetStdHandle.argtypes = [wt.DWORD]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.LocalFree.argtypes = [wt.LPVOID]
_kernel32.LocalFree.restype = wt.LPVOID
_kernel32.ResumeThread.argtypes = [wt.HANDLE]
_kernel32.ResumeThread.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_kernel32.GetExitCodeProcess.restype = wt.BOOL
_kernel32.CreatePipe.argtypes = [_LPHANDLE, _LPHANDLE, wt.LPVOID, wt.DWORD]
_kernel32.CreatePipe.restype = wt.BOOL
_kernel32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
_kernel32.SetHandleInformation.restype = wt.BOOL
_kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
_kernel32.ReadFile.restype = wt.BOOL
_kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
_kernel32.WriteFile.restype = wt.BOOL
_kernel32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wt.HANDLE
_kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wt.BOOL
_kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
_kernel32.SetInformationJobObject.restype = wt.BOOL
_kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
_kernel32.TerminateJobObject.restype = wt.BOOL
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_kernel32.TerminateProcess.restype = wt.BOOL
_kernel32.QueryInformationJobObject.argtypes = [
    wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, wt.LPVOID]
_kernel32.QueryInformationJobObject.restype = wt.BOOL

_advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, _LPHANDLE]
_advapi32.OpenProcessToken.restype = wt.BOOL
_advapi32.DuplicateTokenEx.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, ctypes.c_int, ctypes.c_int, _LPHANDLE]
_advapi32.DuplicateTokenEx.restype = wt.BOOL
_advapi32.CreateRestrictedToken.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.LPVOID, wt.DWORD, wt.LPVOID, _LPHANDLE]
_advapi32.CreateRestrictedToken.restype = wt.BOOL
_advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.LPVOID)]
_advapi32.ConvertStringSidToSidW.restype = wt.BOOL
_advapi32.SetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
_advapi32.SetTokenInformation.restype = wt.BOOL
_advapi32.CreateProcessWithTokenW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPCWSTR, wt.LPWSTR, wt.DWORD, wt.LPVOID,
    wt.LPCWSTR, wt.LPVOID, wt.LPVOID]
_advapi32.CreateProcessWithTokenW.restype = wt.BOOL
_advapi32.CreateProcessAsUserW.argtypes = [
    wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL,
    wt.DWORD, wt.LPVOID, wt.LPCWSTR, wt.LPVOID, wt.LPVOID]
_advapi32.CreateProcessAsUserW.restype = wt.BOOL
_advapi32.LogonUserW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, wt.DWORD, _LPHANDLE]
_advapi32.LogonUserW.restype = wt.BOOL
_advapi32.GetTokenInformation.argtypes = [
    wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_advapi32.GetTokenInformation.restype = wt.BOOL
_advapi32.LookupPrivilegeValueW.argtypes = [
    wt.LPVOID, wt.LPCWSTR, wt.LPVOID]
_advapi32.LookupPrivilegeValueW.restype = wt.BOOL

TOKEN_ALL_ACCESS = 0xF01FF
_SECURITY_IMPERSONATION = 2
_TOKEN_PRIMARY = 1
_DISABLE_MAX_PRIVILEGE = 0x1
_TOKEN_INTEGRITY_LEVEL = 25
_SE_GROUP_INTEGRITY = 0x20
_LOW_IL_SID = "S-1-16-4096"

_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x1
_LOGON_WITH_PROFILE = 0x1
_LOGON32_LOGON_INTERACTIVE = 2
_LOGON32_PROVIDER_DEFAULT = 0
_TOKEN_PRIVILEGES_CLASS = 3
_SE_CHANGE_NOTIFY = "SeChangeNotifyPrivilege"

_STD_INPUT = -10
_STD_OUTPUT = -11
_STD_ERROR = -12
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x102

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", wt.LPVOID), ("Attributes", wt.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(wt.BYTE)), ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", wt.LPVOID),
                ("bInheritHandle", wt.BOOL)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wt.DWORD),
        ("TotalProcesses", wt.DWORD),
        ("ActiveProcesses", wt.DWORD),
        ("TotalTerminatedProcesses", wt.DWORD),
    ]


def _job_process_count(job: int) -> int:
    """Live process count in the job, or -1 when the query fails.

    Uses the fixed-size basic-accounting query — the process-ID list variant
    fails outright once the job holds more IDs than the buffer holds, and
    a caller must never read that failure as "no live writers".
    """
    info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    if not _kernel32.QueryInformationJobObject(
        job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
        ctypes.sizeof(info), None,
    ):
        return -1
    return info.ActiveProcesses


def _last_error() -> OSError:
    e = ctypes.get_last_error()
    return OSError(f"win32 error {e}: {ctypes.FormatError(e)}")


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wt.DWORD)]


def _token_privilege_luids(token: wt.HANDLE) -> "list[_LUID]":
    """LUIDs of every privilege on a token (for a selective delete list)."""
    needed = wt.DWORD(0)
    _advapi32.GetTokenInformation(
        token, _TOKEN_PRIVILEGES_CLASS, None, 0, ctypes.byref(needed)
    )
    if not needed.value:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not _advapi32.GetTokenInformation(
        token, _TOKEN_PRIVILEGES_CLASS, buf, needed, ctypes.byref(needed)
    ):
        raise _last_error()
    count = ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0]
    entries = (_LUID_AND_ATTRIBUTES * count).from_address(
        ctypes.addressof(buf) + 4
    )
    return [_LUID(e.Luid.LowPart, e.Luid.HighPart) for e in entries]


def build_restricted_token(
    base: "int | None" = None, *, keep_traverse_privilege: bool = False
) -> int:
    """Duplicate a token, strip privileges, force low integrity.

    ``base`` is an existing token handle (e.g. a LogonUser result for the
    dedicated sandbox account); without one the current process token is
    used.

    With ``keep_traverse_privilege`` every privilege EXCEPT
    SeChangeNotifyPrivilege is deleted instead of the blanket
    DISABLE_MAX_PRIVILEGE. The foreign sandbox identity needs bypass-
    traverse-checking to even reach a DACL-granted workspace below the
    host user's private directories; stripping it makes every nested
    granted path unreachable. The same-user token keeps max restriction
    (its own DACL already grants traverse). Change-notify only skips
    directory listing checks — every file ACL still applies.
    """
    if base is not None:
        token = wt.HANDLE(base)
    else:
        token = wt.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(token)
        ):
            raise _last_error()
    dup = wt.HANDLE()
    if not _advapi32.DuplicateTokenEx(
        token, TOKEN_ALL_ACCESS, None, _SECURITY_IMPERSONATION, _TOKEN_PRIMARY,
        ctypes.byref(dup),
    ):
        raise _last_error()
    restricted = wt.HANDLE()
    if keep_traverse_privilege:
        keep = _LUID()
        if not _advapi32.LookupPrivilegeValueW(
            None, _SE_CHANGE_NOTIFY, ctypes.byref(keep)
        ):
            raise _last_error()
        delete = [
            l for l in _token_privilege_luids(dup)
            if not (l.LowPart == keep.LowPart and l.HighPart == keep.HighPart)
        ]
        # PrivilegesToDelete takes LUID_AND_ATTRIBUTES[] (12-byte entries),
        # not bare LUIDs — an 8-byte stride misreads the whole list.
        arr = (_LUID_AND_ATTRIBUTES * len(delete))()
        for i, luid in enumerate(delete):
            arr[i].Luid = luid
        if not _advapi32.CreateRestrictedToken(
            dup, 0, 0, None, len(delete), arr, 0, None,
            ctypes.byref(restricted),
        ):
            raise _last_error()
    elif not _advapi32.CreateRestrictedToken(
        dup, _DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None,
        ctypes.byref(restricted),
    ):
        raise _last_error()
    _kernel32.CloseHandle(dup)
    sid = wt.LPVOID()
    if not _advapi32.ConvertStringSidToSidW(_LOW_IL_SID, ctypes.byref(sid)):
        raise _last_error()
    label = _TOKEN_MANDATORY_LABEL()
    label.Label.Sid = sid
    label.Label.Attributes = _SE_GROUP_INTEGRITY
    if not _advapi32.SetTokenInformation(
        restricted, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label),
        ctypes.sizeof(label),
    ):
        raise _last_error()
    _kernel32.LocalFree(sid)
    return restricted.value


def _env_block(env: dict):
    """Sorted KEY=value\\0...\\0 buffer for CREATE_UNICODE_ENVIRONMENT.

    Returns the ctypes buffer object itself — the caller must keep it alive
    until the CreateProcess* call returns.
    """
    if not env:
        return None
    text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper()))
    return ctypes.create_unicode_buffer(text + "\0")


_SANDBOX_USER = "termx-sandbox"


def sandbox_user_credentials() -> "dict | None":
    """Dedicated restricted-user credentials, when admin-provisioned.

    Contract: local account ``termx-sandbox`` exists AND
    ``%ProgramData%\\termx\\sandbox-user.cred`` holds a DPAPI machine-scope
    blob of the generated password (UTF-8) AND the account holds rights on
    ``winsta0`` + the ``default`` desktop (``grant_winsta_desktop_access`` —
    without them the child hangs during process init). Provisioning is an
    install-time elevation step; reading needs no admin. Returns None when
    either piece is absent so callers degrade to token mode.
    """
    cred_file = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "termx" / "sandbox-user.cred"
    netapi32 = ctypes.WinDLL("netapi32", use_last_error=True)
    netapi32.NetUserGetInfo.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                      ctypes.POINTER(wt.LPVOID)]
    netapi32.NetUserGetInfo.restype = wt.DWORD
    netapi32.NetApiBufferFree.argtypes = [wt.LPVOID]
    buf = wt.LPVOID()
    if netapi32.NetUserGetInfo(None, _SANDBOX_USER, 0, ctypes.byref(buf)) != 0:
        return None
    if buf:
        netapi32.NetApiBufferFree(buf)
    if not cred_file.is_file():
        return None

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", wt.LPVOID)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wt.BOOL
    data = cred_file.read_bytes()
    blob_in = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), wt.LPVOID))
    blob_out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None,
                                      None, 0, ctypes.byref(blob_out)):
        return None
    try:
        password = ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(blob_out.pbData)
    if not password:
        return None
    return {"user": _SANDBOX_USER, "domain": ".", "password": password}


def logon_user_token(creds: dict) -> int:
    """Primary token for the sandbox account via LogonUser (INTERACTIVE).

    INTERACTIVE logon of a local account needs no special privilege on
    supported Windows; the resulting primary token is then restricted the
    same way as our own token. Raises OSError on failure — callers must
    propagate, never fall back to a weaker spawn.
    """
    token = wt.HANDLE()
    if not _advapi32.LogonUserW(
        creds["user"], creds.get("domain") or ".", creds["password"],
        _LOGON32_LOGON_INTERACTIVE, _LOGON32_PROVIDER_DEFAULT,
        ctypes.byref(token),
    ):
        raise _last_error()
    return token.value


def canary_spawn(token: int) -> int:
    """Spawn ``cmd /c exit 0`` under ``token`` through the run() spawn chain.

    Used by availability probes: proves the host can actually launch a
    process under this token (CreateProcessWithTokenW, with the
    CreateProcessAsUserW privileged fallback) and confine it in a Job
    Object — rather than assuming a well-formed token implies a working
    spawn. Returns the child exit code; raises OSError when no launch path
    works.
    """
    si = _STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = _PROCESS_INFORMATION()
    cmd = (
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
        + " /c exit 0"
    )
    flags = _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT
    last_exc: "OSError | None" = None
    if not _advapi32.CreateProcessWithTokenW(
        token, 0, None, cmd, flags, None, None,
        ctypes.byref(si), ctypes.byref(pi),
    ):
        last_exc = _last_error()
        if not _advapi32.CreateProcessAsUserW(
            token, None, cmd, None, None, False, flags, None, None,
            ctypes.byref(si), ctypes.byref(pi),
        ):
            last_exc = _last_error()
        else:
            last_exc = None
    if last_exc is not None:
        raise last_exc
    resumed = False
    job = 0
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _last_error()
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info.BasicLimitInformation.ActiveProcessLimit = 8
        if not _kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise _last_error()
        if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
            raise _last_error()
        _kernel32.ResumeThread(pi.hThread)
        resumed = True
        _kernel32.WaitForSingleObject(pi.hProcess, 15000)
        code = wt.DWORD()
        _kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        return code.value
    finally:
        if job:
            _kernel32.CloseHandle(job)
        if not resumed:
            # A failed setup must not strand the suspended canary process.
            _kernel32.TerminateProcess(pi.hProcess, 1)
        _kernel32.CloseHandle(pi.hProcess)
        _kernel32.CloseHandle(pi.hThread)


def probe_sandbox_user() -> bool:
    """True when provisioned creds exist AND actually produce a logon token
    AND the account can see the interactive window station/desktop AND a
    real canary spawns under the restricted user token.

    A child spawned as a foreign user hangs during process init when it has
    no rights on ``winsta0``/``default`` (verified on Server 2022), so the
    provisioning contract includes the window-station/desktop grant; check
    it here so user mode is never advertised into a hung spawn. The canary
    matters on hosts without SE_IMPERSONATE_NAME (a standard-user account):
    CreateProcessWithTokenW would fail there and CreateProcessAsUserW wants
    SE_ASSIGNPRIMARYTOKEN, so without the canary the runner would pick a
    mode in which nothing can start.
    """
    creds = sandbox_user_credentials()
    if creds is None:
        return False
    try:
        handle = logon_user_token(creds)
    except OSError:
        return False
    try:
        sid = _account_sid(_SANDBOX_USER)
        if not _winsta_desktop_granted(sid):
            _kernel32.CloseHandle(handle)
            return False
    except OSError:
        _kernel32.CloseHandle(handle)
        return False
    try:
        token = build_restricted_token(handle, keep_traverse_privilege=True)
    except OSError:
        _kernel32.CloseHandle(handle)
        return False
    _kernel32.CloseHandle(handle)
    try:
        ok = canary_spawn(token) == 0
    except OSError:
        ok = False
    _kernel32.CloseHandle(token)
    return ok


# -- provisioning helpers --------------------------------------------------
# `termx sandbox provision` (elevated, install-time) uses these: create the
# account, write the DPAPI cred, grant window-station/desktop access.

_WRITE_DAC = 0x00040000
_READ_CONTROL = 0x00020000
_GENERIC_ALL = 0x10000000
_DACL_SECURITY_INFORMATION = 4
_ACL_REVISION = 2
_WINSTA_ALL_ACCESS = 0x000F037F
_DESKTOP_ALL_ACCESS = 0x000F01FF
_GRANT_ACCESS = 1
_NO_INHERITANCE = 0
_TRUSTEE_IS_SID = 0
_TRUSTEE_IS_USER = 1


class _TRUSTEE_W(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", wt.LPVOID),
        ("MultipleTrusteeOperation", ctypes.c_int),
        ("TrusteeForm", ctypes.c_int),
        ("TrusteeType", ctypes.c_int),
        # Union slot (name string / PSID / objects): keep raw — LPWSTR
        # would wrongly decode SID pointers as text on read.
        ("ptstrName", wt.LPVOID),
    ]


class _EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", wt.DWORD),
        ("grfAccessMode", ctypes.c_int),
        ("grfInheritance", wt.DWORD),
        ("Trustee", _TRUSTEE_W),
    ]


def _user32():
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.OpenWindowStationW.argtypes = [wt.LPCWSTR, wt.BOOL, wt.DWORD]
    dll.OpenWindowStationW.restype = wt.HANDLE
    dll.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
    dll.OpenDesktopW.restype = wt.HANDLE
    dll.GetUserObjectSecurity.argtypes = [
        wt.HANDLE, ctypes.POINTER(wt.DWORD), wt.LPVOID, wt.DWORD,
        ctypes.POINTER(wt.DWORD)]
    dll.GetUserObjectSecurity.restype = wt.BOOL
    dll.SetUserObjectSecurity.argtypes = [
        wt.HANDLE, ctypes.POINTER(wt.DWORD), wt.LPVOID]
    dll.SetUserObjectSecurity.restype = wt.BOOL
    return dll


def _account_sid(name: str) -> str:
    adv = _advapi32
    adv.LookupAccountNameW.argtypes = [
        wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, ctypes.POINTER(wt.DWORD),
        wt.LPWSTR, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]
    adv.LookupAccountNameW.restype = wt.BOOL
    adv.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wt.BOOL
    cb_sid = wt.DWORD(0)
    cb_dom = wt.DWORD(0)
    use = wt.DWORD(0)
    adv.LookupAccountNameW(None, name, None, ctypes.byref(cb_sid),
                           None, ctypes.byref(cb_dom), ctypes.byref(use))
    if not cb_sid.value:
        raise _last_error()
    sid_buf = ctypes.create_string_buffer(cb_sid.value)
    dom = ctypes.create_unicode_buffer(cb_dom.value)
    if not adv.LookupAccountNameW(None, name, sid_buf, ctypes.byref(cb_sid),
                                  dom, ctypes.byref(cb_dom), ctypes.byref(use)):
        raise _last_error()
    s = wt.LPWSTR()
    adv.ConvertSidToStringSidW(sid_buf, ctypes.byref(s))
    return s.value


def _object_dacl(u32: "ctypes.WinDLL", handle: int):
    """(sd_buffer, dacl_ptr) — keep sd_buffer alive while using dacl_ptr."""
    needed = wt.DWORD(0)
    si = wt.DWORD(_DACL_SECURITY_INFORMATION)
    u32.GetUserObjectSecurity(handle, ctypes.byref(si), None, 0,
                              ctypes.byref(needed))
    if not needed.value:
        raise _last_error()
    sd = ctypes.create_string_buffer(needed.value)
    if not u32.GetUserObjectSecurity(handle, ctypes.byref(si), sd,
                                     needed, ctypes.byref(needed)):
        raise _last_error()
    present = wt.BOOL()
    dacl = wt.LPVOID()
    defaulted = wt.BOOL()
    if not _advapi32.GetSecurityDescriptorDacl(
            sd, ctypes.byref(present), ctypes.byref(dacl),
            ctypes.byref(defaulted)):
        raise _last_error()
    return sd, (dacl if present.value else None)


def grant_winsta_desktop_access(account: str = _SANDBOX_USER) -> None:
    """Give ``account`` access to winsta0 + the default desktop.

    Provision-time step (needs WRITE_DAC on those objects — an elevated
    host). Without it, a child running as a foreign user hangs during
    process init instead of starting.
    """
    adv = _advapi32
    adv.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.LPVOID)]
    adv.ConvertStringSidToSidW.restype = wt.BOOL
    adv.SetEntriesInAclW.argtypes = [
        wt.ULONG, ctypes.POINTER(_EXPLICIT_ACCESS_W), wt.LPVOID,
        ctypes.POINTER(wt.LPVOID)]
    adv.SetEntriesInAclW.restype = wt.DWORD
    adv.InitializeSecurityDescriptor.argtypes = [wt.LPVOID, wt.DWORD]
    adv.InitializeSecurityDescriptor.restype = wt.BOOL
    adv.SetSecurityDescriptorDacl.argtypes = [
        wt.LPVOID, wt.BOOL, wt.LPVOID, wt.BOOL]
    adv.SetSecurityDescriptorDacl.restype = wt.BOOL
    u32 = _user32()
    sid_text = _account_sid(account)
    sid = wt.LPVOID()
    if not adv.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
        raise _last_error()
    try:
        for name, mask in (("winsta0", _WINSTA_ALL_ACCESS),
                           ("default-desktop", _DESKTOP_ALL_ACCESS)):
            if name == "winsta0":
                handle = u32.OpenWindowStationW(
                    "winsta0", False, _WRITE_DAC | _READ_CONTROL)
            else:
                handle = u32.OpenDesktopW(
                    "default", 0, False,
                    _WRITE_DAC | _READ_CONTROL | _GENERIC_ALL)
            if not handle:
                raise _last_error()
            sd, dacl = _object_dacl(u32, handle)
            ea = _EXPLICIT_ACCESS_W()
            ea.grfAccessPermissions = mask
            ea.grfAccessMode = _GRANT_ACCESS
            ea.grfInheritance = _NO_INHERITANCE
            ea.Trustee.TrusteeForm = _TRUSTEE_IS_SID
            ea.Trustee.TrusteeType = _TRUSTEE_IS_USER
            ea.Trustee.ptstrName = sid.value
            new_dacl = wt.LPVOID()
            rc = adv.SetEntriesInAclW(1, ctypes.byref(ea), dacl,
                                    ctypes.byref(new_dacl))
            del sd
            if rc != 0:
                raise OSError(f"SetEntriesInAclW failed: {rc}")
            try:
                nsd = ctypes.create_string_buffer(64)
                if not adv.InitializeSecurityDescriptor(nsd, 1):
                    raise _last_error()
                if not adv.SetSecurityDescriptorDacl(nsd, True, new_dacl, False):
                    raise _last_error()
                si = wt.DWORD(_DACL_SECURITY_INFORMATION)
                if not u32.SetUserObjectSecurity(
                        handle, ctypes.byref(si), nsd):
                    raise _last_error()
            finally:
                _kernel32.LocalFree(new_dacl)
            _kernel32.CloseHandle(handle)
    finally:
        _kernel32.LocalFree(sid)


def _winsta_desktop_granted(sid_text: str) -> bool:
    """Read-only check: does the SID already hold winsta0+desktop rights?"""
    adv = _advapi32
    adv.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.LPVOID)]
    adv.ConvertStringSidToSidW.restype = wt.BOOL
    adv.GetExplicitEntriesFromAclW.argtypes = [
        wt.LPVOID, ctypes.POINTER(wt.ULONG), ctypes.POINTER(wt.LPVOID)]
    adv.GetExplicitEntriesFromAclW.restype = wt.DWORD
    adv.EqualSid.argtypes = [wt.LPVOID, wt.LPVOID]
    adv.EqualSid.restype = wt.BOOL
    u32 = _user32()
    sid = wt.LPVOID()
    if not adv.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
        raise _last_error()
    try:
        for which in ("winsta", "desktop"):
            handle = (
                u32.OpenWindowStationW("winsta0", False, _READ_CONTROL)
                if which == "winsta"
                else u32.OpenDesktopW("default", 0, False, _READ_CONTROL)
            )
            if not handle:
                return False
            # dacl points into sd — keep sd referenced for the whole scan.
            sd, dacl = _object_dacl(u32, handle)
            _kernel32.CloseHandle(handle)
            if not dacl:
                return False
            count = wt.ULONG(0)
            entries = wt.LPVOID()
            rc = adv.GetExplicitEntriesFromAclW(
                dacl, ctypes.byref(count), ctypes.byref(entries))
            if rc != 0:
                return False
            found = False
            if count.value and entries.value:
                arr = (_EXPLICIT_ACCESS_W * count.value).from_address(
                    entries.value)
                for e in arr:
                    if (e.Trustee.TrusteeForm == _TRUSTEE_IS_SID
                            and e.Trustee.ptstrName
                            and adv.EqualSid(e.Trustee.ptstrName, sid)):
                        found = True
                        break
            if entries.value:
                _kernel32.LocalFree(entries)
            del sd
            if not found:
                return False
        return True
    finally:
        _kernel32.LocalFree(sid)


def _inheritable_pipe() -> "tuple[int, int]":
    """(read_end_inheritable, write_end_inheritable) for child std handles."""
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    rd, wr = wt.HANDLE(), wt.HANDLE()
    if not _kernel32.CreatePipe(ctypes.byref(rd), ctypes.byref(wr), ctypes.byref(sa), 0):
        raise _last_error()
    return rd.value, wr.value


def _pump_read_to_handle(rd: int, dst: int) -> None:
    buf = ctypes.create_string_buffer(65536)
    while True:
        n = wt.DWORD()
        if not _kernel32.ReadFile(rd, buf, len(buf), ctypes.byref(n), None) or not n.value:
            return
        total = 0
        while total < n.value:
            written = wt.DWORD()
            if not _kernel32.WriteFile(dst, ctypes.byref(buf, total), n.value - total,
                                       ctypes.byref(written), None):
                return
            total += written.value


def _pump_stdin_to_pipe(src: int, wr: int) -> None:
    buf = ctypes.create_string_buffer(65536)
    while True:
        n = wt.DWORD()
        ok = _kernel32.ReadFile(src, buf, len(buf), ctypes.byref(n), None)
        if not ok or not n.value:
            break
        total = 0
        while total < n.value:
            written = wt.DWORD()
            if not _kernel32.WriteFile(wr, ctypes.byref(buf, total), n.value - total,
                                       ctypes.byref(written), None):
                break
            total += written.value
        else:
            continue
        break
    _kernel32.CloseHandle(wr)


def run(request_path: str) -> int:
    req = json.loads(Path(request_path).read_text(encoding="utf-8"))
    try:
        Path(request_path).unlink()
    except OSError:
        pass

    if req.get("mode") == "user":
        # Credentials are loaded here, not passed through the request file —
        # the password never touches disk in cleartext. The child still gets
        # the full restricted-token treatment (privileges stripped, low IL);
        # only the token's identity differs.
        logon = sandbox_user_credentials()
        if logon is None:
            raise SystemExit("sandbox shim: requested user mode but no provisioned termx-sandbox account")
        try:
            base = logon_user_token(logon)
        except OSError as exc:
            raise SystemExit(f"sandbox shim: LogonUser for {_SANDBOX_USER} failed: {exc}")
        try:
            token = build_restricted_token(base, keep_traverse_privilege=True)
        finally:
            _kernel32.CloseHandle(base)
    else:
        token = build_restricted_token()

    env_buf = _env_block(req.get("env") or {})
    env_ptr = ctypes.cast(env_buf, wt.LPVOID) if env_buf is not None else None
    # Child std handles: pipes whose far ends stay with us. The ends handed
    # to the child stay inheritable; the ends we keep are marked
    # non-inheritable so a broad handle-inheritance spawn cannot hand the
    # child copies of its own pump ends.
    in_rd, in_wr = _inheritable_pipe()     # we write -> child reads
    out_rd, out_wr = _inheritable_pipe()   # child writes -> we read
    err_rd, err_wr = _inheritable_pipe()   # child writes -> we read
    for h in (in_wr, out_rd, err_rd):
        _kernel32.SetHandleInformation(h, _HANDLE_FLAG_INHERIT, 0)

    si = _STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = _STARTF_USESTDHANDLES
    si.hStdInput = in_rd
    si.hStdOutput = out_wr
    si.hStdError = err_wr
    pi = _PROCESS_INFORMATION()
    cmdline = req["command"]
    cwd = req.get("cwd") or None
    flags = _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT
    # Secondary Logon service path first (works without special
    # privileges); CreateProcessAsUser needs SE_ASSIGNPRIMARYTOKEN and is
    # retained as the privileged fallback. LOGON_WITH_PROFILE cannot load a
    # profile for a cloned other-user token (5023, verified) — user mode
    # spawns with no profile-load flag; USERPROFILE/TEMP come from the env
    # block either way.
    logon_flags = 0 if req.get("mode") == "user" else _LOGON_WITH_PROFILE
    spawned = False
    last_exc: "OSError | None" = None
    if _advapi32.CreateProcessWithTokenW(
        token, logon_flags, None, cmdline, flags, env_ptr, cwd,
        ctypes.byref(si), ctypes.byref(pi),
    ):
        spawned = True
    else:
        last_exc = _last_error()
        # bInheritHandles must stay TRUE or the STARTF_USESTDHANDLES
        # handles never reach the child (verified: FALSE yields empty
        # stdio). Bound what TRUE sweeps in: our own std handles are the
        # only other inheritable handles — mark them non-inheritable so
        # the child receives exactly the three pipe ends it was given.
        for _h in (
            _kernel32.GetStdHandle(_STD_INPUT),
            _kernel32.GetStdHandle(_STD_OUTPUT),
            _kernel32.GetStdHandle(_STD_ERROR),
        ):
            _kernel32.SetHandleInformation(_h, _HANDLE_FLAG_INHERIT, 0)
        if _advapi32.CreateProcessAsUserW(
            token, None, cmdline, None, None, True, flags, env_ptr, cwd,
            ctypes.byref(si), ctypes.byref(pi),
        ):
            spawned = True
            last_exc = None
    if not spawned:
        raise SystemExit(f"sandbox shim: cannot create restricted process: {last_exc}")

    # Parent no longer needs the child's copies.
    for h in (in_rd, out_wr, err_wr):
        _kernel32.CloseHandle(h)

    # From here on a failure must reap the suspended child itself — it is not
    # in the job yet, so no job-close path would kill it, and it holds the
    # stdio pipe ends that keep the parent's readers waiting for EOF.
    def _fail_spawn(msg: str) -> "SystemExit":
        _kernel32.TerminateProcess(pi.hProcess, 1)
        _kernel32.WaitForSingleObject(pi.hProcess, 5000)
        for h in (pi.hProcess, pi.hThread):
            _kernel32.CloseHandle(h)
        return SystemExit(f"sandbox shim: {msg}")

    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _fail_spawn("CreateJobObject failed: " + str(_last_error()))
    job_req = req.get("job") or {}
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if job_req.get("active_process_limit"):
        info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = int(job_req["active_process_limit"])
    if job_req.get("process_memory_bytes"):
        info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = int(job_req["process_memory_bytes"])
    if job_req.get("job_memory_bytes"):
        info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
        info.JobMemoryLimit = int(job_req["job_memory_bytes"])
    if job_req.get("job_time_100ns"):
        info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_JOB_TIME
        info.BasicLimitInformation.PerJobUserTimeLimit = int(job_req["job_time_100ns"])
    if not _kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _kernel32.CloseHandle(job)
        raise _fail_spawn("SetInformationJobObject failed: " + str(_last_error()))
    if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
        _kernel32.CloseHandle(job)
        raise _fail_spawn("AssignProcessToJobObject failed: " + str(_last_error()))

    _kernel32.ResumeThread(pi.hThread)

    our_out = _kernel32.GetStdHandle(_STD_OUTPUT)
    our_err = _kernel32.GetStdHandle(_STD_ERROR)
    our_in = _kernel32.GetStdHandle(_STD_INPUT)
    pumps = [
        threading.Thread(target=_pump_read_to_handle, args=(out_rd, our_out), daemon=True),
        threading.Thread(target=_pump_read_to_handle, args=(err_rd, our_err), daemon=True),
        threading.Thread(target=_pump_stdin_to_pipe, args=(our_in, in_wr), daemon=True),
    ]
    for t in pumps:
        t.start()

    _kernel32.WaitForSingleObject(pi.hProcess, _INFINITE)
    code = wt.DWORD()
    _kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))

    # Drain remaining output. Descendants may still hold the stdio pipes past
    # the leader's exit — give them a bounded grace period, then once every
    # writer is *proven* gone the pipes hit EOF and the pumps drain the
    # buffered tail completely (an output tail must never be cut off early).
    # If a stray descendant keeps a pipe open past the grace period — or the
    # job-count query can't prove emptiness — fall back to a bounded join so
    # the shim cannot wedge on it.
    deadline = time.monotonic() + 5.0
    while _job_process_count(job) > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    if _job_process_count(job) == 0:
        for t in pumps[:2]:
            t.join()
    else:
        for t in pumps[:2]:
            t.join(timeout=10)
    # in_wr is owned by the stdin pump (it closes to propagate EOF).
    for h in (out_rd, err_rd, pi.hProcess, pi.hThread):
        _kernel32.CloseHandle(h)
    if token:
        _kernel32.CloseHandle(token)
    _kernel32.CloseHandle(job)  # KILL_ON_JOB_CLOSE reaps any stragglers
    return code.value


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m termx.sandbox._win_shim <request.json>")
    os._exit(run(sys.argv[1]))


if __name__ == "__main__":
    main()
