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
        self.adapters={'termx-bundle/v1':lambda data:data,'skill-markdown/v1':self._skill}

    def register_adapter(self,name,adapter):
        if not name or name in self.adapters:
            raise ValueError('Compatibility adapter name must be unique')
        self.adapters[name]=adapter

    @staticmethod
    def _skill(data):
        return {'format':'termx-bundle/v1','id':data['id'],'version':data['version'],
                'permissions':data.get('permissions',['files-read']),
                'files':{'skills/'+data['id']+'/SKILL.md':data['markdown']}}

    def normalize(self,data,format):
        if format not in self.adapters:
            raise ValueError('Unsupported extension format; a trusted compatibility adapter is required')
        bundle=self.adapters[format](data)
        if set(bundle)-{'format','id','version','permissions','files','description'} or bundle.get('format')!='termx-bundle/v1':
            raise ValueError('Invalid bundle format')
        if not SLUG.fullmatch(bundle.get('id','')) or not SLUG.fullmatch(bundle.get('version','')):
            raise ValueError('Bundle ID and version must be safe slugs')
        permissions=set(bundle.get('permissions',[]))
        if not permissions <= {'files-read','files-write','git-read','git-write','agent-run','network-manage'}:
            raise ValueError('Extension requests forbidden host/authentication permissions')
        files=bundle.get('files')
        if not isinstance(files,dict) or not files or len(files)>200:
            raise ValueError('Bundle needs one to 200 text files')
        total=0
        for name,content in files.items():
            path=PurePosixPath(name)
            if '\\' in name or path.is_absolute() or any(part in {'..','.',''} for part in name.split('/')) or path.parts[0] not in {'skills','agents','hooks','mcp','instructions'}:
                raise ValueError('Unsafe or unsupported bundle path')
            if any(part.startswith('.env') or part in {'.ssh','.aws','credentials.json'} for part in path.parts):
                raise ValueError('Credential files cannot be installed/exported')
            if not isinstance(content,str) or redact(content)!=content or SECRETS.search(content):
                raise ValueError('Bundle must not contain embedded credentials')
            total+=len(content.encode())
        if total>2_000_000:
            raise ValueError('Extension bundle exceeds two megabytes')
        return {**bundle,'permissions':sorted(permissions)}

    def preview(self,principal,data,format='termx-bundle/v1'):
        self.workspace.require(principal,'host-admin')
        bundle=self.normalize(data,format)
        installed=self.store.get('extension',bundle['id'])
        old=set((installed or {}).get('permissions',[])); new=set(bundle['permissions'])
        digest=hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()
        return self.store.create('extension_preview',principal.id,{'bundle':bundle,'digest':digest,
            'permission_diff':{'added':sorted(new-old),'removed':sorted(old-new)},
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
                  'path':str(target),'enabled':True,'description':bundle.get('description','')}
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

    def registry(self,principal,*,name,path):
        self.workspace.require(principal,'host-admin')
        root=Path(path).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Private registry path must be a directory')
        return self.store.create('registry',principal.id,{'name':name[:200],'path':str(root),'kind':'private_local'})

    def registry_packages(self,principal,identifier):
        registry=self.workspace.record(principal,'registry',identifier,scope='host-admin')
        root=Path(registry['path']); packages=[]
        for child in sorted(root.glob('*.json'))[:500]:
            if child.is_symlink() or child.stat().st_size>2_100_000:
                continue
            try:
                bundle=self.normalize(json.loads(child.read_text()),'termx-bundle/v1')
                packages.append({'name':child.name,'id':bundle['id'],'version':bundle['version'],
                                 'permissions':bundle['permissions']})
            except (ValueError,UnicodeError,OSError):
                continue
        return packages

    def registry_preview(self,principal,identifier,filename):
        registry=self.workspace.record(principal,'registry',identifier,scope='host-admin')
        if Path(filename).name!=filename or not filename.endswith('.json'):
            raise ValueError('Registry package must be a JSON filename')
        path=Path(registry['path'])/filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size>2_100_000:
            raise ValueError('Unsafe registry package')
        return self.preview(principal,json.loads(path.read_text()))

    def execution_context(self,principal,identifiers,project_id,cwd,resource_id=None):
        contexts=[]
        for identifier in identifiers:
            extension=self.store.get('extension',identifier)
            if not extension or not extension['enabled']:
                raise ValueError('Selected extension is disabled or removed')
            for permission in extension['permissions']:
                self.workspace.require(principal,permission,project_id,cwd,resource_kind='conversation' if resource_id else None,resource_id=resource_id)
            bundle=self.normalize(json.loads((Path(extension['path'])/'manifest.json').read_text()),'termx-bundle/v1')
            if hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()!=extension['active_digest']:
                raise Conflict('Installed extension integrity check failed')
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
