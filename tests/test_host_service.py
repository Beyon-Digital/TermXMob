"""Supervisor fixtures and profile ownership; these do not register OS services."""
import json
import os
import plistlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from termx.cli import build_parser
from termx.host_instance import HostInstance, profile_id
from termx.host_service import HostService


def controller(tmp_path, monkeypatch, platform='linux', run=None):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'home-config'))
    return HostService(tmp_path/'profile',platform=platform,home=tmp_path/'home',
        executable=str(tmp_path/'python with spaces'),run=run or (lambda *a,**kw:SimpleNamespace(returncode=0,stdout='',stderr='')))


@pytest.mark.parametrize('platform',['linux','darwin','win32'])
def test_private_service_install_renders_safe_user_supervisor_without_secret_argv(tmp_path,monkeypatch,platform):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    host=controller(tmp_path,monkeypatch,platform,run)
    result=host.install(bootstrap_passcode='private-bootstrap-secret',web_dir=str(tmp_path/'web bundle'))
    assert result['installed'] and not result['running'] and not result['ready']
    content=host.definition_path.read_text()
    assert 'private-bootstrap-secret' not in content and 'private-bootstrap-secret' not in json.dumps(result)
    assert '--service-worker' in content and '--parent-pid' not in content
    config=json.loads(host.config_path.read_text())
    assert config['bootstrap_passcode']=='private-bootstrap-secret'
    assert config['host']=='127.0.0.1'
    if os.name!='nt':assert host.config_path.stat().st_mode & 0o777 == 0o600
    if platform=='linux':
        assert 'RestartPreventExitStatus=73 79' in content and 'KillMode=control-group' in content
        assert calls[-1][2]=='enable'
    elif platform=='darwin':
        parsed=plistlib.loads(content.encode())
        assert parsed['KeepAlive']=={'SuccessfulExit':False} and parsed['ProgramArguments'][0].endswith('python with spaces')
        assert not calls  # install never starts a launchd host behind the app.
    else:
        parsed=ET.fromstring(content);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        assert parsed.find('.//t:LogonType',ns).text=='InteractiveToken'
        assert parsed.find('.//t:RunLevel',ns).text=='LeastPrivilege'
        assert parsed.find('.//t:ExecutionTimeLimit',ns).text=='PT0S'


def test_install_failure_rolls_back_unregistered_profile(tmp_path,monkeypatch):
    host=controller(tmp_path,monkeypatch,run=lambda *a,**kw:SimpleNamespace(returncode=1,stdout='',stderr='user supervisor unavailable'))
    with pytest.raises(RuntimeError,match='unavailable'):host.install()
    assert not host.config_path.exists() and not host.definition_path.exists()


def test_service_rejects_public_bind_and_unconfigured_remote_profile(tmp_path,monkeypatch):
    host=controller(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='loopback'):host.install(host='0.0.0.0')
    with pytest.raises(ValueError,match='existing'):host.install(tunnel_profile_id='missing')
    assert not host.config_path.exists()


def test_only_display_selectors_survive_service_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('DISPLAY',':97')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS','unix:path=/run/user/fixture/bus')
    monkeypatch.setenv('OPENROUTER_API_KEY','do-not-copy-a-secret')
    host=controller(tmp_path,monkeypatch)
    host.install()
    value=json.loads(host.config_path.read_text())
    assert value['display_environment']['DISPLAY']==':97'
    assert 'OPENROUTER_API_KEY' not in value['display_environment']
    assert 'do-not-copy-a-secret' not in host.config_path.read_text()+host.definition_path.read_text()


def test_profile_lock_prevents_duplicate_process_and_cleans_owned_runtime(tmp_path):
    first=HostInstance(tmp_path).acquire()
    try:
        published=first.publish(host='127.0.0.1',port=8787,managed=True)
        assert 'proof_token' not in published
        code='from pathlib import Path;from termx.host_instance import HostInstance;HostInstance(Path('+repr(str(tmp_path))+')).acquire()'
        attempted=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        assert attempted.returncode!=0 and 'already owns this profile' in attempted.stderr
        assert json.loads(first.runtime_path.read_text())['instance_id']==first.instance_id
    finally:first.close()
    assert not first.runtime_path.exists()
    second=HostInstance(tmp_path).acquire();second.close()


def test_symlink_runtime_is_not_adopted_or_chmodded(tmp_path,monkeypatch):
    host=controller(tmp_path,monkeypatch)
    host.root.mkdir()
    foreign=tmp_path/'foreign';foreign.write_text('{}');foreign.chmod(0o644)
    host.runtime_path.symlink_to(foreign)
    result=host.status()
    assert not result['ready'] and 'symbolic link' in result['last_error']
    if os.name!='nt':assert foreign.stat().st_mode & 0o777 == 0o644


def test_native_start_options_accepted_but_worker_has_no_parent_watchdog():
    args=build_parser().parse_args(['service','start','--config-dir','profile','--port','8791','--host','127.0.0.1'])
    assert args.command=='service' and args.service_action=='start' and args.port==8791
    worker=build_parser().parse_args(['--service-worker','--config-dir','profile'])
    assert worker.service_worker and worker.parent_pid is None
