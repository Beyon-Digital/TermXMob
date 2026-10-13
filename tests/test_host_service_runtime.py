"""Actual CLI workers in private fixture profiles; no OS service registration."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic,sleep
from types import SimpleNamespace

import httpx

from termx.host_service import HostService


def fixture_service(tmp_path,monkeypatch,port):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'fixture-user-config'))
    controller=HostService(tmp_path/'profile',home=tmp_path/'fixture-home',
        run=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='',stderr=''))
    controller.install(port=port,bootstrap_passcode='fixture-service-bootstrap',web_dir=str(tmp_path/'web'))
    environment={**os.environ,'TERMX_ENGINE_STARTUP_REFRESH':'0','TERMX_AGENTS_DIR':str(tmp_path/'agents'),
        'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),'TERMX_DESKTOP':'0'}
    command=[sys.executable,'-m','termx','--service-worker','--config-dir',str(controller.root)]
    return controller,environment,command


def test_real_service_worker_proof_duplicate_exclusion_and_intentional_stop(tmp_path,monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    controller,environment,command=fixture_service(tmp_path,monkeypatch,port)
    with (tmp_path/'stdout.log').open('w') as stdout,(tmp_path/'stderr.log').open('w') as stderr:
        process=subprocess.Popen(command,env=environment,stdout=stdout,stderr=stderr)
        try:
            deadline=monotonic()+25
            while not controller.status()['ready']:
                assert process.poll() is None,(tmp_path/'stderr.log').read_text()
                assert monotonic()<deadline,controller.status()
                sleep(.1)
            status=controller.status()
            assert status['managed'] and status['pid']==process.pid and status['port']==port
            assert 'proof_token' not in json.dumps(status) and 'fixture-service-bootstrap' not in json.dumps(status)
            duplicate=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=15)
            assert duplicate.returncode==73 and 'already owns this profile' in duplicate.stderr
            assert controller.status()['instance_id']==status['instance_id']
            with httpx.Client(base_url=status['url'],trust_env=False,timeout=5) as client:
                assert client.get('/api/host/runtime').status_code==403
                login=client.post('/auth/setup',headers={'Origin':status['url'],'X-Termx-Passcode':'fixture-service-bootstrap'},
                    json={'username':'fixture-owner','password':'fixture-service-password-123','transport':'bearer'})
                assert login.status_code==200,login.text
                client.headers['Authorization']='Bearer '+login.json()['access_token']
                host=client.get('/auth/me').json()['host_id']
                stopped=client.post('/auth/host/stop',json={'host_id':host,'acknowledge':True})
                assert stopped.status_code==200,stopped.text
            process.wait(timeout=20)
            assert process.returncode==0  # Successful exit suppresses launchd respawn.
            assert not controller.runtime_path.exists()
            assert not controller.status()['ready']
            (tmp_path/'service-proof.json').write_text(json.dumps({'passed':True,
                'supervisor':'isolated command fixture; no OS user service registered',
                'worker':'actual termx --service-worker subprocess','initial_status':status,
                'duplicate_exit':duplicate.returncode,'intentional_stop_exit':process.returncode,
                'private_runtime_removed':not controller.runtime_path.exists(),'model_calls':False},indent=2))
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def test_service_busy_port_fails_without_fallback_listener(tmp_path,monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1)
        controller,environment,command=fixture_service(tmp_path,monkeypatch,listener.getsockname()[1])
        result=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=15)
        assert result.returncode==73
        assert not controller.runtime_path.exists()
