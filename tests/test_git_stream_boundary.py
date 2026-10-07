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
        assert process.stdout.read()==b'metadata-ready\n'
    finally:
        if process.poll() is None:process.kill()
        process.wait()
        process.stdin.close()
