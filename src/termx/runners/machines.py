"""Persistent, explicitly trusted Linux machines reached through verified SSH.

Cloud credentials stay on the control plane. Machines retain their files and
power state when a task ends; disabling a target only revokes TermX execution.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import shlex
import tarfile
import uuid
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from termx.audit import log_event


def validate_config(config):
    config = dict(config)
    for key, pattern in {
        'host': r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}',
        'user': r'[A-Za-z_][A-Za-z0-9_-]{0,63}',
        'region': r'[a-z0-9-]{0,40}',
        'profile': r'[A-Za-z0-9_./-]{0,100}',
        'instance_id': r'[A-Za-z0-9][A-Za-z0-9_-]{0,254}',
    }.items():
        if key == 'instance_id' and not config.get(key):continue
        if not re.fullmatch(pattern, config.get(key, '')):raise HTTPException(400, f'Invalid machine {key}')
    root = PurePosixPath(config['root'])
    if not root.is_absolute() or '..' in root.parts or len(root.parts)<3 or any(c in config['root'] for c in '\0\n\r'):
        raise HTTPException(400, 'Choose an absolute workspace directory below a home or data directory')
    config['root'] = str(root)
    bootstrap=config.get('python_bootstrap','python3.14')
    if not re.fullmatch(r'(?:/[A-Za-z0-9_./ -]+|[A-Za-z0-9_.-]+)',bootstrap):raise HTTPException(400,'Choose a Python executable name or absolute path')
    config['python_bootstrap']=bootstrap
    for key in ('identity_file', 'known_hosts_file'):
        value=config.get(key, '')
        if value and (not Path(value).is_absolute() or any(c in value for c in '\0\n\r')):
            raise HTTPException(400, f'{key} must be an absolute path on the control plane')
    if not config.get('trust_machine'):raise HTTPException(400, 'Explicitly accept execution with the SSH account permissions')
    if config['provider']!='ssh' and (not config.get('region') or not config.get('instance_id')):
        raise HTTPException(400, 'Cloud machines require a region and instance identifier')
    config.update(kind='machine', network='machine', image=config['name'])
    return config


def ssh_command(config, *argv):
    # Never accept host key changes, passwords, forwarding, or arbitrary SSH flags.
    command=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
             '-o','ClearAllForwardings=yes','-o','ForwardAgent=no',
             '-o','ConnectTimeout=10','-o','ServerAliveInterval=10',
             '-o','ServerAliveCountMax=2','-p',str(config['port'])]
    if config.get('identity_file'):command+=['-o','IdentitiesOnly=yes','-i',config['identity_file']]
    if config.get('known_hosts_file'):command+=['-o','UserKnownHostsFile='+config['known_hosts_file']]
    return [*command,config['user']+'@'+config['host'],shlex.join(argv)]


async def capture(command, data=None, timeout=30):
    try:
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc:raise HTTPException(503, f'Install {command[0]} on the control plane') from exc
    async def bounded(reader):
        out=bytearray()
        while chunk:=await reader.read(65536):
            out.extend(chunk)
            if len(out)>20*1024*1024:raise HTTPException(413,'Machine response exceeds 20 MiB')
        return bytes(out)
    async def exchange():
        async def send():
            if data:process.stdin.write(data);await process.stdin.drain()
            process.stdin.close()
        _,out,_=await asyncio.gather(send(),bounded(process.stdout),bounded(process.stderr))
        await process.wait()
        if process.returncode:raise HTTPException(502,'Machine operation failed. Check SSH trust, connectivity, runtime prerequisites, and cloud permissions.')
        return out
    try:return await asyncio.wait_for(exchange(),timeout)
    finally:
        if process.returncode is None:process.kill()
        await process.wait()


class MachineService:
    def __init__(self,runners):
        self.runners=runners;self.locks={};self.operations={}
        with runners.db() as db:
            db.execute("UPDATE runners SET status='unreachable' WHERE status IN ('setting-up','connecting')")

    def row(self,owner,identifier):
        row=self.runners.row(owner,identifier)
        if row['configuration'].get('kind')!='machine':raise HTTPException(404,'Machine not found')
        return row

    def register(self,owner,project,config):
        config=validate_config(config);identifier=uuid.uuid4().hex
        with self.runners.db() as db:
            db.execute('INSERT INTO runners VALUES(?,?,?,?,?,?,?)',(identifier,owner,project,json.dumps(config),'','registered',253402300799))
        log_event('runner_machine_registered',runner_id=identifier,project_id=project,provider=config['provider'])
        return self.row(owner,identifier)

    def command(self,config,*argv):return ssh_command(config,*argv)

    def worker_command(self,config,*args):
        return self.command(config,config['root']+'/.termx-runtime/bin/python','-I','-m','termx.runners.worker','--machine-root',config['root'],*args)

    async def probe(self,config):
        return json.loads(await capture(self.worker_command(config,'--capabilities')))

    def idle(self,row):
        with self.runners.db() as db:
            if db.execute("SELECT 1 FROM jobs WHERE runner=? AND status='running'",(row['id'],)).fetchone():raise HTTPException(409,'Stop the active task before changing machine state')

    def status(self,identifier,status):
        with self.runners.db() as db:db.execute('UPDATE runners SET status=? WHERE id=?',(status,identifier))

    def error(self,owner,identifier,message):
        config=self.row(owner,identifier)['configuration'];config['last_error']=message
        with self.runners.db() as db:db.execute('UPDATE runners SET configuration=? WHERE id=?',(json.dumps(config),identifier))

    def begin_setup(self,owner,identifier):
        row=self.row(owner,identifier);self.idle(row)
        if row['status']=='removed':raise HTTPException(409,'Register a new connection for this removed machine')
        if not self.runners.authority(row):raise HTTPException(403,'Machine enrollment was revoked')
        if identifier in self.operations:return self.row(owner,identifier)
        if self.locks.setdefault(identifier,asyncio.Lock()).locked():raise HTTPException(409,'Another machine operation is in progress')
        self.status(identifier,'setting-up')
        async def operation():
            try:await self.connect(owner,identifier,setup=True)
            except Exception:pass # persisted status is observed by the desktop
            finally:self.operations.pop(identifier,None)
        self.operations[identifier]=asyncio.create_task(operation())
        return self.row(owner,identifier)

    async def close(self):
        pending=list(self.operations.values())
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)

    async def connect(self,owner,identifier,setup=False):
        lock=self.locks.setdefault(identifier,asyncio.Lock())
        if lock.locked():raise HTTPException(409,'Another machine operation is in progress')
        async with lock:
            row=self.row(owner,identifier);self.idle(row)
            if row['status']=='removed':raise HTTPException(409,'Register a new connection for this removed machine')
            if not self.runners.authority(row):raise HTTPException(403,'Machine enrollment was revoked; register it again under current permissions')
            self.status(identifier,'setting-up' if setup else 'connecting')
            self.error(owner,identifier,None)
            try:
                config=row['configuration']
                if setup:await self.setup(config)
                probe=await self.probe(config)
                from termx.runners.worker import capabilities
                expected=capabilities(config['root'])
                if any(probe.get(key)!=expected[key] for key in ('protocol','engine','root','network','credential_transport','review','machine_control')):
                    raise HTTPException(409,'Install the current TermX machine runtime')
                if not self.runners.authority(row):raise HTTPException(403,'Machine enrollment was revoked during connection')
                self.status(identifier,'ready')
                self.error(owner,identifier,None)
                log_event('runner_machine_connected',runner_id=identifier,setup=setup)
                return self.row(owner,identifier)
            except BaseException as exc:
                self.status(identifier,'unreachable')
                self.error(owner,identifier,exc.detail if isinstance(exc,HTTPException) else 'Setup or connection was interrupted. Check SSH, Python 3.14 with venv, and package network access, then retry.')
                raise

    async def setup(self,config):
        # Ship this control plane's source, not a stale public package release.
        # Dependencies are installed by pip in a private venv on the target.
        import termx
        from importlib.metadata import requires
        source=Path(termx.__file__).parent
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            for path in source.rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and not path.is_symlink():
                    archive.add(path,arcname='termx/'+path.relative_to(source).as_posix(),recursive=False)
            dependencies='\n'.join(r for r in (requires('termx') or []) if 'extra ==' not in r).encode()
            member=tarfile.TarInfo('requirements.txt');member.size=len(dependencies);archive.addfile(member,io.BytesIO(dependencies))
        script='''import pathlib,sys,tarfile,subprocess,sysconfig
assert sys.version_info >= (3,14), 'Python 3.14 or later is required'
root=pathlib.Path(sys.argv[1]); root.mkdir(parents=True,exist_ok=True)
runtime=root/'.termx-runtime'
subprocess.run([sys.executable,'-m','venv',str(runtime)],check=True)
source=runtime/'source'; source.mkdir(exist_ok=True)
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz') as archive: archive.extractall(source,filter='data')
python=str(runtime/'bin/python')
subprocess.run([python,'-m','pip','install','-r',str(source/'requirements.txt')],check=True,stdout=sys.stderr)
site=subprocess.check_output([python,'-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip()
pathlib.Path(site,'termx-machine.pth').write_text(str(source)+'\\n')
'''
        await capture(self.command(config,config['python_bootstrap'],'-c',script,config['root']),buffer.getvalue(),timeout=600)

    async def stop(self,row,teardown=False):
        operation=self.operations.get(row['id'])
        if operation:
            operation.cancel();await asyncio.gather(operation,return_exceptions=True)
        async with self.locks.setdefault(row['id'],asyncio.Lock()):
            return await self._stop(self.row(row['owner'],row['id']),teardown)

    async def _stop(self,row,teardown=False):
        if row['status']=='removed':return row
        # Revoke admission before network I/O. Never equate a lost SSH channel
        # with confirmed cancellation, and never implicitly power off a VM.
        self.status(row['id'],'stopping')
        with self.runners.db() as db:jobs=db.execute("SELECT id FROM jobs WHERE runner=? AND status IN ('running','outcome-unknown')",(row['id'],)).fetchall()
        try:
            for job in jobs:
                await capture(self.worker_command(row['configuration'],'--stop-job',job['id']),timeout=15)
                with self.runners.db() as db:db.execute("UPDATE jobs SET status='stopped' WHERE id=? AND status='outcome-unknown'",(job['id'],))
            self.status(row['id'],'removed' if teardown else 'stopped')
        except BaseException:
            self.status(row['id'],'stop-unconfirmed');raise
        return self.row(row['owner'],row['id'])

    async def cloud_action(self,owner,identifier,action):
        lock=self.locks.setdefault(identifier,asyncio.Lock())
        if lock.locked() or identifier in self.operations:raise HTTPException(409,'Another machine operation is in progress')
        async with lock:
            row=self.row(owner,identifier);config=row['configuration']
            if action!='status':self.idle(row)
            if row['status']=='removed':raise HTTPException(409,'Machine connection was removed')
            if not self.runners.authority(row):raise HTTPException(403,'Machine enrollment was revoked')
            if action not in {'status','start','stop','reboot'}:raise HTTPException(400,'Unsupported cloud action')
            provider=config['provider']
            if provider not in {'ec2','lightsail'}:raise HTTPException(409,'Use your provider console for this machine lifecycle')
            command=['aws','--region',config['region'],'--output','json','--no-cli-pager']
            if config.get('profile'):command+=['--profile',config['profile']]
            if provider=='ec2':
                verb={'status':'describe-instances','start':'start-instances','stop':'stop-instances','reboot':'reboot-instances'}[action]
                command+=['ec2',verb,'--instance-ids',config['instance_id']]
            else:
                verb={'status':'get-instance-state','start':'start-instance','stop':'stop-instance','reboot':'reboot-instance'}[action]
                command+=['lightsail',verb,'--instance-name',config['instance_id']]
            if action!='status':self.status(identifier,'cloud-'+action+'-pending')
            try:result=json.loads(await capture(command,timeout=60) or b'{}')
            except BaseException:
                if action!='status':self.status(identifier,'cloud-outcome-unknown')
                raise
            log_event('runner_machine_cloud_action',runner_id=identifier,action=action,provider=provider)
            return {'action':action,'result':result,'machine':self.row(owner,identifier)}
