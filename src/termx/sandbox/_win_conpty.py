"""Real ConPTY attached to a restricted token and a kill-on-close Job Object.

The host owns terminal I/O; only the restricted child receives the console
attribute. No unrestricted shell or pipe-only terminal fallback is available.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
from pathlib import Path
import os
import subprocess
import threading
import time

from termx.sandbox import _win_shim as win

kernel = win._kernel32
kernel.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID,
    wt.BOOL, wt.DWORD, wt.LPVOID, wt.LPCWSTR, wt.LPVOID, wt.LPVOID]
kernel.CreateProcessW.restype = wt.BOOL
kernel.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
kernel.IsProcessInJob.restype = wt.BOOL
win._advapi32.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
win._advapi32.ConvertSidToStringSidW.restype = wt.BOOL


class _COORD(ctypes.Structure):
    _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]


class _STARTUPINFOEX(ctypes.Structure):
    _fields_ = [('StartupInfo', win._STARTUPINFO), ('lpAttributeList', wt.LPVOID)]


kernel.CreatePseudoConsole.argtypes = [_COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
kernel.CreatePseudoConsole.restype = ctypes.c_long
kernel.ResizePseudoConsole.argtypes = [wt.HANDLE, _COORD]
kernel.ResizePseudoConsole.restype = ctypes.c_long
kernel.ClosePseudoConsole.argtypes = [wt.HANDLE]
kernel.ClosePseudoConsole.restype = None
kernel.InitializeProcThreadAttributeList.argtypes = [wt.LPVOID, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
kernel.InitializeProcThreadAttributeList.restype = wt.BOOL
kernel.UpdateProcThreadAttribute.argtypes = [wt.LPVOID, wt.DWORD, ctypes.c_size_t, wt.LPVOID, ctypes.c_size_t, wt.LPVOID, wt.LPVOID]
kernel.UpdateProcThreadAttribute.restype = wt.BOOL
kernel.DeleteProcThreadAttributeList.argtypes = [wt.LPVOID]
kernel.DeleteProcThreadAttributeList.restype = None


def _check(ok):
    if not ok: raise win._last_error()


def _size(rows, cols):
    return _COORD(max(1, min(int(cols), 32767)), max(1, min(int(rows), 32767)))


class RestrictedConPTY:
    master_fd = -1

    def __init__(self, *, command, cwd, env, job_limits, user_mode, rows, cols, release):
        self._handles = []
        self._console = None
        self._job = None
        self._process = None
        self._broker = None
        self._returncode = None
        self._closing = threading.Event()
        self._done = threading.Event()
        self._output = queue.Queue(maxsize=512)
        self._state = threading.RLock()
        self._release = release
        self.proc = self
        self.pid = None
        try:
            self._launch(command, cwd, env, job_limits, user_mode, rows, cols)
        except BaseException:
            reaped = True
            if self._job: kernel.TerminateJobObject(self._job, 1)
            for process in (self._process, self._broker):
                if process:
                    kernel.TerminateProcess(process, 1)
                    reaped = (kernel.WaitForSingleObject(process, 5000) == 0) and reaped
            if self._job: reaped = reaped and win._job_process_count(self._job) == 0
            self._closing.set()
            self._close_handles()
            if reaped: release()
            raise
        self._reader = threading.Thread(target=self._pump, name=f'conpty-output-{self.pid}', daemon=True)
        self._watcher = threading.Thread(target=self._watch, name=f'conpty-job-{self.pid}', daemon=True)
        self._reader.start(); self._watcher.start()

    def _owned(self, handle):
        self._handles.append(handle)
        return handle

    def _launch(self, command, cwd, env, limits, user_mode, rows, cols):
        token = None
        if user_mode:
            credentials = win.sandbox_user_credentials()
            if credentials is None: raise PermissionError('Restricted terminal identity is unavailable')
            base = win.logon_user_token(credentials)
            try: token = win.build_restricted_token(base, keep_traverse_privilege=True)
            finally: kernel.CloseHandle(base)
        else:
            token = win.build_restricted_token()
        attributes = None
        initialized = False
        try:
            # Secondary Logon cannot carry the extended ConPTY attributes.
            # Start a restricted parent suspended, then let the documented
            # PARENT_PROCESS attribute inherit its token and Job Object. The
            # parent is never resumed; no unrestricted process executes.
            plain = win._STARTUPINFO(); plain.cb = ctypes.sizeof(plain)
            parent = win._PROCESS_INFORMATION()
            broker_executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe')
            broker_command = subprocess.list2cmdline([broker_executable, '/d', '/c', 'exit', '0'])
            broker_flags = win._CREATE_SUSPENDED | win._CREATE_UNICODE_ENVIRONMENT | 0x08000000
            environment = win._env_block(env)
            argv = ctypes.create_unicode_buffer(broker_command)
            if not win._advapi32.CreateProcessWithTokenW(token, 0, broker_executable, argv, broker_flags,
                environment, cwd, ctypes.byref(plain), ctypes.byref(parent)):
                first_error = win._last_error()
                argv = ctypes.create_unicode_buffer(broker_command)
                if not win._advapi32.CreateProcessAsUserW(token, broker_executable, argv, None, None, False,
                    broker_flags, environment, cwd, ctypes.byref(plain), ctypes.byref(parent)):
                    raise OSError(f'Restricted console parent launch failed: {first_error}; {win._last_error()}')
            self._broker = self._owned(parent.hProcess)
            parent_thread = self._owned(parent.hThread)
            self._prepare_job(limits)
            _check(kernel.AssignProcessToJobObject(self._job, self._broker))
            kernel.CloseHandle(parent_thread); self._handles.remove(parent_thread)
            input_read, self._input = win._inheritable_pipe()
            self._owned(input_read); self._owned(self._input)
            self._output_read, output_write = win._inheritable_pipe()
            self._owned(self._output_read); self._owned(output_write)
            for handle in self._handles:
                _check(kernel.SetHandleInformation(handle, win._HANDLE_FLAG_INHERIT, 0))
            console = wt.HANDLE()
            result = kernel.CreatePseudoConsole(_size(rows, cols), input_read, output_write, 0, ctypes.byref(console))
            if result < 0: raise OSError(f'CreatePseudoConsole failed ({result})')
            self._console = console
            # Conhost has duplicated its endpoints. Keep only host write/read.
            for handle in (input_read, output_write):
                kernel.CloseHandle(handle); self._handles.remove(handle)
            length = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(length))
            _check(length.value > 0)
            attributes = ctypes.create_string_buffer(length.value)
            _check(kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(length)))
            initialized = True
            _check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020016,
                console, ctypes.sizeof(wt.HANDLE), None, None))
            parent_handle = wt.HANDLE(self._broker)
            _check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020000,
                ctypes.byref(parent_handle), ctypes.sizeof(parent_handle), None, None))
            startup = _STARTUPINFOEX(); startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.cast(attributes, wt.LPVOID)
            process = win._PROCESS_INFORMATION()
            flags = win._CREATE_SUSPENDED | win._CREATE_UNICODE_ENVIRONMENT | 0x00080000
            argv = ctypes.create_unicode_buffer(command)
            _check(kernel.CreateProcessW(None, argv, None, None, False, flags,
                environment, cwd, ctypes.byref(startup), ctypes.byref(process)))
            self._process = self._owned(process.hProcess)
            thread = self._owned(process.hThread); self.pid = process.dwProcessId
            self._verify_child_token(token)
            member = wt.BOOL()
            _check(kernel.IsProcessInJob(self._process, self._job, ctypes.byref(member)))
            if not member.value: raise PermissionError('Restricted console did not inherit its Job Object')
            if kernel.ResumeThread(thread) == 0xffffffff: raise win._last_error()
            kernel.CloseHandle(thread); self._handles.remove(thread)
        finally:
            if initialized: kernel.DeleteProcThreadAttributeList(attributes)
            if token: kernel.CloseHandle(token)

    def _prepare_job(self, limits):
        self._job = self._owned(kernel.CreateJobObjectW(None, None)); _check(self._job)
        info = win._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = win._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limits.get('active_process_limit'):
            info.BasicLimitInformation.LimitFlags |= win._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = int(limits['active_process_limit'])
        if limits.get('process_memory_bytes'):
            info.BasicLimitInformation.LimitFlags |= win._JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(limits['process_memory_bytes'])
        if limits.get('job_memory_bytes'):
            info.BasicLimitInformation.LimitFlags |= win._JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(limits['job_memory_bytes'])
        if limits.get('job_time_100ns'):
            info.BasicLimitInformation.LimitFlags |= win._JOB_OBJECT_LIMIT_JOB_TIME
            info.BasicLimitInformation.PerJobUserTimeLimit = int(limits['job_time_100ns'])
        _check(kernel.SetInformationJobObject(self._job, win._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info)))

    def _verify_child_token(self, expected):
        child = wt.HANDLE()
        _check(win._advapi32.OpenProcessToken(self._process, 8, ctypes.byref(child)))
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

    def _pump(self):
        buffer, count = ctypes.create_string_buffer(8192), wt.DWORD()
        try:
            while kernel.ReadFile(self._output_read, buffer, len(buffer), ctypes.byref(count), None) and count.value:
                data = buffer.raw[:count.value]
                while not self._closing.is_set():
                    try: self._output.put(data, timeout=.1); break
                    except queue.Full: continue
        finally:
            # Always drain conhost output while ClosePseudoConsole is waiting;
            # bounded backpressure must never strand the job shutdown thread.
            while not self._closing.is_set():
                try: self._output.put(b'', timeout=.1); break
                except queue.Full: continue

    def _watch(self):
        safe_to_release = False
        try:
            kernel.WaitForSingleObject(self._process, win._INFINITE)
            code = wt.DWORD(); _check(kernel.GetExitCodeProcess(self._process, ctypes.byref(code)))
            self._returncode = code.value
            # A shell leader's exit never releases roots while descendants
            # retain the lower-integrity write grant.
            _check(kernel.TerminateJobObject(self._job, code.value))
            deadline = time.monotonic() + 15
            while win._job_process_count(self._job) != 0:
                if time.monotonic() >= deadline:
                    # Preserve the filesystem references if Windows cannot
                    # prove all writers have stopped; never restore early.
                    raise OSError('Restricted terminal descendants have not exited')
                time.sleep(.05)
            safe_to_release = True
            with self._state:
                console, self._console = self._console, None
            if console: kernel.ClosePseudoConsole(console)
            self._reader.join(timeout=5)
        finally:
            self._close_handles()
            try:
                if safe_to_release: self._release()
            finally: self._done.set()

    def _close_handles(self):
        with self._state:
            if self._job: kernel.TerminateJobObject(self._job, 1)
            if self._console:
                kernel.ClosePseudoConsole(self._console); self._console = None
            for handle in reversed(self._handles):
                if handle: kernel.CloseHandle(handle)
            self._handles.clear(); self._job = None

    def read(self, timeout=.25):
        try: return self._output.get(timeout=timeout)
        except queue.Empty: return b'' if self._done.is_set() else None

    def write(self, data):
        if self._closing.is_set() or not data: return
        with self._state:
            if self._job is None: return
            remaining = bytes(data)
            while remaining:
                count = wt.DWORD()
                _check(kernel.WriteFile(self._input, remaining, len(remaining), ctypes.byref(count), None))
                if not count.value: raise OSError('Restricted console input pipe stopped accepting data')
                remaining = remaining[count.value:]

    def resize(self, rows, cols):
        with self._state:
            if self._console:
                result = kernel.ResizePseudoConsole(self._console, _size(rows, cols))
                if result < 0: raise OSError(f'ResizePseudoConsole failed ({result})')

    def alive(self): return self._returncode is None and not self._done.is_set()
    def exit_code(self): return self._returncode
    def poll(self): return self.exit_code()
    def wait(self, timeout=None):
        if not self._done.wait(timeout): raise TimeoutError('Restricted terminal has not exited')
        return self._returncode

    def send_signal(self, name):
        if not self.alive(): return False
        if name.lower() == 'int': self.write(b'\x03'); return True
        if name.lower() in {'term', 'hup', 'kill'}: self.kill(); return True
        return False

    def kill(self, timeout=1.5):
        self._closing.set()
        with self._state:
            if self._job: _check(kernel.TerminateJobObject(self._job, 1))
        self._done.wait(timeout)
