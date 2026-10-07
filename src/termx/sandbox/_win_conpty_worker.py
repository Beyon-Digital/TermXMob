"""Fixed restricted console broker. Never runs a console at host integrity."""
from __future__ import annotations
import base64
import ctypes
import ctypes.wintypes as wt
import os
import threading
from termx.sandbox._win_conpty_protocol import Decoder, encode, validate_start


def main():
    import msvcrt
    from termx.sandbox import _win_shim as win
    from termx.sandbox._win_conpty import LocalConPTY
    from termx.sandbox._win_conpty_security import verify_child_token
    # Refuse ordinary host invocation before any shell/conhost effect.
    token=wt.HANDLE()
    if not win._advapi32.OpenProcessToken(win._kernel32.GetCurrentProcess(),8,ctypes.byref(token)):
        raise win._last_error()
    try:verify_child_token(win._kernel32.GetCurrentProcess(),token)
    finally:win._kernel32.CloseHandle(token)
    msvcrt.setmode(0,os.O_BINARY);msvcrt.setmode(1,os.O_BINARY)
    lock=threading.Lock();decoder=Decoder();terminal=None
    def publish(value):
        data=encode(value)
        with lock:
            while data:
                written=os.write(1,data)
                if written<=0:raise OSError('Console broker output closed')
                data=data[written:]
    try:
        values=[]
        while not values:
            data=os.read(0,8192)
            if not data:raise ValueError('Console broker start missing')
            values=decoder.feed(data)
        config=validate_start(values.pop(0))
        terminal=LocalConPTY(command=config['command'],cwd=config['cwd'],env=config['env'],
            job_limits=config['job'],user_mode=False,rows=config['rows'],cols=config['cols'],release=lambda:None,
            progress=lambda stage:publish({'type':'phase','stage':stage}))
        publish({'type':'ready','pid':terminal.pid})
        def controls():
            try:
                pending=values
                while terminal.alive():
                    for value in pending:
                        kind=value.get('type')
                        if kind=='input':
                            raw=value.get('data')
                            if not isinstance(raw,str) or len(raw)>22000:raise ValueError('Invalid console input')
                            data=base64.b64decode(raw,validate=True)
                            if len(data)>16384:raise ValueError('Console input exceeds limit')
                            terminal.write(data)
                        elif kind=='resize':
                            rows,cols=value.get('rows'),value.get('cols')
                            if type(rows) is not int or type(cols) is not int or not(1<=rows<=32767 and 1<=cols<=32767):
                                raise ValueError('Invalid console dimensions')
                            terminal.resize(rows,cols)
                        elif kind=='signal' and value.get('name')=='int':terminal.send_signal('int')
                        else:raise ValueError('Unknown console control')
                    data=os.read(0,8192)
                    if not data:break
                    pending=decoder.feed(data)
            except (OSError,ValueError,KeyError):pass
            finally:terminal.kill(15)
        threading.Thread(target=controls,name='console-broker-controls',daemon=True).start()
        while True:
            data=terminal.read(.25)
            if data is None:continue
            if data==b'':break
            publish({'type':'data','data':base64.b64encode(data).decode('ascii')})
        code=terminal.wait(15)
        publish({'type':'exit','code':code,'diagnostics':terminal.diagnostics()})
        # A raw daemon os.read never participates in Python buffered-I/O locks.
        # The outer host job owns the broker, conhost and all descendants.
        os._exit(int(code)&255)
    except BaseException as error:
        if terminal:terminal.kill(15)
        try:publish({'type':'error','error':'Restricted console broker failed',
            'category':type(error).__name__,'winerror':getattr(error,'winerror',None)})
        except OSError:pass
        os._exit(1)


if __name__=='__main__':main()
