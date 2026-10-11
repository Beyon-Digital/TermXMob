from __future__ import annotations
import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from time import time
from fastapi import HTTPException
from termx.audit import log_event
from termx.private_files import protect_private_path
from termx.runners.machines import capture
from .base import CloudError, check_credentials, until
from .bootstrap import identities, cloud_init
from .aws import AwsProvider
from .azure import AzureProvider
from .gcp import GcpProvider

PROVIDERS={'aws':AwsProvider,'azure':AzureProvider,'gcp':GcpProvider}
TERMINAL={'deleted'}

class CloudService:
    def __init__(self,runners,authority):
        self.runners=runners;self.authority=authority;self.credentials=runners.credentials;self.workers={};self.monitor=None
        self.provider_factory=lambda kind,credentials:PROVIDERS[kind](credentials)
        with runners.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS cloud_accounts(id TEXT PRIMARY KEY,owner TEXT,provider TEXT,name TEXT,identity TEXT,revision INTEGER);
            CREATE TABLE IF NOT EXISTS cloud_previews(id TEXT PRIMARY KEY,owner TEXT,payload TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS cloud_deployments(id TEXT PRIMARY KEY,owner TEXT,request_id TEXT,payload TEXT,UNIQUE(owner,request_id));''')
    def accounts(self,owner):
        with self.runners.db() as db:rows=db.execute('SELECT * FROM cloud_accounts WHERE owner=? ORDER BY rowid DESC',(owner,)).fetchall()
        return [{**dict(row),'identity':json.loads(row['identity']),'authenticated':bool(self.credentials.get('cloud-account:'+row['id']))} for row in rows]
    def account(self,owner,identifier):
        for row in self.accounts(owner):
            if row['id']==identifier:return row
        raise HTTPException(404,'Cloud account not found')
    @asynccontextmanager
    async def provider(self,account):
        raw=self.credentials.get('cloud-account:'+account['id'])
        if not raw:raise CloudError('Cloud authentication is unavailable. Update the saved account in Settings.')
        adapter=self.provider_factory(account['provider'],json.loads(raw))
        try:yield adapter
        finally:await adapter.close()
    async def save_account(self,owner,kind,name,credentials,identifier=None):
        if not name.strip() or len(name)>100:raise HTTPException(400,'Provide a cloud account name')
        old=self.account(owner,identifier) if identifier else None
        if old and old['provider']!=kind:raise HTTPException(409,'Account provider cannot change')
        values=check_credentials(kind,credentials)
        provider=self.provider_factory(kind,values)
        try:identity=await provider.identity()
        except CloudError:raise
        except Exception:raise CloudError('Provider authentication could not be validated.',definitive=True) from None
        finally:await provider.close()
        if old and (old['identity']['account']!=identity['account'] or old['identity'].get('tenant')!=identity.get('tenant')):raise HTTPException(409,'Replacement credentials must belong to the same cloud account')
        identifier=identifier or uuid.uuid4().hex
        try:self.credentials.set('cloud-account:'+identifier,json.dumps(values))
        except Exception:raise HTTPException(503,'Secure credential storage is unavailable. Enable the host OS credential store before saving cloud accounts.') from None
        with self.runners.db() as db:db.execute('INSERT INTO cloud_accounts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,identity=excluded.identity,revision=excluded.revision',(identifier,owner,kind,name,json.dumps(identity),(old['revision']+1) if old else 1))
        log_event('cloud_account_saved',account_id=identifier,provider=kind)
        return self.account(owner,identifier)
    def delete_account(self,owner,identifier):
        self.account(owner,identifier)
        if any(d['plan']['account_id']==identifier and d['status'] not in TERMINAL for d in self.list(owner)):raise HTTPException(409,'Clean up all deployments before removing their cloud account')
        self.credentials.delete('cloud-account:'+identifier)
        with self.runners.db() as db:db.execute('DELETE FROM cloud_accounts WHERE id=? AND owner=?',(identifier,owner))
        return {'deleted':True}
    async def catalog(self,owner,identifier,region='',zone=''):
        async with self.provider(self.account(owner,identifier)) as provider:return await provider.catalog(region,zone)
    async def preview(self,principal,plan):
        account=self.account(principal.id,plan['account_id'])
        async with self.provider(account) as provider:
            catalog=await provider.catalog(plan['region'],plan['zone'])
        for field,key in [('region','regions'),('zone','zones'),('size','sizes'),('image','images')]:
            if plan[field] not in {entry['id'] for entry in catalog.get(key,[])}:raise HTTPException(400,'Select an available '+field+' from the current provider catalog')
        allowed={entry['id'] for entry in catalog['volume_types']}
        if any(v['type'] not in allowed for v in [plan['boot'],*plan['volumes']]):raise HTTPException(400,'Select a supported disk type')
        if plan['network_mode']=='existing':
            if plan['network'] not in {n['id'] for n in catalog['networks']}:raise HTTPException(400,'Choose a network from this account and region')
            subnet=next((s for s in catalog['subnets'] if s['id']==plan['subnet'] and s['network']==plan['network']),None)
            if not subnet or (subnet.get('zone') and subnet['zone']!=plan['zone']):raise HTTPException(400,'Choose a subnet in the selected network and availability zone')
        value={'plan':plan,'account_revision':account['revision'],'provider':account['provider'],'identity':account['identity'],'policy_version':principal.policy_version}
        identifier=uuid.uuid4().hex;expires=time()+300
        with self.runners.db() as db:
            db.execute('DELETE FROM cloud_previews WHERE expires<?',(time(),))
            db.execute('INSERT INTO cloud_previews VALUES(?,?,?,?)',(identifier,principal.id,json.dumps(value),expires))
        return {'id':identifier,'expires':expires,**value,'billing':'Cloud provider usage charges apply until resources are deleted. Prices and quotas are determined by your account.','cleanup_resources':['VM','boot and data disks','managed SSH firewall','public IP when selected',*(['dedicated network and subnet'] if plan['network_mode']=='managed' else [])]}
    def list(self,owner=None):
        with self.runners.db() as db:rows=db.execute('SELECT payload FROM cloud_deployments'+(' WHERE owner=?' if owner else '')+' ORDER BY rowid DESC',(owner,) if owner else ()).fetchall()
        return [json.loads(row['payload']) for row in rows]
    def row(self,owner,identifier):
        with self.runners.db() as db:row=db.execute('SELECT payload FROM cloud_deployments WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:raise HTTPException(404,'Cloud deployment not found')
        return json.loads(row['payload'])
    def update(self,owner,identifier,**changes):
        with self.runners.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM cloud_deployments WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            value={**json.loads(row['payload']),**changes,'updated':time()}
            db.execute('UPDATE cloud_deployments SET payload=? WHERE id=?',(json.dumps(value),identifier))
        return value
    def resource(self,d,kind,name,**changes):
        current=self.row(d['owner'],d['id']);rows=current['resources']
        old=next((r for r in rows if r['kind']==kind and r['name']==name),None)
        if old:old.update(changes)
        else:rows.append({'kind':kind,'name':name,**changes})
        self.update(d['owner'],d['id'],resources=rows)
    def launch(self,principal,preview_id):
        with self.runners.db() as db:row=db.execute('SELECT * FROM cloud_previews WHERE id=? AND owner=?',(preview_id,principal.id)).fetchone()
        if not row:raise HTTPException(404,'Provisioning review not found')
        value=json.loads(row['payload']);plan=value['plan']
        for old in self.list(principal.id):
            if old['request_id']==plan['request_id']:
                if old['plan']!=plan:raise HTTPException(409,'Request ID belongs to another deployment')
                return old
        if row['expires']<=time() or value['policy_version']!=principal.policy_version or self.account(principal.id,plan['account_id'])['revision']!=value['account_revision']:raise HTTPException(409,'Provisioning review expired or changed; review again')
        identifier=uuid.uuid4().hex
        if not self.authority({'owner':principal.id,'project':plan['project_id'],'configuration':{'policy_version':principal.policy_version}}):raise HTTPException(403,'Project execution authority changed')
        try:self.credentials.set('cloud-machine:'+identifier,json.dumps(identities()))
        except Exception:raise HTTPException(503,'Secure storage for machine SSH identities is unavailable') from None
        deployment={'id':identifier,'owner':principal.id,'request_id':plan['request_id'],**value,'status':'queued','resources':[],'runner_id':None,'created':time(),'updated':time(),'expires':time()+plan['lifetime_hours']*3600 if plan['cleanup']!='manual' else None,'error':None}
        try:
            with self.runners.db() as db:db.execute('INSERT INTO cloud_deployments VALUES(?,?,?,?)',(identifier,principal.id,plan['request_id'],json.dumps(deployment)))
        except Exception:
            self.credentials.delete('cloud-machine:'+identifier)
            # Another request may have won the same durable idempotency key.
            for old in self.list(principal.id):
                if old['request_id']==plan['request_id'] and old['plan']==plan:return old
            raise
        self.schedule(deployment,self.provision)
        return deployment
    def schedule(self,d,operation):
        if d['id'] in self.workers:raise HTTPException(409,'A deployment operation is already in progress')
        async def work():
            try:await operation(d)
            finally:
                if self.workers.get(d['id']) is asyncio.current_task():self.workers.pop(d['id'],None)
        self.workers[d['id']]=asyncio.create_task(work())
    def authorized(self,d):
        return self.authority({'owner':d['owner'],'project':d['plan']['project_id'],'configuration':{'policy_version':d['policy_version']}})
    async def provision(self,d):
        try:
            async with asyncio.timeout(1800),self.provider(self.account(d['owner'],d['plan']['account_id'])) as provider:
                self.update(d['owner'],d['id'],status='provisioning',error=None)
                async def ensure(kind,name,params):
                    if not self.authorized(d):raise CloudError('Project execution authority was revoked.')
                    current=self.row(d['owner'],d['id'])
                    record=next((r for r in current['resources'] if r['kind']==kind and r['name']==name),None)
                    if record and record.get('id') and record['state']=='created':return record
                    found=await provider.find(kind,name,d)
                    if found:
                        self.resource(d,kind,name,**found,state='created');return found
                    if record and record['state'] in {'creating','unknown'}:raise CloudError('Resource creation outcome remains unknown; cleanup or inspect before creating another deployment.')
                    self.resource(d,kind,name,state='creating')
                    try:result=await provider.create(kind,name,params,d)
                    except CloudError as exc:
                        self.resource(d,kind,name,state='rejected' if exc.definitive else 'unknown');raise
                    self.resource(d,kind,name,**result,state='created');return result
                keys=json.loads(self.credentials.get('cloud-machine:'+d['id']))
                await provider.provision(d,ensure,cloud_init(keys,d['plan']['allow_sudo']))
                connection=await until(lambda:provider.machine(self.row(d['owner'],d['id'])),lambda value:bool(value and value.get('host')))
                if not self.authorized(d):raise CloudError('Project execution authority was revoked.')
                await self.attach(self.row(d['owner'],d['id']),connection,keys)
                self.update(d['owner'],d['id'],status='ready',error=None)
                log_event('cloud_machine_ready',deployment_id=d['id'],provider=d['provider'])
        except asyncio.CancelledError:
            self.update(d['owner'],d['id'],status='interrupted',error='Provisioning was interrupted; reconcile and clean up owned resources.');raise
        except Exception as exc:
            message=str(exc) if isinstance(exc,CloudError) else 'Machine setup failed. Check network reachability, bootstrap requirements, and cloud permissions.'
            self.update(d['owner'],d['id'],status='failed',error=message)
            await self.cleanup(self.row(d['owner'],d['id']))
    async def attach(self,d,connection,keys):
        import ipaddress
        ipaddress.ip_address(connection['host'])
        directory=self.runners.directory/'cloud-ssh'/d['id'];directory.mkdir(parents=True,exist_ok=True);protect_private_path(directory,directory=True)
        keyfile=directory/'identity';keyfile.write_text(keys['private']);protect_private_path(keyfile)
        known=directory/'known_hosts';known.write_text(connection['host']+' '+keys['host_public']+'\n');protect_private_path(known)
        self.update(d['owner'],d['id'],status='bootstrapping')
        config={'name':d['plan']['name'],'provider':'ssh','host':connection['host'],'user':'termx','port':22,'root':'/home/termx/workspace','identity_file':str(keyfile),'known_hosts_file':str(known),'python_bootstrap':'/usr/local/bin/termx-python','region':d['plan']['region'],'profile':'','instance_id':connection['instance_id'],'trust_machine':True,'policy_version':d['policy_version'],'cloud_deployment':d['id'],'cloud_provider':d['provider']}
        runner=self.runners.machines.register(d['owner'],d['plan']['project_id'],config)
        if d.get('expires'):
            with self.runners.db() as db:db.execute('UPDATE runners SET expires=? WHERE id=?',(d['expires'],runner['id']))
        self.update(d['owner'],d['id'],runner_id=runner['id'])
        async def ready():
            if not self.authorized(d):raise CloudError('Project execution authority was revoked.')
            try:return await capture(self.runners.machines.command(config,'/usr/local/bin/termx-python','--version'),timeout=15)
            except (HTTPException,TimeoutError):return None
        await until(ready,attempts=90)
        await self.runners.machines.connect(d['owner'],runner['id'],setup=True)
    async def request_cleanup(self,owner,identifier):
        d=self.row(owner,identifier)
        if d['status']=='deleted':return d
        worker=self.workers.get(identifier)
        if worker:
            if d['status']=='cleaning':return d
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        d=self.update(owner,identifier,status='cleaning')
        self.schedule(d,self.cleanup);return d
    async def cleanup(self,d):
        self.update(d['owner'],d['id'],status='cleaning',cleanup_error=None)
        try:
            # Disable admission first. Cloud termination remains available if SSH
            # cancellation cannot be confirmed, since it stops the entire owned VM.
            runner_id=d.get('runner_id')
            if runner_id:
                try:await self.runners.stop(d['owner'],runner_id)
                except Exception:pass
            async with self.provider(self.account(d['owner'],d['plan']['account_id'])) as provider:
                for resource in reversed(self.row(d['owner'],d['id'])['resources']):
                    if resource['state'] in {'deleted','rejected'}:continue
                    if not resource.get('id'):
                        found=await provider.find(resource['kind'],resource['name'],d)
                        if not found:raise CloudError('An interrupted create has no confirmed outcome yet. Retry cleanup after inspecting the provider account.')
                        resource.update(found);self.resource(d,resource['kind'],resource['name'],**found,state='created')
                    self.resource(d,resource['kind'],resource['name'],state='deleting')
                    await provider.delete(resource,d)
                    self.resource(d,resource['kind'],resource['name'],state='deleted')
                for resource in await provider.leftovers(d):
                    self.resource(d,resource['kind'],resource['name'],id=resource['id'],state='deleting')
                    await provider.delete(resource,d);self.resource(d,resource['kind'],resource['name'],state='deleted')
            if runner_id:self.runners.machines.status(runner_id,'removed')
            self.credentials.delete('cloud-machine:'+d['id'])
            shutil.rmtree(self.runners.directory/'cloud-ssh'/d['id'],ignore_errors=True)
            self.update(d['owner'],d['id'],status='deleted',cleanup_error=None)
            log_event('cloud_machine_deleted',deployment_id=d['id'],provider=d['provider'])
        except asyncio.CancelledError:
            self.update(d['owner'],d['id'],status='cleanup-failed',cleanup_error='Cleanup was interrupted. Retry to reconcile the remaining resources.');raise
        except Exception as exc:
            self.update(d['owner'],d['id'],status='cleanup-failed',cleanup_error=str(exc) if isinstance(exc,CloudError) else 'Cleanup could not be confirmed. Restore provider access and retry cleanup.')
    def start(self):
        if not self.monitor:self.monitor=asyncio.create_task(self.watch())
    async def watch(self):
        for d in self.list():
            if d['status'] in {'queued','provisioning','bootstrapping','cleaning','interrupted'}:self.schedule(d,self.cleanup)
        while True:
            for d in self.list():
                if d['status']!='ready' or d['id'] in self.workers:continue
                expired=d.get('expires') and d['expires']<=time()
                finished=False
                if d['plan']['cleanup']=='after-task' and d.get('runner_id'):
                    jobs=self.runners.jobs(d['owner'],d['runner_id']);finished=bool(jobs) and not any(j['status']=='running' for j in jobs)
                if expired or finished or not self.authorized(d):self.schedule(d,self.cleanup)
            await asyncio.sleep(5)
    async def close(self):
        if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
        workers=list(self.workers.values())
        for worker in workers:worker.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
