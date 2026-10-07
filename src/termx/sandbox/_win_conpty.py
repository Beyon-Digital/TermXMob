"""Real ConPTY attached to a restricted token and a kill-on-close Job Object.

The host owns terminal I/O; only the restricted child receives the console
attribute. No unrestricted shell or pipe-only terminal fallback is available.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
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


class LocalConPTY:
    master_fd = -1

    def __init__(self, *, command, cwd, env, job_limits, user_mode, rows, cols, release, progress=None):
        self._handles = []
        self._console = None
        self._job = None
        self._process = None
        self._progress = progress or (lambda stage: None)
        self._returncode = None
        self._reader_error = None
        self._resume_count = None
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
            for process in (self._process,):
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
        token = wt.HANDLE()
        _check(win._advapi32.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
        attributes = None
        initialized = False
        try:
            from termx.sandbox._win_conpty_security import verify_child_token
            verify_child_token(kernel.GetCurrentProcess(), token)
            self._progress('restricted-token-verified')
            self._prepare_job(limits)
            input_read, self._input = win._inheritable_pipe()
            self._owned(input_read); self._owned(self._input)
            self._output_read, output_write = win._inheritable_pipe()
            self._owned(self._output_read); self._owned(output_write)
            for handle in self._handles:
                _check(kernel.SetHandleInformation(handle, win._HANDLE_FLAG_INHERIT, 0))
            console = wt.HANDLE()
            self._progress('creating-console')
            result = kernel.CreatePseudoConsole(_size(rows, cols), input_read, output_write, 0, ctypes.byref(console))
            if result < 0: raise OSError(f'CreatePseudoConsole failed ({result})')
            self._console = console
            self._progress('console-created')
            # Conhost has duplicated its endpoints. Keep only host write/read.
            for handle in (input_read, output_write):
                kernel.CloseHandle(handle); self._handles.remove(handle)
            length = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(length))
            _check(length.value > 0)
            attributes = ctypes.create_string_buffer(length.value)
            _check(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(length)))
            initialized = True
            _check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020016,
                console, ctypes.sizeof(wt.HANDLE), None, None))
            startup = _STARTUPINFOEX(); startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.cast(attributes, wt.LPVOID)
            process = win._PROCESS_INFORMATION()
            flags = win._CREATE_SUSPENDED | win._CREATE_UNICODE_ENVIRONMENT | 0x00080000
            argv = ctypes.create_unicode_buffer(command)
            environment = win._env_block(env)
            self._progress('creating-client')
            _check(kernel.CreateProcessW(None, argv, None, None, False, flags,
                environment, cwd, ctypes.byref(startup), ctypes.byref(process)))
            self._process = self._owned(process.hProcess)
            thread = self._owned(process.hThread); self.pid = process.dwProcessId
            self._verify_child_token(token)
            _check(kernel.AssignProcessToJobObject(self._job, self._process))
            member = wt.BOOL()
            _check(kernel.IsProcessInJob(self._process, self._job, ctypes.byref(member)))
            if not member.value: raise PermissionError('Restricted console client is outside its job')
            self._resume_count = kernel.ResumeThread(thread)
            if self._resume_count == 0xffffffff: raise win._last_error()
            self._progress('client-resumed')
            kernel.CloseHandle(thread); self._handles.remove(thread)
        finally:
            if initialized: kernel.DeleteProcThreadAttributeList(attributes)
            kernel.CloseHandle(token)

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
        from termx.sandbox._win_conpty_security import verify_child_token
        verify_child_token(self._process, expected)

    def _pump(self):
        buffer, count = ctypes.create_string_buffer(8192), wt.DWORD()
        try:
            while True:
                if not kernel.ReadFile(self._output_read, buffer, len(buffer), ctypes.byref(count), None):
                    self._reader_error = ctypes.get_last_error()
                    break
                if not count.value: break
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
    def diagnostics(self):
        with self._state:
            return {'exit_code':self._returncode,'alive':self.alive(),
                'reader_error':self._reader_error,'resume_count':self._resume_count,
                'job_active':win._job_process_count(self._job) if self._job else 0}
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

# The public host facade runs this console implementation in an already
# restricted broker process, never in the daemon primary-token context.
from termx.sandbox._win_conpty_host import RestrictedConPTY
