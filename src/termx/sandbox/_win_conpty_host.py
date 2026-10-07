"""Host transport to a genuine restricted-token console broker.

The plain token launch avoids passing console references between parent handle
contexts. The host owns one kill-on-close job covering broker, console server,
shell and descendants; roots remain granted until that complete job is empty.
"""
from __future__ import annotations
import base64
import ctypes
import ctypes.wintypes as wt
import queue
import subprocess
import sys
import threading
import time
from termx.sandbox import _win_shim as win
from termx.sandbox._win_conpty_protocol import Decoder, encode, validate_response
from termx.sandbox._win_conpty_security import verify_child_token

kernel = win._kernel32

def check(value):
    if not value: raise win._last_error()

class RestrictedConPTY:
    master_fd = -1
    def __init__(self, *, command, cwd, env, job_limits, user_mode, rows, cols, release):
        self._handles=[];self._job=None;self._process=None;self._returncode=None
        self._closing=threading.Event();self._done=threading.Event();self._ready=threading.Event()
        self._output=queue.Queue(maxsize=512);self._control=queue.Queue(maxsize=64)
        self._state=threading.RLock();self._release=release;self._failure=None
        self._reader_error=None;self._resume_count=None;self._console_state={};self._stderr=bytearray();self._phase='launching-broker'
        self.proc=self;self.pid=None
        try:self._launch(cwd,env,job_limits,user_mode)
        except BaseException:
            reaped=self._process is None
            if self._job:kernel.TerminateJobObject(self._job,1)
            if self._process:
                kernel.TerminateProcess(self._process,1)
                reaped=kernel.WaitForSingleObject(self._process,5000)==0
            if self._job:reaped=reaped and win._job_process_count(self._job)==0
            self._close_handles()
            if reaped:release()
            raise
        self._threads=[threading.Thread(target=fn,name=name,daemon=True) for fn,name in
            [(self._pump,'restricted-console-output'),(self._send,'restricted-console-input'),
             (self._read_stderr,'restricted-console-errors')]]
        for thread in self._threads:thread.start()
        self._watcher=threading.Thread(target=self._watch,name='restricted-console-job',daemon=True);self._watcher.start()
        try:
            self._enqueue({'type':'start','command':command,'cwd':cwd,'env':env,
                'job':job_limits,'rows':rows,'cols':cols})
            if not self._ready.wait(30):raise OSError('Restricted console initialization deadline exceeded: '+self._phase)
            if self._failure or self._returncode is not None or not self.pid:
                raise OSError('Restricted console initialization failed: '+str(self._failure or 'broker exited')+'; '+str(self.diagnostics()))
        except BaseException:
            self.kill(15)
            raise

    def _owned(self,handle):self._handles.append(handle);return handle
    def _launch(self,cwd,env,limits,user_mode):
        from termx.sandbox._win_conpty import LocalConPTY, _STARTUPINFOEX
        if user_mode:
            credentials=win.sandbox_user_credentials()
            if credentials is None:raise PermissionError('Restricted terminal identity unavailable')
            base=win.logon_user_token(credentials)
            try:token=win.build_restricted_token(base,keep_traverse_privilege=True)
            finally:kernel.CloseHandle(base)
        else:token=win.build_restricted_token()
        try:
            input_read,self._input=win._inheritable_pipe();self._owned(input_read);self._owned(self._input)
            self._output_read,output_write=win._inheritable_pipe();self._owned(self._output_read);self._owned(output_write)
            self._stderr_read,error_write=win._inheritable_pipe();self._owned(self._stderr_read);self._owned(error_write)
            for handle in (self._input,self._output_read,self._stderr_read):check(kernel.SetHandleInformation(handle,win._HANDLE_FLAG_INHERIT,0))
            startup=win._STARTUPINFO();startup.cb=ctypes.sizeof(startup)
            startup.dwFlags=win._STARTF_USESTDHANDLES;startup.hStdInput=input_read;startup.hStdOutput=output_write;startup.hStdError=error_write
            process=win._PROCESS_INFORMATION()
            argv=([sys.executable,'--runtime-module','termx.sandbox._win_conpty_worker']
                if getattr(sys,'frozen',False) else [sys.executable,'-I','-m','termx.sandbox._win_conpty_worker'])
            command=subprocess.list2cmdline(argv);mutable=ctypes.create_unicode_buffer(command)
            environment=win._env_block(env)
            flags=win._CREATE_SUSPENDED|win._CREATE_UNICODE_ENVIRONMENT|0x08000000
            if not win._advapi32.CreateProcessWithTokenW(token,0,sys.executable,mutable,flags,
                environment,cwd,ctypes.byref(startup),ctypes.byref(process)):
                first=win._last_error();mutable=ctypes.create_unicode_buffer(command)
                # Only these three pipe ends may inherit on the API path
                # requiring bInheritHandles; concurrent daemon handles stay out.
                length=ctypes.c_size_t()
                kernel.InitializeProcThreadAttributeList(None,1,0,ctypes.byref(length))
                check(length.value > 0)
                attributes=ctypes.create_string_buffer(length.value)
                check(kernel.InitializeProcThreadAttributeList(attributes,1,0,ctypes.byref(length)))
                try:
                    handles=(wt.HANDLE*3)(input_read,output_write,error_write)
                    check(kernel.UpdateProcThreadAttribute(attributes,0,0x00020002,
                        handles,ctypes.sizeof(handles),None,None))
                    extended=_STARTUPINFOEX();extended.StartupInfo=startup
                    extended.StartupInfo.cb=ctypes.sizeof(extended)
                    extended.lpAttributeList=ctypes.cast(attributes,wt.LPVOID)
                    if not win._advapi32.CreateProcessAsUserW(token,sys.executable,mutable,None,None,True,
                        flags|0x00080000,environment,cwd,ctypes.byref(extended),ctypes.byref(process)):
                        raise OSError(f'Restricted console broker launch failed: {first}; {win._last_error()}')
                finally:kernel.DeleteProcThreadAttributeList(attributes)
            self._process=self._owned(process.hProcess);thread=self._owned(process.hThread)
            # Reuse the same complete Job Object limit configuration as the
            # local console. No code runs until both token and job are verified.
            LocalConPTY._prepare_job(self,limits)
            check(kernel.AssignProcessToJobObject(self._job,self._process))
            member=wt.BOOL()
            check(kernel.IsProcessInJob(self._process,self._job,ctypes.byref(member)))
            if not member.value:raise PermissionError('Restricted broker is outside its job')
            verify_child_token(self._process,token)
            for handle in (input_read,output_write,error_write):kernel.CloseHandle(handle);self._handles.remove(handle)
            self._resume_count=kernel.ResumeThread(thread)
            if self._resume_count==0xffffffff:raise win._last_error()
            kernel.CloseHandle(thread);self._handles.remove(thread)
        finally:kernel.CloseHandle(token)

    def _enqueue(self,value):
        if self._closing.is_set():return
        try:self._control.put_nowait(encode(value))
        except queue.Full:raise OSError('Restricted terminal input is busy') from None
    def _send(self):
        while not self._closing.is_set():
            try:data=self._control.get(timeout=.1)
            except queue.Empty:continue
            try:
                while data and not self._closing.is_set():
                    count=wt.DWORD();check(kernel.WriteFile(self._input,data,len(data),ctypes.byref(count),None))
                    if not count.value:raise OSError('Restricted console input closed')
                    data=data[count.value:]
            except OSError:return
    def _publish(self,data):
        while not self._closing.is_set():
            try:self._output.put(data,timeout=.1);return
            except queue.Full:continue
    def _pump(self):
        decoder=Decoder();buffer=ctypes.create_string_buffer(8192);count=wt.DWORD()
        ready=False
        try:
            while kernel.ReadFile(self._output_read,buffer,len(buffer),ctypes.byref(count),None) and count.value:
                for value in decoder.feed(buffer.raw[:count.value]):
                    data=validate_response(value,ready)
                    kind=value.get('type')
                    if kind=='ready':ready=True;self.pid=value['pid'];self._ready.set()
                    elif kind=='phase':self._phase=str(value['stage'])
                    elif kind=='data':self._publish(data)
                    elif kind=='exit':self._returncode=int(value['code']);self._console_state=value.get('diagnostics') or {}
                    elif kind=='error':self._failure=str(value.get('error') or 'broker failure')+'; '+str(value.get('category'))+'; '+str(value.get('winerror'));self._ready.set()
                    else:raise ValueError('Unknown restricted console response')
        except (ValueError,KeyError,OSError) as error:self._failure=str(error)
        finally:
            self._reader_error=ctypes.get_last_error();self._ready.set();self._publish(b'')
    def _read_stderr(self):
        buffer=ctypes.create_string_buffer(4096);count=wt.DWORD()
        while kernel.ReadFile(self._stderr_read,buffer,len(buffer),ctypes.byref(count),None) and count.value:
            if len(self._stderr)<32768:self._stderr.extend(buffer.raw[:min(count.value,32768-len(self._stderr))])
    def _watch(self):
        safe=False
        try:
            kernel.WaitForSingleObject(self._process,win._INFINITE)
            code=wt.DWORD();check(kernel.GetExitCodeProcess(self._process,ctypes.byref(code)))
            if self._returncode is None:self._returncode=code.value
            check(kernel.TerminateJobObject(self._job,code.value));deadline=time.monotonic()+15
            while win._job_process_count(self._job)!=0:
                if time.monotonic()>deadline:raise OSError('Restricted console descendants have not exited')
                time.sleep(.05)
            safe=True;self._ready.set()
            for thread in self._threads:
                if thread.name!='restricted-console-input':thread.join(timeout=2)
        finally:
            self._closing.set();self._close_handles()
            try:
                if safe:self._release()
            finally:self._done.set();self._ready.set()
    def _close_handles(self):
        with self._state:
            if self._job:kernel.TerminateJobObject(self._job,1)
            for handle in reversed(self._handles):
                if handle:kernel.CloseHandle(handle)
            self._handles.clear();self._job=None
    def read(self,timeout=.25):
        try:return self._output.get(timeout=timeout)
        except queue.Empty:return b'' if self._done.is_set() else None
    def write(self,data):
        for offset in range(0,len(data),16384):self._enqueue({'type':'input','data':base64.b64encode(data[offset:offset+16384]).decode()})
    def resize(self,rows,cols):self._enqueue({'type':'resize','rows':max(1,min(int(rows),32767)),'cols':max(1,min(int(cols),32767))})
    def send_signal(self,name):
        if not self.alive():return False
        if name.lower()=='int':self._enqueue({'type':'signal','name':'int'});return True
        if name.lower() in {'kill','term','hup'}:self.kill();return True
        return False
    def alive(self):return self._returncode is None and not self._done.is_set()
    def exit_code(self):return self._returncode
    def poll(self):return self.exit_code()
    def wait(self,timeout=None):
        if not self._done.wait(timeout):raise TimeoutError('Restricted terminal has not exited')
        return self._returncode
    def kill(self,timeout=1.5):
        self._closing.set()
        with self._state:
            if self._job:check(kernel.TerminateJobObject(self._job,1))
        self._done.wait(timeout)
    def diagnostics(self):
        with self._state:return {'exit_code':self._returncode,'alive':self.alive(),
            'reader_error':self._reader_error,'resume_count':self._resume_count,
            'job_active':win._job_process_count(self._job) if self._job else 0,
            'console':self._console_state,'broker_error':self._failure,
            'broker_phase':self._phase,
            'broker_stderr':self._stderr.decode('utf-8',errors='replace')}
