"""Actual owned daemon shutdown; never signals an existing user host."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep

import httpx
import pytest


@pytest.mark.parametrize('desktop', [False, True])
def test_explicit_host_stop_has_intentional_desktop_exit_status(tmp_path, desktop):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, TERMX_CONFIG_DIR=str(tmp_path/'config'),
                       TERMX_AGENTS_DIR=str(tmp_path/'agents'),
                       TERMX_ENGINE_STARTUP_REFRESH='0', TERMX_DESKTOP='0', PYTHONUNBUFFERED='1')
    command = [sys.executable, '-m', 'termx', '--host', '127.0.0.1', '--port', str(port),
               '--passcode', 'owned-host-stop-fixture', '--web-dir', str(tmp_path/'web')]
    if desktop:
        command.append('--desktop')
    # Windows pipes have a small buffer: the non-desktop QR/banner can fill it
    # before the client request is served. Files retain all diagnostics without
    # making application readiness depend on an undrained test pipe.
    stdout_path, stderr_path = tmp_path/'stdout.log', tmp_path/'stderr.log'
    with stdout_path.open('w', encoding='utf-8') as stdout_log, stderr_path.open('w', encoding='utf-8') as stderr_log:
        process = subprocess.Popen(command, env=environment, cwd=Path(__file__).resolve().parents[1],
                                   stdout=stdout_log, stderr=stderr_log, text=True)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False,
                              headers={'Origin':f'http://127.0.0.1:{port}'}) as client:
                deadline = monotonic()+35
                while True:
                    assert process.poll() is None, stderr_path.read_text(encoding='utf-8')
                    try:
                        if client.get('/auth/methods').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert monotonic() < deadline, ('Owned fixture daemon did not become ready\n'
                                                   + stderr_path.read_text(encoding='utf-8'))
                    sleep(.1)
                login = client.post('/auth/setup', headers={'X-Termx-Passcode':'owned-host-stop-fixture'},
                                    json={'username':'owned-stop-owner','password':'owned-stop-password-123',
                                          'transport':'bearer','device_name':'Owned test'})
                assert login.status_code == 200, login.text
                client.headers['Authorization'] = 'Bearer '+login.json()['access_token']
                host = client.get('/auth/me').json()['host_id']
                refused = client.post('/auth/host/stop', json={'host_id':'another-host','acknowledge':True})
                assert refused.status_code == 400
                assert process.poll() is None
                assert client.get('/auth/me').status_code == 200
                accepted = client.post('/auth/host/stop', json={'host_id':host,'acknowledge':True})
                assert accepted.status_code == 200 and accepted.json()['accepted']
            process.communicate(timeout=25)
            stdout = stdout_path.read_text(encoding='utf-8')
            stderr = stderr_path.read_text(encoding='utf-8')
            assert process.returncode == (79 if desktop else 0), stderr
            events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
            assert [row for row in events if row.get('termx') == 'stopping'] == (
                [{'termx':'stopping','reason':'host-stop'}] if desktop else [])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
