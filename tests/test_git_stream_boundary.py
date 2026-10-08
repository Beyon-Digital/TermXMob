"""Actual subprocess boundary: Git metadata must never inherit the agent RPC pipe."""
import subprocess
import sys


def test_git_probe_cannot_read_agent_protocol_stdin():
    # The parent intentionally retains its pipe. A substituted Git child that
    # reads stdin until EOF exposes inheritance without relying on Git timing.
    source = r'''
import subprocess,sys
from termx import git_ops
original=subprocess.run
def git_child(argv,**options):
    assert argv[0]=='git'
    return original([sys.executable,'-c',"import sys; data=sys.stdin.buffer.read(); assert not data; print('true')"],**options)
git_ops.subprocess.run=git_child
git_ops.shutil.which=lambda _:sys.executable
assert git_ops.is_repo('.')
print('metadata-ready',flush=True)
'''
    process=subprocess.Popen([sys.executable,'-c',source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.wait(timeout=5)
        assert process.returncode==0,process.stderr.read().decode('utf-8','replace')
        assert process.stdout.read().splitlines()==[b'metadata-ready']
    finally:
        if process.poll() is None:process.kill()
        process.wait()
        process.stdin.close()


def test_noninteractive_shell_cannot_read_agent_protocol_stdin(tmp_path):
    import os
    import shlex
    argv=[sys.executable,'-c',"import sys; assert sys.stdin.buffer.read()==b''; print('shell-eof')"]
    command=subprocess.list2cmdline(argv) if os.name=='nt' else ' '.join(shlex.quote(value) for value in argv)
    source='''
import asyncio,sys
from termx.agent.execution import run_shell
from termx.sandbox.host import HostSandboxRunner
async def run():
    result=await run_shell(sys.argv[1],sys.argv[2],timeout_s=3,runner=HostSandboxRunner(),profile='host')
    assert result.exit_code==0 and result.output.splitlines()==['shell-eof'], result
    print('shell-ready',flush=True)
asyncio.run(run())
'''
    process=subprocess.Popen([sys.executable,'-c',source,command,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.wait(timeout=8)
        assert process.returncode==0,process.stderr.read().decode('utf-8','replace')
        assert process.stdout.read().splitlines()==[b'shell-ready']
    finally:
        if process.poll() is None:process.kill()
        process.wait();process.stdin.close()


def test_noninteractive_mutating_git_cannot_read_agent_protocol_stdin(tmp_path):
    source='''
import asyncio,sys
from dataclasses import replace
from types import SimpleNamespace
from termx.agent.providers import ProviderCall
from termx.agent.tools.git import _run_mutating
from termx.sandbox.host import HostSandboxRunner
class GitChild:
    async def spawn(self,spec):
        return await HostSandboxRunner().spawn(replace(spec,argv=(sys.executable,'-c',"import sys; assert sys.stdin.buffer.read()==b''; print('git-eof')")))
async def run():
    ctx=SimpleNamespace(task={},cwd=sys.argv[1],cancel=asyncio.Event(),sandbox_runner=lambda _:GitChild())
    await _run_mutating(ProviderCall(type='function',call_id='git',name='git_commit',arguments={}),ctx,'commit',timeout=3)
    print('git-ready',flush=True)
asyncio.run(run())
'''
    process=subprocess.Popen([sys.executable,'-c',source,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.wait(timeout=8)
        assert process.returncode==0,process.stderr.read().decode('utf-8','replace')
        assert process.stdout.read().splitlines()==[b'git-ready']
    finally:
        if process.poll() is None:process.kill()
        process.wait();process.stdin.close()
