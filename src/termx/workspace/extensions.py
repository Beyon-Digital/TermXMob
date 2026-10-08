"""Reviewed immutable extension bundles and private local registries.

Compatibility importers are trusted host composition callbacks. Bundles cannot
install auth adapters or evaluate code in the daemon process.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from urllib.parse import urljoin

from termx.workspace.https_registry import HTTPSRegistryTransport, checked_url
from pathlib import Path, PurePosixPath
from time import time

from termx.agent.policy import redact
from termx.agent.store import ACTIVE_STATUSES
from termx.workspace.store import Conflict

SLUG = re.compile(r'^[a-z0-9][a-z0-9._-]{0,79}$')
SECRETS = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:password|api_key|access_token|refresh_token|client_secret)\s*[:=]\s*["\']?[^\s"\']{8,}',re.I)


class ExtensionService:
    def __init__(self,workspace,root: Path | None = None):
        self.workspace=workspace
        self.store=workspace.store
        self.root=root or self.store.path.parent/'extension-bundles'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.transport=HTTPSRegistryTransport()
        self.adapters={'termx-bundle/v1':lambda data:data,'skill-markdown/v1':self._skill}

    def register_adapter(self,name,adapter):
        if not name or name in self.adapters:
            raise ValueError('Compatibility adapter name must be unique')
        self.adapters[name]=adapter

    @staticmethod
    def _skill(data):
        if not {'id','version','markdown'}<=set(data):raise ValueError('Skill import needs id, version and markdown')
        return {'format':'termx-bundle/v1','id':data['id'],'version':data['version'],
                'permissions':data.get('permissions',['files-read']),
                'files':{'skills/'+data['id']+'/SKILL.md':data['markdown']}}

    def normalize(self,data,format):
        if format not in self.adapters:
            raise ValueError('Unsupported extension format; a trusted compatibility adapter is required')
        if not isinstance(data,dict):raise ValueError('Bundle data must be an object')
        bundle=self.adapters[format](data)
        if not isinstance(bundle,dict):raise ValueError('Imported bundle must be an object')
        if len(json.dumps(bundle).encode())>2_100_000:raise ValueError('Entire bundle exceeds its byte budget')
        if set(bundle)-{'format','id','version','permissions','files','description','dependencies'} or bundle.get('format')!='termx-bundle/v1':
            raise ValueError('Invalid bundle format')
        if not isinstance(bundle.get('id'),str) or not isinstance(bundle.get('version'),str) or not SLUG.fullmatch(bundle['id']) or not SLUG.fullmatch(bundle['version']):
            raise ValueError('Bundle ID and version must be safe slugs')
        if not isinstance(bundle.get('permissions',[]),list) or any(not isinstance(p,str) for p in bundle.get('permissions',[])):
            raise ValueError('Permissions must be a list of supported names')
        permissions=set(bundle.get('permissions',[]))
        if not permissions <= {'files-read','files-write','git-read','git-write','agent-run','network-manage'}:
            raise ValueError('Extension requests forbidden host/authentication permissions')
        files=bundle.get('files')
        if not isinstance(files,dict) or not files or len(files)>200:
            raise ValueError('Bundle needs one to 200 text files')
        total=0
        for name,content in files.items():
            if not isinstance(name,str):raise ValueError('Bundle file paths must be text')
            path=PurePosixPath(name)
            if '\\' in name or path.is_absolute() or any(part in {'..','.',''} for part in name.split('/')) or path.parts[0] not in {'skills','agents','hooks','mcp','instructions'}:
                raise ValueError('Unsafe or unsupported bundle path')
            if any(part.startswith('.env') or part in {'.ssh','.aws','credentials.json'} for part in path.parts):
                raise ValueError('Credential files cannot be installed/exported')
            if not isinstance(content,str) or redact(content)!=content or SECRETS.search(content):
                raise ValueError('Bundle must not contain embedded credentials')
            if name.endswith('/SKILL.md'):
                from termx.workspace.skill_sources import validate_markdown
                validate_markdown(content)
            total+=len(content.encode())
        if total>2_000_000:
            raise ValueError('Extension bundle exceeds two megabytes')
        dependencies=bundle.get('dependencies',[])
        if not isinstance(dependencies,list) or len(dependencies)>20:
            raise ValueError('Bundle dependencies must be a bounded list')
        seen=set()
        for dependency in dependencies:
            if (not isinstance(dependency,dict) or set(dependency)!={'id','version','digest'}
                    or not isinstance(dependency.get('id'),str) or not isinstance(dependency.get('version'),str) or not isinstance(dependency.get('digest'),str)
                    or not SLUG.fullmatch(dependency['id']) or not SLUG.fullmatch(dependency['version'])
                    or not re.fullmatch('[0-9a-f]{64}',dependency.get('digest','')) or dependency['id'] in seen
                    or dependency['id']==bundle['id']):
                raise ValueError('Dependencies require unique IDs, versions and canonical bundle digests')
            seen.add(dependency['id'])
        return {**bundle,'permissions':sorted(permissions)}

    def preview(self,principal,data,format='termx-bundle/v1'):
        self.workspace.require(principal,'host-admin')
        bundle=self.normalize(data,format)
        installed=self.store.get('extension',bundle['id'])
        old=set((installed or {}).get('permissions',[])); new=set(bundle['permissions'])
        digest=hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()
        return self.store.create('extension_preview',principal.id,{'bundle':bundle,'digest':digest,
            'permission_diff':{'added':sorted(new-old),'removed':sorted(old-new)},
            'dependencies':self._dependencies(bundle),
            'previous_revision':installed['revision'] if installed else None,
            'expires_at':time()+600,'consumed':False})

    def _in_use(self,identifier):
        leases=[]
        for lease in self.store.list('extension_lease'):
            if lease['extension_id']!=identifier or lease.get('released'):
                continue
            if lease.get('dispatch_pending'):
                leases.append(lease)
                continue
            task=self.workspace.agents.get_task(lease['task_id'])
            if task and task['status'] in ACTIVE_STATUSES:
                leases.append(lease)
        return leases

    def install(self,principal,preview_id,digest):
        self.workspace.require(principal,'host-admin')
        preview=self.workspace.record(principal,'extension_preview',preview_id,scope='host-admin')
        with self.store.lock:
            preview=self.store.get('extension_preview',preview_id)
            if preview['consumed'] or preview['expires_at']<=time() or digest!=preview['digest']:
                raise Conflict('Extension review expired or already consumed')
            bundle=preview['bundle']; identifier=bundle['id']
            self.workspace.require(principal,'host-admin')
            if any(not item['satisfied'] for item in self._dependencies(bundle)):
                raise Conflict('Install exact reviewed dependencies before installing this package')
            provenance=preview.get('provenance',{})
            if provenance.get('kind')=='native-skill-source':
                source=self.workspace.state.skill_sources.read(principal,scope=provenance['scope'],project_id=provenance.get('project_id'),name=provenance.get('folder',identifier))
                if source['digest']!=provenance['source_digest']:
                    raise Conflict('Skill source changed; review its current version before publishing')
            if provenance.get('registry_id'):
                registry=self.workspace.record(principal,'registry',provenance['registry_id'],scope='host-admin')
                if registry['revision']!=provenance['registry_revision']:
                    raise Conflict('Registry or credential changed; review the package again')
            current=self.store.get('extension',identifier)
            if (current['revision'] if current else None)!=preview['previous_revision']:
                raise Conflict('Installed version changed; review permissions again')
            if self._in_use(identifier):
                raise Conflict('Extension is in use; stop its tasks before updating')
            target=self.root/identifier/digest
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not target.exists():
                temp=Path(tempfile.mkdtemp(prefix='.install-',dir=target.parent))
                try:
                    for name,content in bundle['files'].items():
                        path=temp/name
                        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                        path.write_text(content,encoding='utf-8'); path.chmod(0o600)
                    (temp/'manifest.json').write_text(json.dumps(bundle),encoding='utf-8')
                    (temp/'manifest.json').chmod(0o600)
                    os.replace(temp,target)
                finally:
                    if temp.exists():
                        shutil.rmtree(temp)
            version=self.store.get('extension_version',identifier+':'+digest)
            if not version:
                self.store.create('extension_version',principal.id,{'extension_id':identifier,'digest':digest,
                    'version':bundle['version'],'permissions':bundle['permissions'],'path':str(target)},identifier=identifier+':'+digest)
            body={'active_digest':digest,'version':bundle['version'],'permissions':bundle['permissions'],
                  'path':str(target),'enabled':True,'description':bundle.get('description',''),
                  'dependencies':bundle.get('dependencies',[]),'provenance':preview.get('provenance',{'kind':'portable-import'})}
            if current:
                row=self.store.update('extension',identifier,body,current['revision'])
            else:
                row=self.store.create('extension',principal.id,body,identifier=identifier)
            self.store.update('extension_preview',preview_id,{**preview,'consumed':True})
            self.store.log(principal.id,'extension',identifier,'activated',{'digest':digest,'version':bundle['version']})
            return row

    def rollback_preview(self,principal,identifier,digest):
        self.workspace.require(principal,'host-admin')
        version=self.store.get('extension_version',identifier+':'+digest)
        if not version:
            raise KeyError(digest)
        bundle=json.loads((Path(version['path'])/'manifest.json').read_text())
        if hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()!=digest:
            raise Conflict('Stored extension changed on disk')
        return self.preview(principal,bundle)

    def remove(self,principal,identifier):
        self.workspace.require(principal,'host-admin')
        with self.store.lock:
            if self._in_use(identifier):
                raise Conflict('Extension is in use; task leases prevent removal')
            if not self.store.get('extension',identifier):
                raise KeyError(identifier)
            self.store.delete('extension',identifier)
            self.store.log(principal.id,'extension',identifier,'removed')
        # Historical immutable files are kept for explicit rollback. No task
        # path disappears while it may still be referenced by the ledger.

    def acquire(self,principal,identifier,task_id):
        self.workspace.record(principal,'task',task_id,scope='agent-run')
        extension=self.store.get('extension',identifier)
        if not extension or not extension['enabled']:
            raise ValueError('Extension is not enabled')
        for permission in extension['permissions']:
            task=self.store.get('task',task_id)
            self.workspace.require(principal,permission,task.get('project_id'),task.get('cwd'))
        return self.store.create('extension_lease',principal.id,{'extension_id':identifier,'task_id':task_id,
            'digest':extension['active_digest'],'released':False})

    def export(self,principal,identifier):
        self.workspace.require(principal,'host-admin')
        current=self.store.get('extension',identifier)
        if not current:
            raise KeyError(identifier)
        bundle=json.loads((Path(current['path'])/'manifest.json').read_text())
        bundle=self.normalize(bundle,'termx-bundle/v1')
        if hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()!=current['active_digest']:
            raise Conflict('Stored bundle integrity check failed')
        return bundle

    def _dependencies(self,bundle):
        results=[]
        for dependency in bundle.get('dependencies',[]):
            installed=self.store.get('extension',dependency['id'])
            results.append({**dependency,'satisfied':bool(installed and installed['enabled'] and
                installed['version']==dependency['version'] and installed['active_digest']==dependency['digest'])})
        return results

    def _credentials(self):
        credentials=getattr(self.workspace.state,'credentials',None)
        if credentials is None:
            credentials=getattr(getattr(self.workspace.state,'agent',None),'credentials',None)
        return credentials

    def registry(self,principal,*,name,path=None,url=None,kind='private_local',credential=None):
        self.workspace.require(principal,'host-admin')
        identifier=uuid.uuid4().hex
        if kind=='private_local':
            if url or credential or not path:raise ValueError('Local registry requires only a folder')
            if Path(path).is_symlink():raise ValueError('Registry root cannot be a symlink')
            root=Path(path).resolve(strict=True)
            if not root.is_dir():raise ValueError('Private registry path must be a directory')
            body={'name':name[:200],'path':str(root),'kind':kind}
        elif kind in {'public_https','private_https'}:
            if path or not url:raise ValueError('HTTPS registry requires only an index URL')
            checked_url(url)
            if kind=='public_https' and credential:raise ValueError('Public registries do not use private credentials')
            if kind=='private_https' and not credential:raise ValueError('Private registry requires a host-held credential')
            body={'name':name[:200],'url':url,'kind':kind,'credential_configured':kind=='private_https'}
            if credential:
                if len(credential)>4096 or any(ord(c)<32 or ord(c)>126 for c in credential):raise ValueError('Invalid registry credential')
                credentials=self._credentials()
                if not credentials or not credentials.available():raise ValueError('Protected credential store is unavailable')
                reference='termx-registry:'+principal.id+':'+identifier
                credentials.set(reference,credential)
                body['credential_ref']=reference
        else:raise ValueError('Unsupported registry adapter')
        try:return self.public_registry(self.store.create('registry',principal.id,body,identifier=identifier))
        except BaseException:
            if body.get('credential_ref'):self._credentials().delete(body['credential_ref'])
            raise

    @staticmethod
    def public_registry(row):
        return {k:v for k,v in row.items() if k!='credential_ref'}

    def rotate_registry_credential(self,principal,identifier,credential,revision):
        registry=self.workspace.record(principal,'registry',identifier,scope='host-admin')
        if registry['kind']!='private_https':raise ValueError('Registry does not use private authentication')
        if not credential or len(credential)>4096 or any(ord(c)<32 or ord(c)>126 for c in credential):raise ValueError('Invalid registry credential')
        with self.store.lock:
            current=self.store.get('registry',identifier)
            if current['revision']!=revision:raise Conflict('Registry changed; reload before rotating credentials')
            credentials=self._credentials()
            if not credentials or not credentials.available():raise ValueError('Protected credential store is unavailable')
            credentials.set(current['credential_ref'],credential)
            return self.public_registry(self.store.update('registry',identifier,current,revision))

    def _catalog(self,principal,registry):
        credential=None
        if registry['kind']=='private_https':
            credential=self._credentials().get(registry['credential_ref']) if self._credentials() else None
            if not credential:raise ValueError('Registry credential is unavailable; rotate it in the manager')
        catalog,provenance=self.transport.fetch(registry['url'],credential=credential)
        if catalog.get('format')!='termx-registry/v1' or set(catalog)-{'format','packages'}:
            raise ValueError('Unsupported registry index format')
        packages=catalog.get('packages')
        if not isinstance(packages,list) or len(packages)>500:raise ValueError('Registry index exceeds 500 packages')
        seen=set();normalized=[]
        for package in packages:
            if (not isinstance(package,dict) or set(package)-{'id','version','url','sha256','format','permissions','dependencies'}
                    or not isinstance(package.get('id'),str) or not isinstance(package.get('version'),str) or not isinstance(package.get('sha256'),str)
                    or not SLUG.fullmatch(package['id']) or not SLUG.fullmatch(package['version'])
                    or not re.fullmatch('[0-9a-f]{64}',package.get('sha256',''))
                    or not isinstance(package.get('url'),str) or not package['url']
                    or package.get('format','termx-bundle/v1') not in self.adapters):raise ValueError('Invalid registry package metadata')
            # Validate optional metadata before exposing it to any UI or review.
            declaration=self.normalize({'format':'termx-bundle/v1','id':package['id'],'version':package['version'],
                'files':{'instructions/catalog.txt':'Catalog metadata only'},'permissions':package.get('permissions',[]),
                'dependencies':package.get('dependencies',[])},'termx-bundle/v1')
            if 'permissions' in package:package={**package,'permissions':declaration['permissions']}
            key=package['id']+'@'+package['version']
            if key in seen:raise ValueError('Registry contains duplicate package versions')
            seen.add(key)
            target=urljoin(registry['url'],package.get('url',''))
            if checked_url(target)[1]!=checked_url(registry['url'])[1]:raise ValueError('Registry package changed origin')
            normalized.append({**package,'url':target,'filename':key,'name':package['id']})
        self.workspace.record(principal,'registry',registry['id'],scope='host-admin')
        return normalized,provenance,credential

    def registry_packages(self,principal,identifier):
        registry=self.workspace.record(principal,'registry',identifier,scope='host-admin')
        if registry['kind']!='private_local':return self._catalog(principal,registry)[0]
        root=Path(registry['path']); packages=[]
        if root.is_symlink():raise ValueError('Registry root became a link')
        for child in sorted(root.glob('*.json'))[:500]:
            if child.is_symlink() or child.stat().st_size>2_100_000:continue
            try:
                bundle=self.normalize(json.loads(child.read_text()),'termx-bundle/v1')
                packages.append({'filename':child.name,'name':child.name,'id':bundle['id'],'version':bundle['version'],
                                 'permissions':bundle['permissions'],'dependencies':bundle.get('dependencies',[])})
            except (ValueError,UnicodeError,OSError):continue
        return packages

    def registry_preview(self,principal,identifier,filename):
        registry=self.workspace.record(principal,'registry',identifier,scope='host-admin')
        if registry['kind']=='private_local':
            if Path(filename).name!=filename or not filename.endswith('.json'):raise ValueError('Registry package must be a JSON filename')
            path=Path(registry['path'])/filename
            if path.is_symlink() or not path.is_file() or path.stat().st_size>2_100_000:raise ValueError('Unsafe registry package')
            preview=self.preview(principal,json.loads(path.read_text()))
            provenance={'kind':'private-local-registry','registry_id':identifier,'registry_revision':registry['revision'],'filename':filename}
        else:
            packages,index_provenance,credential=self._catalog(principal,registry)
            package=next((row for row in packages if row['filename']==filename),None)
            if not package:raise KeyError(filename)
            data,download=self.transport.fetch(package['url'],origin_url=registry['url'],credential=credential)
            if download['sha256']!=package['sha256']:raise Conflict('Registry package digest changed; download was not installed')
            bundle=self.normalize(data,package.get('format','termx-bundle/v1'))
            if bundle['id']!=package['id'] or bundle['version']!=package['version']:raise ValueError('Package identity differs from registry index')
            for field in ('permissions','dependencies'):
                if field in package and package[field]!=bundle.get(field,[]):raise ValueError('Package '+field+' differs from registry review metadata')
            self.workspace.record(principal,'registry',identifier,scope='host-admin')
            preview=self.preview(principal,bundle)
            provenance={'kind':registry['kind'],'registry_id':identifier,'registry_revision':registry['revision'],
                        'index':index_provenance,'package':download,'authentication':'host-credential-reference' if credential else 'public'}
        return self.store.update('extension_preview',preview['id'],{**preview,'provenance':provenance})

    def execution_context(self,principal,identifiers,project_id,cwd,resource_id=None):
        contexts=[];pending=list(identifiers);seen=set()
        while pending:
            identifier=pending.pop(0)
            if identifier in seen:continue
            seen.add(identifier)
            if len(seen)>100:raise ValueError('Selected extension dependency graph exceeds 100 bundles')
            extension=self.store.get('extension',identifier)
            if not extension or not extension['enabled']:
                raise ValueError('Selected extension is disabled or removed')
            for permission in extension['permissions']:
                self.workspace.require(principal,permission,project_id,cwd,resource_kind='conversation' if resource_id else None,resource_id=resource_id)
            bundle=self.normalize(json.loads((Path(extension['path'])/'manifest.json').read_text()),'termx-bundle/v1')
            if hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()!=extension['active_digest']:
                raise Conflict('Installed extension integrity check failed')
            if any(not item['satisfied'] for item in self._dependencies(bundle)):
                raise Conflict('Selected extension dependency changed; review installed versions')
            pending.extend(item['id'] for item in bundle.get('dependencies',[]))
            files=[{'path':name,'content':content} for name,content in bundle['files'].items()
                   if name.startswith(('skills/','instructions/','agents/'))]
            contexts.append({'id':identifier,'version':extension['version'],'digest':extension['active_digest'],'instructions':files})
        if len(json.dumps(contexts).encode())>256_000:
            raise ValueError('Selected skills exceed the conversation context budget')
        return contexts

    def hold_dispatch(self,principal,contexts,key):
        leases=[]
        with self.store.lock:
            for context in contexts:
                current=self.store.get('extension',context['id'])
                if not current or current['active_digest']!=context['digest']:
                    raise Conflict('Selected extension changed before dispatch; review the new version')
            for context in contexts:
                leases.append(self.store.create('extension_lease',principal.id,{'extension_id':context['id'],
                    'task_id':None,'digest':context['digest'],'request_id':key,'dispatch_pending':True,'released':False}))
        return leases

    def finish_dispatch(self,principal,key,task_id):
        with self.store.lock:
            for lease in self.store.list('extension_lease',principal.id):
                if lease.get('request_id')==key and lease.get('dispatch_pending'):
                    self.store.update('extension_lease',lease['id'],{**lease,'task_id':task_id,'dispatch_pending':False})

    def abort_dispatch(self,principal,key):
        with self.store.lock:
            for lease in self.store.list('extension_lease',principal.id):
                if lease.get('request_id')==key and lease.get('dispatch_pending'):
                    self.store.update('extension_lease',lease['id'],{**lease,'dispatch_pending':False,'released':True})

    def active_roots(self):
        """Discovery adapter: expose only activated immutable version roots."""
        return [row['path'] for row in self.store.list('extension') if row['enabled']]
