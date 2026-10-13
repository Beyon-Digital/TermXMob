"""Opt-in per-user supervisors; install is explicit and stores no argv secrets."""
from __future__ import annotations

import getpass
import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from termx.config import ConfigStore, atomic_write, config_dir
from termx.host_instance import private_owned, profile_id

DISPLAY_KEYS=('DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class HostService:
    def __init__(self, root=None, *, platform=None, home=None, run=None, executable=None):
        self.root = Path(root or config_dir()).expanduser().resolve()
        self.platform = platform or sys.platform
        self.home = Path(home or Path.home())
        self.run = run or subprocess.run
        self.executable = executable or sys.executable
        self.id = profile_id(self.root)
        self.name = 'termx-host-' + self.id[:12]
        self.config_path = self.root / 'host-service.json'
        self.runtime_path = self.root / 'host-runtime.json'

    @property
    def backend(self):
        if self.platform.startswith('linux'): return 'systemd-user'
        if self.platform == 'darwin': return 'launchd-user'
        if self.platform == 'win32': return 'windows-task'
        return 'unsupported'

    @property
    def definition_path(self):
        if self.backend == 'systemd-user':
            base = Path(os.environ.get('XDG_CONFIG_HOME', self.home / '.config'))
            return base / 'systemd' / 'user' / (self.name + '.service')
        if self.backend == 'launchd-user':
            return self.home / 'Library' / 'LaunchAgents' / (self.name + '.plist')
        return self.root / (self.name + '.xml')

    def _command(self):
        return [self.executable] if getattr(sys, 'frozen', False) else [self.executable, '-m', 'termx']

    def _settings(self):
        if not self.config_path.exists():
            return None
        private_owned(self.config_path)
        value = json.loads(self.config_path.read_text())
        if value.get('profile_id') != self.id or value.get('schema') != 1:
            raise ValueError('Host service configuration belongs to another profile')
        return value

    def _invoke(self, *arguments, check=True):
        result = self.run(list(arguments), capture_output=True, text=True, timeout=15)
        if check and result.returncode:
            reason = (result.stderr or result.stdout or 'Supervisor command failed').strip()[-1500:]
            raise RuntimeError(reason)
        return result

    @staticmethod
    def _unit_quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

    def _arguments(self, settings):
        return [*settings['command'], '--service-worker', '--config-dir', str(self.root)]

    def render(self, settings):
        arguments = self._arguments(settings)
        if self.backend == 'systemd-user':
            env = f'Environment={self._unit_quote("TERMX_CONFIG_DIR=" + str(self.root))}\n'
            if settings.get('pythonpath'):
                env += f'Environment={self._unit_quote("PYTHONPATH=" + settings["pythonpath"])}\n'
            for key,value in settings.get('display_environment',{}).items():
                env += f'Environment={self._unit_quote(key+"="+value)}\n'
            return ('[Unit]\nDescription=Termx background host\nAfter=network.target\n\n[Service]\nType=simple\n'
                'ExecStart=' + ' '.join(self._unit_quote(arg) for arg in arguments) + '\n' + env +
                'WorkingDirectory=' + self._unit_quote(settings['working_directory']) + '\n'
                'Restart=on-failure\nRestartSec=3\nRestartPreventExitStatus=73 79\nKillMode=control-group\n'
                'TimeoutStopSec=30\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
        if self.backend == 'launchd-user':
            env = {'TERMX_CONFIG_DIR': str(self.root)}
            if settings.get('pythonpath'): env['PYTHONPATH'] = settings['pythonpath']
            return plistlib.dumps({'Label': self.name, 'ProgramArguments': arguments, 'EnvironmentVariables': env,
                'WorkingDirectory': settings['working_directory'], 'RunAtLoad': True,
                'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 3,
                'StandardOutPath': str(self.root / 'host-service.log'),
                'StandardErrorPath': str(self.root / 'host-service-error.log')}).decode()
        if self.backend == 'windows-task':
            namespace='http://schemas.microsoft.com/windows/2004/02/mit/task'
            ET.register_namespace('', namespace)
            def node(parent, name, value=None):
                child = ET.SubElement(parent, '{'+namespace+'}'+name)
                if value is not None: child.text = str(value)
                return child
            task = ET.Element('{'+namespace+'}Task', version='1.4')
            triggers=node(task,'Triggers');node(node(triggers,'LogonTrigger'),'Enabled','true')
            principal=node(node(task,'Principals'),'Principal');principal.set('id','CurrentUser')
            user = os.environ.get('USERDOMAIN', '')
            node(principal,'UserId',(user+'\\' if user else '')+getpass.getuser())
            node(principal,'LogonType','InteractiveToken');node(principal,'RunLevel','LeastPrivilege')
            options=node(task,'Settings');node(options,'MultipleInstancesPolicy','IgnoreNew')
            node(options,'DisallowStartIfOnBatteries','false');node(options,'StopIfGoingOnBatteries','false')
            node(options,'ExecutionTimeLimit','PT0S');node(options,'AllowStartOnDemand','true')
            restart=node(options,'RestartOnFailure');node(restart,'Interval','PT1M');node(restart,'Count',3)
            actions=node(task,'Actions');actions.set('Context','CurrentUser');action=node(actions,'Exec')
            node(action,'Command',arguments[0]);node(action,'Arguments',subprocess.list2cmdline(arguments[1:]))
            node(action,'WorkingDirectory',settings['working_directory'])
            return ET.tostring(task, encoding='unicode')
        raise RuntimeError('This platform has no supported per-user service supervisor')

    def install(self, *, host='127.0.0.1', port=8787, web_dir=None, bootstrap_passcode=None,tunnel_profile_id=None,tunnel_authority=None):
        if host not in {'127.0.0.1','::1'}:
            raise ValueError('Background services bind to loopback; use authenticated TLS remote access')
        if not isinstance(port,int) or isinstance(port,bool) or not 1 <= port <= 65535:
            raise ValueError('Service port must be between 1 and 65535')
        if self.backend == 'unsupported': raise RuntimeError('Unsupported user service platform')
        if any(ord(c) < 32 for c in str(self.root)+str(self.executable)+str(web_dir or '')+str(Path.cwd())):
            raise ValueError('Service paths cannot contain control characters')
        if tunnel_profile_id and not ConfigStore(self.root/'config.json').get_profile(tunnel_profile_id):
            raise ValueError('Choose an existing configured tunnel profile for remote startup')
        previous = self._settings() or {}
        settings = {'schema':1,'profile_id':self.id,'host':host,'port':port,'web_dir':web_dir,
            'command':self._command(),'working_directory':str(Path.cwd().resolve()),
            'pythonpath':str(Path(__file__).resolve().parents[1]) if not getattr(sys,'frozen',False) else None,
            'tunnel_profile_id':tunnel_profile_id,'tunnel_authority':tunnel_authority,
            'bootstrap_passcode':bootstrap_passcode if bootstrap_passcode is not None else previous.get('bootstrap_passcode')}
        settings['display_environment']={key:os.environ[key] for key in DISPLAY_KEYS
            if self.backend=='systemd-user' and key in os.environ and len(os.environ[key])<=1024 and not any(ord(c)<32 for c in os.environ[key])}
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        private_owned(self.root,directory=True)
        definition=self.definition_path
        old_config=self.config_path.read_text() if self.config_path.exists() else None
        if definition.exists():private_owned(definition)
        old_definition=definition.read_text() if definition.exists() else None
        atomic_write(self.config_path,json.dumps(settings))
        atomic_write(definition,self.render(settings))
        try:
            if self.backend == 'systemd-user':
                self._invoke('systemctl','--user','daemon-reload')
                self._invoke('systemctl','--user','enable',self.name+'.service')
            elif self.backend == 'launchd-user':
                pass  # Registration is loaded explicitly by start, or at next login.
            else:
                self._invoke('schtasks','/Create','/TN',self.name,'/XML',str(definition),'/F')
        except (OSError,RuntimeError,subprocess.TimeoutExpired):
            for path,old in ((self.config_path,old_config),(definition,old_definition)):
                if old is None:path.unlink(missing_ok=True)
                else:atomic_write(path,old)
            if self.backend=='systemd-user':
                self._invoke('systemctl','--user','daemon-reload',check=False)
            raise
        return self.status()

    def start(self, *, bootstrap_passcode=None):
        settings=self._settings()
        if not settings: raise ValueError('Install this host service before starting it')
        if bootstrap_passcode is not None:
            settings['bootstrap_passcode']=bootstrap_passcode
            atomic_write(self.config_path,json.dumps(settings))
        current=self.status()
        if current['ready']:
            if not current['managed']:
                raise RuntimeError('This profile is owned by an app host; stop it before starting the background service')
            return current
        if self.backend == 'systemd-user': self._invoke('systemctl','--user','start',self.name+'.service')
        elif self.backend == 'launchd-user':
            domain=f'gui/{os.getuid()}'
            loaded=self._invoke('launchctl','print',f'{domain}/{self.name}',check=False)
            if loaded.returncode:self._invoke('launchctl','bootstrap',domain,str(self.definition_path))
            else:self._invoke('launchctl','kickstart',f'{domain}/{self.name}')
        else: self._invoke('schtasks','/Run','/TN',self.name)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            current=self.status()
            if current['ready']:return current
            time.sleep(.1)
        raise RuntimeError(current['last_error'] or 'Host did not become ready within 10 seconds; inspect the user supervisor log')

    def stop(self, *, wait=True):
        if not self._settings(): return self.status()
        if self.backend == 'systemd-user': self._invoke('systemctl','--user',*(() if wait else ('--no-block',)),'stop',self.name+'.service')
        elif self.backend == 'launchd-user':
            # Keep the registration but disable respawn until the next explicit start.
            self._invoke('launchctl','bootout',f'gui/{os.getuid()}/{self.name}',check=False)
        else: self._invoke('schtasks','/End','/TN',self.name,check=False)
        result=self.status()
        if wait:
            deadline=time.monotonic()+10
            while result['ready'] and result['managed'] and time.monotonic()<deadline:
                time.sleep(.1);result=self.status()
            if result['ready'] and result['managed']:
                raise RuntimeError('The background host is still running after its supervisor stop request')
        return result

    def uninstall(self, *, wait=True):
        if not self._settings(): return self.status()
        self.stop(wait=wait)
        if self.backend == 'systemd-user':
            self._invoke('systemctl','--user','disable',self.name+'.service')
        elif self.backend == 'windows-task': self._invoke('schtasks','/Delete','/TN',self.name,'/F')
        self.definition_path.unlink(missing_ok=True)
        self.config_path.unlink(missing_ok=True)
        if self.backend == 'systemd-user': self._invoke('systemctl','--user','daemon-reload')
        return self.status()

    def status(self):
        result={'backend':self.backend,'supported':self.backend!='unsupported','installed':self.config_path.is_file(),
            'running':False,'ready':False,'managed':False,'pid':None,'port':None,'url':None,
            'profile_id':self.id,'instance_id':None,'requires_login':True,'last_error':None}
        try:
            settings=self._settings()
            if settings:
                result['port']=settings['port']
                result['tunnel_profile_id']=settings.get('tunnel_profile_id')
                result['graphical_environment']=bool(settings.get('display_environment'))
            if not self.runtime_path.exists(): return result
            private_owned(self.runtime_path)
            runtime=json.loads(self.runtime_path.read_text())
            if runtime.get('profile_id') != self.id or runtime.get('host') not in {'127.0.0.1','::1'}:
                raise ValueError('Host runtime profile or local endpoint does not match')
            port=runtime.get('port')
            if not isinstance(port,int) or not 1<=port<=65535: raise ValueError('Invalid runtime port')
            address=f'[{runtime["host"]}]' if ':' in runtime['host'] else runtime['host']
            url=f'http://{address}:{port}'
            request=urllib.request.Request(url+'/api/host/runtime',headers={'X-Termx-Host-Proof':runtime['proof_token']})
            # Ignore proxy environment: discovery is only to the local process.
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
            with opener.open(request,timeout=.5) as response:
                observed=json.loads(response.read(8192))
            if any(observed.get(key)!=runtime.get(key) for key in ('instance_id','profile_id','pid','managed')):
                raise ValueError('Local endpoint is owned by a different host instance')
            result.update({key:runtime.get(key) for key in ('pid','port','instance_id','managed')})
            result.update(running=True,ready=True,url=url)
            result['remote']=observed.get('remote',{'state':'disabled','url':None})
        except (OSError,ValueError,KeyError,urllib.error.URLError) as exc:
            result['last_error']=str(exc)
        return result
