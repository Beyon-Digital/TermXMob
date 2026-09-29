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
_advapi32.CreateProcessWithLogonW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, wt.LPCWSTR, wt.LPWSTR,
    wt.DWORD, wt.LPVOID, wt.LPCWSTR, wt.LPVOID, wt.LPVOID]
_advapi32.CreateProcessWithLogonW.restype = wt.BOOL

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

_STD_INPUT = -10
_STD_OUTPUT = -11
_STD_ERROR = -12
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x102

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
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


def _last_error() -> OSError:
    e = ctypes.get_last_error()
    return OSError(f"win32 error {e}: {ctypes.FormatError(e)}")


def build_restricted_token() -> int:
    """Duplicate our process token, strip privileges, force low integrity."""
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
    if not _advapi32.CreateRestrictedToken(
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
    blob of the generated password (UTF-8). Provisioning is an install-time
    elevation step; reading needs no admin. Returns None when either piece
    is absent so callers degrade to token mode.
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

    token = None
    logon = None
    if req.get("mode") == "user":
        # Credentials are loaded here, not passed through the request file —
        # the password never touches disk in cleartext.
        logon = sandbox_user_credentials()
        if logon is None:
            raise SystemExit("sandbox shim: requested user mode but no provisioned termx-sandbox account")
    if logon is None:
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
    spawned = False
    last_exc: "OSError | None" = None
    if logon is not None:
        if _advapi32.CreateProcessWithLogonW(
            logon["user"], logon.get("domain") or ".", logon["password"],
            _LOGON_WITH_PROFILE, None, cmdline, flags, env_ptr, cwd,
            ctypes.byref(si), ctypes.byref(pi),
        ):
            spawned = True
        else:
            last_exc = _last_error()
    else:
        # Secondary Logon service path first (works without special
        # privileges); CreateProcessAsUser needs SE_ASSIGNPRIMARYTOKEN and is
        # retained as the privileged fallback.
        if _advapi32.CreateProcessWithTokenW(
            token, _LOGON_WITH_PROFILE, None, cmdline, flags, env_ptr, cwd,
            ctypes.byref(si), ctypes.byref(pi),
        ):
            spawned = True
        else:
            last_exc = _last_error()
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

    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise SystemExit("sandbox shim: CreateJobObject failed: " + str(_last_error()))
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
        raise SystemExit("sandbox shim: SetInformationJobObject failed: " + str(_last_error()))
    if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
        raise SystemExit("sandbox shim: AssignProcessToJobObject failed: " + str(_last_error()))

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

    # Drain remaining output: pumps end on pipe EOF; a descendant holding the
    # pipe past the leader's exit must not wedge the shim forever.
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
