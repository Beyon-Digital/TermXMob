"""Dedicated Docker runners on the owner's local or SSH Docker host.

No host bind mounts or implicit execution migration. Containers
have fixed CPU/memory/PID limits, isolated scratch storage and explicit network
policy. SSH hosts use the user's existing SSH host-key verification and keys.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shlex
import sqlite3
import tarfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from time import time
from fastapi import HTTPException
from termx.audit import log_event

class RunnerService:
    def __init__(self, directory, credentials, authority=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory/'runners.sqlite3'
        self.credentials = credentials
        self.authority = authority or (lambda row: True)
        self.workers = {}
        self.monitor = None
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS runners (
                id TEXT PRIMARY KEY, owner TEXT, project TEXT, configuration TEXT,
                container TEXT, status TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, runner TEXT, request_id TEXT, digest TEXT,
                configuration TEXT, status TEXT, started REAL, ended REAL, result TEXT,
                UNIQUE(runner, request_id));''')
        self.path.chmod(0o600)
        from termx.runners.machines import MachineService
        self.machines = MachineService(self)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    async def docker(self, configuration, *args, data=None, timeout=30, on_chunk=None):
        command=['docker']
        endpoint=configuration.get('endpoint','')
        if endpoint:command+=['--host',endpoint]
        else:command+=['--context',configuration['context']]
        try:
            process=await asyncio.create_subprocess_exec(*command,*args,stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError as exc:raise HTTPException(503,'Install the Docker CLI on this host') from exc
        async def collect(reader):
            output=bytearray()
            while chunk:=await reader.read(8192):
                output.extend(chunk)
                if on_chunk:
                    del output[:-100000]
                    on_chunk(bytes(output))
                elif len(output)>20*1024*1024:
                    raise HTTPException(413,'Runner response exceeds 20 MiB')
            return bytes(output)
        async def communicate():
            if data:process.stdin.write(data);await process.stdin.drain()
            process.stdin.close()
            out,err=await asyncio.gather(collect(process.stdout),collect(process.stderr))
            await process.wait();return out,err
        try:
            out,err=await asyncio.wait_for(communicate(),timeout)
        except (TimeoutError,asyncio.CancelledError):
            if process.returncode is None:process.kill()
            await process.wait();raise
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        if len(out)>20*1024*1024:raise HTTPException(413,'Runner response exceeds 20 MiB')
        if process.returncode:raise HTTPException(502,'Docker operation failed; inspect host connectivity, image availability and container state')
        return out

    def row(self, owner, identifier):
        with self.db() as db:row=db.execute('SELECT * FROM runners WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:raise HTTPException(404,'Runner not found')
        return {**dict(row),'configuration':json.loads(row['configuration'])}

    def list(self, owner):
        with self.db() as db:ids=db.execute('SELECT id FROM runners WHERE owner=? ORDER BY rowid DESC',(owner,)).fetchall()
        return [self.row(owner,row['id']) for row in ids]

    async def provision(self, owner, project, config):
        config=dict(config)
        if config.get('network','none') not in {'none','bridge'}:raise HTTPException(400,'Choose isolated or explicitly networked execution')
        endpoint=config.get('endpoint','')
        if endpoint and not re.fullmatch(r'ssh://(?:[a-zA-Z0-9_.-]+@)?[a-zA-Z0-9.-]+(?::[0-9]{1,5})?',endpoint):
            raise HTTPException(400,'Remote enrollment requires an SSH Docker endpoint with verified host keys')
        if not endpoint:
            try:
                process=await asyncio.create_subprocess_exec('docker','context','show',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            except FileNotFoundError as exc:
                raise HTTPException(503,'Install the Docker CLI on this host') from exc
            try:
                out,_=await asyncio.wait_for(process.communicate(),10)
            except (TimeoutError,asyncio.CancelledError):
                if process.returncode is None:process.kill()
                await process.wait();raise
            config['context']=out.decode().strip()
            if process.returncode or not config['context']:raise HTTPException(503,'No Docker context available')
        image=config.get('image','')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,255}',image):raise HTTPException(400,'Select a locally available container image')
        # Resolve an immutable image ID. Provision never downloads an image.
        details=json.loads(await self.docker(config,'image','inspect',image))
        config['image_id']=details[0]['Id']
        config['cpu']=max(.1,min(float(config.get('cpu',1)),8))
        config['memory_mb']=max(64,min(int(config.get('memory_mb',512)),8192))
        seconds=max(10,min(int(config.get('lease_seconds',3600)),86400))
        config['lease_seconds']=seconds
        identifier=uuid.uuid4().hex
        with self.db() as db:db.execute('INSERT INTO runners VALUES(?,?,?,?,?,?,?)',
            (identifier,owner,project,json.dumps(config),'','provisioning',time()+seconds))
        try:
            container=(await self.docker(config,'create','--name','termx-runner-'+identifier,
                '--label','termx.runner='+identifier,'--user','65534:65534','--read-only',
                '--tmpfs','/workspace:rw,exec,nosuid,size=268435456,mode=1777',
                '--tmpfs','/tmp:rw,noexec,nosuid,size=67108864,mode=1777',
                '--tmpfs','/run/termx:rw,noexec,nosuid,size=1048576,mode=1777',
                '--cap-drop','ALL','--security-opt','no-new-privileges=true','--pids-limit','128',
                '--cpus',str(config['cpu']),'--memory',str(config['memory_mb'])+'m',
                '--network',config.get('network','none'),'--workdir','/workspace',
                # Docker client proxy configuration can contain credentials;
                # enrollment does not silently inject it into the chosen image.
                *[flag for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy') for flag in ('--env',name+'=')],
                '--entrypoint','/bin/sh',config['image_id'],'-c','exec sleep '+str(seconds))).decode().strip()
            with self.db() as db:db.execute('UPDATE runners SET container=?,status=? WHERE id=?',(container,'starting',identifier))
            await self.docker(config,'start',container)
            await self.docker(config,'exec',container,'/bin/sh','-c','command -v tar >/dev/null && command -v timeout >/dev/null')
            with self.db() as db:db.execute('UPDATE runners SET status=? WHERE id=?',('ready',identifier))
            log_event('runner_provisioned',runner_id=identifier,project_id=project,network=config.get('network','none'))
            return self.row(owner,identifier)
        except BaseException:
            with self.db() as db:db.execute('UPDATE runners SET status=? WHERE id=?',('failed',identifier))
            raise

    def jobs(self, owner, identifier):
        self.row(owner,identifier)
        with self.db() as db:rows=db.execute('SELECT * FROM jobs WHERE runner=? ORDER BY started DESC',(identifier,)).fetchall()
        return [{**dict(row),'result':json.loads(row['result']) if row['result'] else None,
                 'configuration':json.loads(row['configuration'])} for row in rows]

    async def run(self, owner, identifier, request):
        runner=self.row(owner,identifier);request=dict(request)
        if runner['configuration'].get('kind')=='machine':raise HTTPException(409,'Use an agent session to run reviewed commands on this machine')
        if runner['status']!='ready' or runner['expires']<=time() or not self.authority(runner):raise HTTPException(403,'Runner lease is unavailable or revoked')
        argv=request.get('argv')
        if not isinstance(argv,list) or not argv or len(argv)>64 or any(not isinstance(arg,str) or '\0' in arg or len(arg)>8192 for arg in argv):raise HTTPException(400,'Provide an explicit command argument array')
        seconds=int(request.get('seconds',60))
        if seconds<1 or seconds>min(3600,int(runner['expires']-time())):raise HTTPException(400,'Run budget must fit the remaining lease')
        refs=request.get('secrets',{})
        if not isinstance(refs,dict) or len(refs)>20 or any(not re.fullmatch(r'TERMX_SECRET_[A-Z0-9_]{1,64}',key) or not isinstance(value,str) for key,value in refs.items()):raise HTTPException(400,'Secrets require scoped TERMX_SECRET_ environment references')
        digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        job_id=uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM jobs WHERE runner=? AND request_id=?',(identifier,request['request_id'])).fetchone()
            if previous:
                if previous['digest']!=digest:raise HTTPException(409,'Request ID belongs to another command')
                return next(job for job in self.jobs(owner,identifier) if job['id']==previous['id'])
            if db.execute("SELECT 1 FROM jobs WHERE runner=? AND status='running'",(identifier,)).fetchone():raise HTTPException(409,'This dedicated runner already has an active job')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(job_id,identifier,request['request_id'],digest,json.dumps(request),'running',time(),None,None))
        self.workers[job_id]=asyncio.create_task(self._execute(runner,job_id,request))
        return next(job for job in self.jobs(owner,identifier) if job['id']==job_id)

    async def _execute(self, runner, identifier, request):
        config=runner['configuration'];container=runner['container'];values=[]
        status='failed';result={'error':'Runner stopped before a confirmed outcome'}
        try:
            exports=[]
            for name,reference in request.get('secrets',{}).items():
                value=self.credentials.get(reference)
                if value is None:raise HTTPException(409,'A scoped credential reference is unavailable')
                values.append(value);exports.append('export '+name+'='+shlex.quote(value))
            # stdin tar keeps credentials out of CLI arguments and durable configuration.
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as archive:
                raw=('\n'.join(exports)+'\n').encode();info=tarfile.TarInfo(identifier+'.env');info.size=len(raw);info.mode=0o600;info.uid=info.gid=65534;archive.addfile(info,io.BytesIO(raw))
            await self.docker(config,'exec','-i',container,'tar','-xf','-','-C','/run/termx',data=buffer.getvalue())
            def stream_output(output):
                with self.db() as db:db.execute('UPDATE jobs SET result=? WHERE id=? AND status=?',
                    (json.dumps({'stdout':output.decode(errors='replace'),'live':True}),identifier,'running'))
            output=await self.docker(config,'exec',container,'/bin/sh','-c',
                '. /run/termx/'+identifier+'.env; timeout -s KILL '+str(int(request.get('seconds',60)))+' "$@" 2>&1; code=$?; printf "\\nTERMX_EXIT:%s\\n" "$code"',
                'termx-job',*request['argv'],timeout=int(request.get('seconds',60)),
                on_chunk=None if values else stream_output)
            text=output.decode(errors='replace')[-100000:]
            for value in values:
                if value:text=text.replace(value,'[REDACTED]')
            text,marker,code=text.rpartition('\nTERMX_EXIT:')
            exit_code=int(code.strip()) if marker else None
            status='completed' if exit_code==0 else 'failed';result={'stdout':text,'exit_code':exit_code,'truncated':len(output)>100000}
            if exit_code in {124,137,143}:
                status='budget-stopped';result['error']='Run time budget exhausted; dedicated container stopped'
                await self.stop(runner['owner'],runner['id'])
        except TimeoutError:
            status='budget-stopped';result={'error':'Run time budget exhausted; dedicated container stopped'}
            await self.stop(runner['owner'],runner['id'])
        except asyncio.CancelledError:
            status='stopped';result={'error':'Run cancelled; inspect the dedicated runner before retrying'}
        except Exception as exc:
            result={'error':exc.detail if isinstance(exc,HTTPException) else 'Runner operation failed'}
        finally:
            try:await self.docker(config,'exec',container,'rm','-f','/run/termx/'+identifier+'.env')
            except Exception:pass
            with self.db() as db:db.execute('UPDATE jobs SET status=?,ended=?,result=? WHERE id=?',(status,time(),json.dumps(result),identifier))
            log_event('runner_job_finished',runner_id=runner['id'],job_id=identifier,status=status)
            self.workers.pop(identifier,None)

    async def stop(self, owner, identifier, teardown=False):
        runner=self.row(owner,identifier)
        if runner['configuration'].get('kind')=='machine':return await self.machines.stop(runner,teardown)
        if runner['container']:
            await self.docker(runner['configuration'],'rm' if teardown else 'stop',
                *(['--force'] if teardown else ['--time','1']),runner['container'])
        with self.db() as db:
            db.execute('UPDATE runners SET status=? WHERE id=?',('removed' if teardown else 'stopped',identifier))
            db.execute("UPDATE jobs SET status='outcome-unknown',ended=? WHERE runner=? AND status='running'",(time(),identifier))
        log_event('runner_stopped',runner_id=identifier,teardown=teardown)
        return self.row(owner,identifier)

    async def upload(self, owner, identifier, archive_bytes):
        runner=self.row(owner,identifier)
        if runner['configuration'].get('kind')=='machine':raise HTTPException(409,'Use SSH/SFTP or an agent session to manage persistent machine files')
        if runner['status']!='ready' or not self.authority(runner):raise HTTPException(403,'Runner is unavailable')
        if len(archive_bytes)>20*1024*1024:raise HTTPException(413,'Workspace archive exceeds 20 MiB')
        output=io.BytesIO();total=0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as source,tarfile.open(fileobj=output,mode='w') as target:
                for member in source:
                    path=Path(member.name)
                    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()) or path.is_absolute() or '..' in path.parts:raise HTTPException(400,'Workspace archives cannot include links, devices or paths outside the runner')
                    total+=member.size
                    if total>20*1024*1024:raise HTTPException(413,'Expanded workspace exceeds 20 MiB')
                    member.uid=member.gid=65534;member.mode=0o700 if member.isdir() else 0o600
                    target.addfile(member,source.extractfile(member) if member.isfile() else None)
        except tarfile.TarError as exc:raise HTTPException(400,'Invalid workspace tar archive') from exc
        await self.docker(runner['configuration'],'exec','-i',runner['container'],'tar','-xf','-','-C','/workspace',data=output.getvalue())
        return {'ok':True,'bytes':total}

    async def results(self, owner, identifier, path):
        runner=self.row(owner,identifier);relative=Path(path)
        if runner['configuration'].get('kind')=='machine':raise HTTPException(409,'Use SSH/SFTP or an agent session to retrieve persistent machine files')
        if relative.is_absolute() or '..' in relative.parts or not path:raise HTTPException(400,'Choose a result path inside the runner workspace')
        # tar is generated in the container, not followed by the host.
        raw=await self.docker(runner['configuration'],'exec',runner['container'],'tar','-cf','-','-C','/workspace',path)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            if any(member.issym() or member.islnk() for member in archive):raise HTTPException(400,'Result contains links; choose regular files')
        return raw

    def start(self):
        if getattr(self,'cloud',None):self.cloud.start()
        if self.monitor is None:self.monitor=asyncio.create_task(self._monitor())

    async def _monitor(self):
        # In-flight exec outcome is ambiguous after host restart: never rerun it.
        with self.db() as db:
            interrupted=[dict(row) for row in db.execute("SELECT DISTINCT runners.* FROM runners JOIN jobs ON jobs.runner=runners.id WHERE jobs.status='running'")]
            db.execute("UPDATE jobs SET status='outcome-unknown',ended=? WHERE status='running'",(time(),))
        for row in interrupted:
            try:await self.stop(row['owner'],row['id'])
            except Exception:
                with self.db() as db:db.execute("UPDATE runners SET status='stop-unconfirmed' WHERE id=?",(row['id'],))
        while True:
            with self.db() as db:rows=[dict(row) for row in db.execute("SELECT * FROM runners WHERE status IN ('ready','starting')")]
            for row in rows:
                row['configuration']=json.loads(row['configuration'])
                if row['expires']<=time() or not self.authority(row):
                    try:await self.stop(row['owner'],row['id'])
                    except Exception:
                        with self.db() as db:db.execute("UPDATE runners SET status='stop-unconfirmed' WHERE id=?",(row['id'],))
            await asyncio.sleep(1)

    async def close(self):
        if getattr(self,'cloud',None):await self.cloud.close()
        await self.machines.close()
        if self.monitor:
            self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
        with self.db() as db:
            active=[dict(row) for row in db.execute("SELECT DISTINCT runners.* FROM runners JOIN jobs ON jobs.runner=runners.id WHERE jobs.status='running'")]
        for row in active:
            try:await self.stop(row['owner'],row['id'])
            except Exception:
                with self.db() as db:db.execute("UPDATE runners SET status='stop-unconfirmed' WHERE id=?",(row['id'],))
        for worker in list(self.workers.values()):worker.cancel()
        await asyncio.gather(*list(self.workers.values()),return_exceptions=True)
