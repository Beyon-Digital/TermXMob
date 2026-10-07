"""Principal-scoped durable chat, inspectable memory, and lifecycle hooks.

Engine state remains in AgentStore/EngineGateway. Cross-engine handoffs copy
only reviewed public context; they never copy native IDs or provider secrets.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shlex
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

from termx.agent.execution import run_shell
from termx.agent.limits import resolve_limits
from termx.agent.policy import redact
from termx.agent.store import ACTIVE_STATUSES
from termx.config import config_dir
from termx.identity import Principal
from termx.workspace.store import Conflict, WorkspaceStore


class WorkspaceService:
    def __init__(self, state, path: Path | None = None, *, project_check=None, hook_runner=None):
        self.state = state
        self.store = WorkspaceStore(path or config_dir() / 'workspace.sqlite3')
        self.agents = state.agent_store
        self.project_check = project_check
        self.hook_runner = hook_runner
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._workers: set[asyncio.Task] = set()
        self._closed = False
        state.agent._listeners.add(self._on_event)

    def require(self, principal: Principal, scope: str, project_id: str | None = None, cwd: str | None = None, *, resource_kind=None, resource_id=None):
        live = self.state.identity.principal_by_id(principal.id) if hasattr(self.state, 'identity') else principal
        if not live or scope not in live.scopes:
            raise PermissionError('Permission denied')
        authz = getattr(self.state, 'authorization', None)
        if authz:
            if not project_id and not cwd and scope in {'agent-view','agent-control'}:
                authz.require_creation_principal(live, scope)
            else:
                authz.require_principal(live, scope, project_id=project_id,resource_kind=resource_kind,resource_id=resource_id)
        principal = live
        if project_id is not None:
            if cwd and getattr(self.state, 'projects', None):
                project = next((p for p in self.state.projects.projects() if p['id'] == project_id), None)
                if not project:
                    raise PermissionError('Authorized project is unavailable')
                inside=Path(cwd).resolve().is_relative_to(Path(project['path']).resolve())
                bound=self.store.get(resource_kind,resource_id) if resource_kind in {'conversation','task'} and resource_id else None
                selected=self.worktree_target(project_id,bound.get('worktree_id')) if bound and bound.get('worktree_id') else None
                if selected and bound.get('worktree_digest')!=selected['digest']:
                    raise PermissionError('Enrolled worktree identity changed; renew the session target')
                tracked=self.agents.task_worktree(resource_id) if resource_kind=='task' and resource_id else None
                if not inside and not (selected and Path(cwd).resolve().is_relative_to(Path(selected['path']))) and not (tracked and tracked.get('status') in {'active','kept'} and Path(tracked['base_repo']).resolve()==Path(project['path']).resolve() and Path(tracked['worktree_path']).resolve()==Path(cwd).resolve()):
                    raise PermissionError('Folder is outside the authorized project or tracked task worktree')
            if authz:
                return
            if self.project_check:
                self.project_check(principal, scope, project_id, cwd)
            else:
                # Until a project directory is installed, only host administrators
                # can establish an execution boundary; never accept a client path
                # for a scoped user merely because agent-run exists globally.
                if 'host-admin' not in principal.scopes:
                    raise PermissionError('Project authorization is not configured')
        elif cwd and 'host-admin' not in principal.scopes:
            managed=self.scratch_root(principal.id)
            if not (authz and resource_kind in {'conversation','task'} and resource_id and Path(cwd).resolve().is_relative_to(managed)):
                raise PermissionError('Scratch execution requires an owned managed resource or project grant')

    def scratch_root(self,owner):
        return (self.store.path.parent/'scratch'/hashlib.sha256(owner.encode()).hexdigest()).resolve()

    def record(self, principal: Principal, kind: str, identifier: str, *, scope='agent-view'):
        row = self.store.get(kind, identifier)
        if row is None or row['owner'] != principal.id:
            raise KeyError(identifier)
        self.require(principal, scope, row.get('project_id'), None if row.get('worktree_id') and scope in {'agent-view','agent-control'} else row.get('cwd'),
                     resource_kind=kind if kind in {'conversation','task'} else None,
                     resource_id=identifier if kind in {'conversation','task'} else None)
        return row

    def assert_resource(self, owner: str, kind: str, identifier: str):
        """Ownership helper for legacy route integration. No implicit adoption."""
        row = self.store.get(kind, identifier)
        if not row or row['owner'] != owner:
            raise PermissionError('Resource belongs to another user or is not enrolled')
        return row

    @staticmethod
    def execution_target(row):
        """Delegations bind settings explicitly; later edits require fresh consent."""
        return {key:row.get(key) for key in ('engine','provider_id','model','mode','workflow','extension_ids','runner_id','runner_credential_ref','project_id','cwd','worktree_id','worktree_digest','worktree_branch','run_limits')}

    def worktree_target(self,project_id,identifier):
        """Resolve enrolled checkout IDs; caller paths never establish authority."""
        if not isinstance(identifier,str) or not identifier or len(identifier)>200 or not project_id:
            raise ValueError('Choose an enrolled worktree for this execution project')
        projects=getattr(self.state,'projects',None);delivery=getattr(self.state,'delivery',None)
        if not projects or not delivery:
            raise ValueError('Git worktree service is unavailable')
        project=projects.project(project_id)
        root=Path(project['path']).resolve(strict=True)
        row=next((item for item in delivery.worktrees(project_id) if item['id']==identifier),None)
        if not row or Path(row['root']).resolve()!=root:
            raise PermissionError('Worktree is not enrolled for this project')
        target=Path(row['path']).resolve(strict=True)
        from termx import git_ops
        def common(folder):
            value=git_ops._require_ok(git_ops._git(str(folder),'rev-parse','--git-common-dir')).strip()
            return (folder/value).resolve(strict=True)
        if not target.is_dir() or common(target)!=common(root):
            raise PermissionError('Worktree no longer belongs to the enrolled repository')
        listed=git_ops._require_ok(git_ops._git(str(root),'worktree','list','--porcelain'))
        live={Path(line[9:]).resolve() for line in listed.splitlines() if line.startswith('worktree ')}
        if target not in live:
            raise PermissionError('Worktree was removed from the repository')
        digest=hashlib.sha256(json.dumps({'id':identifier,'project':project_id,'root':str(root),'path':str(target),'common':str(common(root))},sort_keys=True).encode()).hexdigest()
        branch=git_ops._require_ok(git_ops._git(str(target),'rev-parse','--abbrev-ref','HEAD')).strip()
        return {**row,'path':str(target),'digest':digest,'branch':branch}

    def session_files(self,principal,identifier,scope='files-read',expected_worktree=None):
        row=self.record(principal,'conversation',identifier,scope=scope)
        if row.get('runner_id'):
            raise Conflict('Cloud files belong to the uploaded runner workspace; this editor targets local checkouts')
        if expected_worktree!=row.get('worktree_id'):
            raise Conflict('Execution checkout changed. Return to the original checkout before saving this buffer.')
        from termx.project_files import ProjectFiles
        root=row['cwd'];project=self.state.projects.project(row['project_id'])
        class BoundFiles(ProjectFiles):
            def project(self,project_id):
                if project_id!=project['id']:
                    raise PermissionError('Wrong execution project')
                return {**project,'path':root}
        files=object.__new__(BoundFiles);files.lock=self.state.projects.lock
        return files,row['project_id']

    def validate_runner(self,principal,row):
        identifier=row.get('runner_id')
        reference=row.get('runner_credential_ref')
        if any(value is not None and (not isinstance(value,str) or len(value)>200) for value in (identifier,reference)):
            raise ValueError('Runner and account references must be bounded IDs')
        if not identifier:
            if reference:
                raise ValueError('A runner credential reference needs a selected runner')
            return
        if row.get('worktree_id'):
            raise ValueError('Choose the project checkout before cloud execution; a local worktree is not the uploaded runner snapshot')
        if row.get('engine')!='internal' or row.get('workflow'):
            raise ValueError('Dedicated cloud execution currently requires the TermX internal agent')
        if not reference or not row.get('provider_id') or not row.get('model'):
            raise ValueError('Cloud execution requires an explicit provider, credential reference and model')
        if reference!=row['provider_id']:
            raise ValueError('Select the configured account reference for this provider')
        runners=getattr(self.state,'runners',None)
        if not runners:
            raise ValueError('Runner service is unavailable')
        runner=runners.row(principal.id,identifier)
        if runner['status']!='ready' or runner['expires']<=time() or runner['project']!=(row.get('project_id') or ''):
            raise PermissionError('Runner must be ready and scoped to this execution project')
        if runner['configuration'].get('network','none')!='none':
            raise PermissionError('Reviewed cloud agents require the isolated runner network policy')

    def create_session(self, principal, *, title='', project_id=None, cwd=None,
                       engine='internal', provider_id=None, model=None, mode='ask', workflow=None,runner_id=None,runner_credential_ref=None,run_limits=None,worktree_id=None):
        self.require(principal, 'agent-control', project_id, None if worktree_id else cwd)
        target=self.worktree_target(project_id,worktree_id) if worktree_id else None
        if target:
            if cwd and Path(cwd).resolve()!=Path(target['path']):
                raise ValueError('Selected worktree supplies the execution folder')
            cwd=target['path']
        run_limits=resolve_limits(run_limits)
        if engine != 'internal' and engine not in self.state.engines.engines():
            raise ValueError('Engine is not installed')
        if mode not in {'ask', 'agent'}:
            raise ValueError('Unknown conversation mode')
        if workflow not in {None, 'browser'} or (workflow == 'browser' and engine != 'claude'):
            raise ValueError('Browser workflow requires a dedicated Claude conversation')
        self.validate_runner(principal,{'engine':engine,'workflow':workflow,'runner_id':runner_id,'runner_credential_ref':runner_credential_ref,'provider_id':provider_id,'model':model,'project_id':project_id,'worktree_id':worktree_id})
        scratch=not project_id and not cwd
        if scratch:
            folder=self.scratch_root(principal.id)/uuid4().hex
            folder.mkdir(parents=True,mode=0o700)
            folder.parent.chmod(0o700)
            cwd=str(folder)
        else:
            cwd = str(Path(cwd).resolve(strict=True)) if cwd else None
        conversation = self.agents.create_conversation(title=title[:200], project_id=project_id,
                                                      cwd=cwd, provider_id=provider_id, model=model, mode=mode)
        row = self.store.create('conversation', principal.id,
                               {'engine': engine, 'cwd': cwd, 'draft_text': '', 'scroll': 0,
                                'linked_from': None, 'transfer': None, 'extension_ids': [], 'workflow': workflow, 'group_id': project_id, 'scratch':scratch,'runner_id':runner_id,'runner_credential_ref':runner_credential_ref,'worktree_id':worktree_id,'worktree_digest':target['digest'] if target else None,'run_limits':resolve_limits(run_limits)}, project_id, conversation['id'])
        if getattr(self.state, 'authorization', None):
            self.state.authorization.claim_principal(principal, 'conversation', row['id'], project_id=project_id)
        self.store.log(principal.id, 'conversation', row['id'], 'created', {'engine': engine})
        return self.session(principal, row['id'])

    def session(self, principal, identifier, *, turns=True):
        row = self.record(principal, 'conversation', identifier)
        conversation = self.agents.get_conversation(identifier, include_turns=turns)
        if not conversation:
            raise KeyError(identifier)
        if turns:
            for turn in conversation.get('turns', []):
                task = self.agents.get_task(turn['task_id']) if turn.get('task_id') else None
                if task:
                    public_fields = {'id','prompt','cwd','provider_id','model','status','limits','mode','parent_id','plan','result','error','engine','created_at','updated_at'}
                    task = {k:v for k,v in task.items() if k in public_fields}
                    task_meta=self.store.get('task',task['id']) or {}
                    task['execution_location']=task_meta.get('execution_location') or task.get('cwd')
                    if task['status'] in ACTIVE_STATUSES:
                        task['events'] = self.agents.events(task['id'],tail_limit=200)
                        task['approvals'] = self.agents.approvals(task['id'])
                turn['task'] = task
        if row.get('worktree_id'):
            try:
                row['worktree_branch']=self.worktree_target(row['project_id'],row['worktree_id'])['branch']
                row['worktree_unavailable']=None
            except (PermissionError,OSError,ValueError):
                row['worktree_branch']=None
                row['worktree_unavailable']='Selected worktree is unavailable; choose an enrolled checkout before running or editing'
        latest=self.agents.workspace_turns_page(identifier,descending=True,limit=1)
        last_task=self.agents.get_task(latest[0]['task_id']) if latest and latest[0].get('task_id') else None
        return {**row, **conversation, 'revision': row['revision'],'latest_status':(last_task or {}).get('status'),
                'updated_at': max(row['updated_at'], conversation['updated_at'],(last_task or {}).get('updated_at',0))}

    def sessions(self, principal, *, query='', archived=False, project_id=None, limit=500):
        self.require(principal, 'agent-view', project_id)
        rows = []
        metadata=self.store.list('conversation',principal.id,project_id)
        authz=getattr(self.state,'authorization',None)
        if authz and hasattr(authz,'resource_snapshot'):
            # One live policy snapshot is used only within this collection read.
            # It includes each resource's enrolled owner/project boundary.
            snapshots=[authz.resource_snapshot(principal,'agent-view','conversation',[meta['id'] for meta in metadata[offset:offset+5000]]) for offset in range(0,max(1,len(metadata)),5000)]
            snapshot=snapshots[0]
            if any(item.policy_version!=snapshot.policy_version or item.administrator!=snapshot.administrator for item in snapshots):
                raise PermissionError('Collection authority changed while reading; retry the list')
            project_service=getattr(self.state,'projects',None)
            projects={project['id']:Path(project['path']).resolve() for project in project_service.projects()} if project_service else {}
            boundaries={identifier:project for item in snapshots for identifier,project in item.resources}
            allowed=frozenset(boundaries)
            enrolled_by_project={}
            selected=[]
            for meta in metadata:
                if meta['owner']!=principal.id or meta['id'] not in allowed:continue
                if not snapshot.administrator and meta.get('project_id')!=boundaries.get(meta['id']):continue
                root=projects.get(meta.get('project_id'))
                if meta.get('project_id'):
                    if root is None:continue
                    # An enrolled checkout may be unavailable and still needs
                    # to remain inspectable for target recovery. Its recorded
                    # identity must match the host's same-project enrollment.
                    if meta.get('worktree_id'):
                        delivery=getattr(self.state,'delivery',None)
                        if meta['project_id'] not in enrolled_by_project:
                            enrolled_by_project[meta['project_id']]={item['id']:item for item in delivery.worktrees(meta['project_id'])} if delivery else {}
                        enrolled=enrolled_by_project[meta['project_id']].get(meta['worktree_id'])
                        if enrolled and (Path(enrolled['root']).resolve()!=root or Path(enrolled['path']).resolve()!=Path(meta['cwd']).resolve()):continue
                        if not enrolled:
                            # Historical host-owned metadata stays discoverable
                            # after removal; all execution/file access still
                            # requires the live enrolled target in require().
                            meta={**meta,'worktree_unavailable':'Selected worktree enrollment is unavailable; choose another checkout before running or editing'}
                    elif meta.get('cwd') and not Path(meta['cwd']).resolve().is_relative_to(root):continue
                elif meta.get('cwd') and not snapshot.administrator and not Path(meta['cwd']).resolve().is_relative_to(self.scratch_root(principal.id)):continue
                selected.append(meta)
            canonical={}
            for offset in range(0,len(selected),500):
                canonical.update({row['id']:row for row in self.agents.workspace_conversations_snapshot([meta['id'] for meta in selected[offset:offset+500]])})
            for meta in selected:
                conversation=canonical.get(meta['id'])
                if not conversation:continue
                if conversation.get('project_id')!=meta.get('project_id') or conversation.get('cwd')!=meta.get('cwd'):
                    continue
                row={**meta,**conversation,'revision':meta['revision'],'updated_at':max(meta['updated_at'],conversation['updated_at'],conversation['latest_task_updated'])}
                row.pop('latest_task_updated',None)
                if bool(row['archived'])!=archived or query.casefold() not in row['title'].casefold():continue
                rows.append(row)
            for item in snapshots:authz.validate_resource_snapshot(item)
        else:
            for meta in metadata:
                try:
                    row = self.session(principal, meta['id'], turns=False)
                except (KeyError, PermissionError):
                    continue
                if bool(row['archived']) != archived or query.casefold() not in row['title'].casefold():
                    continue
                rows.append(row)
        rows.sort(key=lambda r: (not r['pinned'], -r['updated_at']))
        return rows[:max(1,min(limit,500))]

    def active(self, identifier):
        return self.agents.active_conversation_tasks(identifier)

    def update_session(self, principal, identifier, *, revision, changes):
        row = self.record(principal, 'conversation', identifier, scope='agent-control')
        allowed = {'title','pinned','archived','draft_text','draft_context','scroll','model','provider_id','mode','engine','extension_ids','workflow','group_id','runner_id','runner_credential_ref','run_limits','worktree_id'}
        if changes.keys() - allowed:
            raise ValueError('Unsupported session setting')
        if 'draft_context' in changes:
            self.validate_context(changes['draft_context'])
        if 'worktree_id' in changes:
            if row.get('runner_id'):
                raise Conflict('Select local execution before choosing a host worktree')
            if row['engine']!='internal' and self.agents.workspace_turns_page(identifier,limit=1):
                raise Conflict('Native conversations keep their original checkout; create a new conversation for another worktree')
            if not row.get('project_id'):
                raise ValueError('Worktrees require an execution project')
            target=self.worktree_target(row['project_id'],changes['worktree_id']) if changes['worktree_id'] else None
            changes['cwd']=target['path'] if target else self.state.projects.project(row['project_id'])['path']
            changes['worktree_digest']=target['digest'] if target else None
        if 'run_limits' in changes:
            changes['run_limits']=resolve_limits(changes['run_limits'])
        if 'group_id' in changes:
            if changes['group_id'] is not None and not isinstance(changes['group_id'],str):
                raise ValueError('Group must be a project ID or unassigned')
            if changes['group_id']:
                self.require(principal,'agent-view',changes['group_id'])
        if 'workflow' in changes:
            if changes['workflow'] not in {None, 'browser'} or (changes['workflow']=='browser' and row['engine']!='claude'):
                raise ValueError('Browser workflow requires a dedicated Claude conversation')
            if self.agents.workspace_turns_page(identifier,limit=1):
                raise Conflict('Create a new conversation to change its tool workflow')
        if 'extension_ids' in changes:
            if not isinstance(changes['extension_ids'],list) or len(changes['extension_ids'])>20 or any(not isinstance(item,str) for item in changes['extension_ids']):
                raise ValueError('Select at most 20 installed extensions')
            for item in changes['extension_ids']:
                if not getattr(self.state,'extensions',None) or not self.state.extensions.store.get('extension',item):
                    raise ValueError('Selected extension is not installed')
        if changes.keys() & {'engine','provider_id','model','workflow','runner_id','runner_credential_ref'}:
            self.validate_runner(principal,{**self.session(principal,identifier,turns=False),**changes})
        for key, value in changes.items():
            if key in {'pinned','archived'} and not isinstance(value,bool):
                raise ValueError('Session flags must be booleans')
            if key in {'title','draft_text','model','provider_id','mode','engine'} and not (value is None and key in {'model','provider_id'}) and not isinstance(value,str):
                raise ValueError('Session text settings must be strings')
            if key == 'scroll' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=100_000_000):
                raise ValueError('Invalid scroll position')
            if isinstance(value,str) and len(value)> (64000 if key=='draft_text' else 200):
                raise ValueError('Session setting exceeds its size limit')
        if changes.get('engine', row['engine']) != row['engine']:
            raise Conflict('Change engines using a reviewed linked fork')
        if changes.keys() & {'model','provider_id','mode','engine','extension_ids','workflow','runner_id','runner_credential_ref','run_limits','worktree_id'} and (self.active(identifier) or (self._send_locks.get(identifier) and self._send_locks[identifier].locked())):
            raise Conflict('Wait for the active turn before changing session settings')
        if 'mode' in changes and changes['mode'] not in {'ask','agent'}:
            raise ValueError('Unknown mode')
        with self.store.lock:
            if row['revision'] != revision:
                raise Conflict('Session changed; reload before saving')
            for key in ('draft_text','draft_context','scroll','extension_ids','workflow','group_id','runner_id','runner_credential_ref','run_limits','worktree_id','worktree_digest','cwd'):
                if key in changes:
                    row[key] = changes[key]
            meta = self.store.update('conversation', identifier, row, revision)
            self.agents.update_conversation(identifier, **{k:v for k,v in changes.items() if k not in {'draft_text','draft_context','scroll','engine','extension_ids','workflow','group_id','runner_id','runner_credential_ref','run_limits','worktree_id','worktree_digest'}})
        self.store.log(principal.id, 'conversation', identifier, 'updated', {'fields':sorted(changes)})
        return self.session(principal, meta['id'])

    def fork_preview(self, principal, identifier, *, engine, turn_ids, files=None, summary=''):
        source = self.session(principal, identifier)
        self.record(principal,'conversation',identifier,scope='agent-run')
        if engine == source['engine']:
            raise ValueError('Choose a different engine for a cross-engine handoff')
        if engine != 'internal' and engine not in self.state.engines.engines():
            raise ValueError('Engine is not installed')
        chosen = set(turn_ids)
        if not chosen <= {t['id'] for t in source['turns']}:
            raise ValueError('Unknown source turn')
        messages = []
        for turn in source['turns']:
            if turn['id'] in chosen:
                if redact(turn['prompt']) != turn['prompt']:
                    raise ValueError('Remove credentials from selected messages before transfer')
                messages.append({'role':'user','content':turn['prompt'], 'turn_id':turn['id']})
                result = (turn.get('task') or {}).get('result')
                if result:
                    messages.append({'role':'assistant','content':redact(str(result)), 'turn_id':turn['id']})
        safe_files = []
        for filename in files or []:
            if not source.get('cwd'):
                raise ValueError('Files require a project folder')
            root = Path(source['cwd']).resolve()
            target = (root / filename).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > 256_000:
                raise ValueError('Selected file is outside the project or too large')
            if any(part.startswith('.env') or part in {'.ssh','.aws','.git'} for part in target.relative_to(root).parts):
                raise ValueError('Credentials and Git internals cannot be transferred')
            self.require(principal, 'files-read', source.get('project_id'), str(target))
            content = target.read_text(encoding='utf-8')
            if redact(content) != content:
                raise ValueError('Remove secrets before transferring selected files')
            safe_files.append({'path':str(target.relative_to(root)), 'content':content,
                               'sha256':hashlib.sha256(content.encode()).hexdigest()})
        if redact(summary) != summary:
            raise ValueError('Summary cannot transfer credentials')
        transfer = {'source_id':identifier,'source_revision':source['revision'],'source_updated_at':source['updated_at'],'engine':engine,
                    'messages':messages,'files':safe_files,'summary':summary,
                    'semantics':'new_session_with_reviewed_context',
                    'source_execution':{'runner_id':source.get('runner_id'),'cwd':'/workspace' if source.get('runner_id') else source.get('cwd')},
                    'target_execution':{'kind':'local','cwd':source.get('cwd'),'project_id':source.get('project_id')},
                    'excluded':['native_session','hidden_memory','credentials','native_resume']}
        encoded = json.dumps(transfer, sort_keys=True)
        if len(encoded.encode()) > 1_000_000:
            raise ValueError('Transfer exceeds the one megabyte context budget')
        preview = self.store.create('fork_preview', principal.id,
                                    {'transfer':transfer,'digest':hashlib.sha256(encoded.encode()).hexdigest(),
                                     'expires_at':time()+600,'consumed':False}, source.get('project_id'))
        return preview

    def commit_fork(self, principal, preview_id, *, digest, model=None, provider_id=None):
        preview = self.record(principal, 'fork_preview', preview_id, scope='agent-control')
        with self.store.lock:
            if preview['consumed'] or preview['expires_at'] <= time() or preview['digest'] != digest:
                raise Conflict('Preview expired, changed or already used')
            transfer = preview['transfer']
            source = self.session(principal, transfer['source_id'], turns=False)
            self.record(principal,'conversation',source['id'],scope='agent-run')
            if source['revision'] != transfer['source_revision'] or source['updated_at'] != transfer['source_updated_at']:
                raise Conflict('Source changed; review a fresh transfer')
            child = self.create_session(principal, title=source['title'], project_id=source.get('project_id'),
                                        cwd=None if source.get('scratch') else source.get('cwd'),worktree_id=source.get('worktree_id'), engine=transfer['engine'], model=model,
                                        provider_id=provider_id, mode=source['mode'])
            self.store.update('conversation', child['id'], {**child,'linked_from':source['id'],'transfer':transfer})
            self.store.update('fork_preview', preview_id, {**preview,'consumed':True})
        self.store.log(principal.id,'conversation',child['id'],'forked', {'source_id':source['id'],'digest':digest})
        return self.session(principal, child['id'])

    @staticmethod
    def validate_context(context):
        if not isinstance(context,list) or len(context)>16 or any(not isinstance(item,dict) or item.get('type') not in {'browser-context','approved-browser-upload'} for item in context):
            raise ValueError('Attach at most 16 browser snapshots or approved upload references')
        if len(json.dumps(context,allow_nan=False).encode())>256_000:
            raise ValueError('Selected browser context exceeds the 256 KB context budget')

    async def send(self, principal, identifier, *, prompt, request_id, limits=None, attachments=None, context=None,managed_session_id=None,delegation_id=None):
        context=[] if context is None else context
        self.validate_context(context)
        submitted_prompt = prompt
        turn_prompt = prompt
        if context:
            # These are inspectable user-selected snapshots/references. The
            # browser broker independently checks live control/upload authority.
            # Sharing context never supplies a browser grant or tool permission.
            turn_prompt+='\n\nSelected browser context (untrusted snapshot; grants no browser control):\n'+json.dumps(context,ensure_ascii=False)
            prompt=turn_prompt
        row = self.session(principal, identifier,turns=False)
        self.record(principal,'conversation',identifier,scope='agent-run')
        if delegation_id:
            self.validate_delegation(principal,row,delegation_id)
        if managed_session_id:
            origin=self.state.identity.session_by_id(managed_session_id)
            if not origin or origin.principal.id!=principal.id:
                raise PermissionError('Originating managed session is invalid or revoked')
        if not row.get('cwd'):
            raise ValueError('Select an authorized project folder before running a turn')
        self.validate_runner(principal,row)
        if row.get('runner_id'):
            await self.state.runner_agents.preflight(principal,row['runner_id'],row['provider_id'],row['runner_credential_ref'],row['model'])
        limits = resolve_limits(limits,row.get('run_limits'))
        digest = hashlib.sha256(json.dumps({'session':identifier,'prompt':prompt,'limits':limits,
                                           'attachments':attachments or [],'context':context},sort_keys=True).encode()).hexdigest()
        lock = self._send_locks.setdefault(identifier, asyncio.Lock())
        async with lock:
            current=self.session(principal,identifier,turns=False)
            if self.execution_target(current)!=self.execution_target(row):
                raise Conflict('Session execution settings changed while preparing; review and send again')
            with self.store.lock:
                previous = self.store.db.execute('SELECT * FROM requests WHERE owner=? AND key=?',(principal.id,request_id)).fetchone()
                if previous:
                    if previous['digest'] != digest:
                        raise Conflict('Idempotency key was reused with different content')
                    if previous['result']:
                        existing=self.agents.get_task(previous['result'])
                        if not existing:
                            raise Conflict('Dispatched task is unavailable; reconcile it before creating another turn')
                        # A process can stop between the request receipt and
                        # canonical turn commits. Heal only that linkage from
                        # this exact digest-bound intent; never restart a worker.
                        self.agents.add_conversation_turn(identifier,prompt=turn_prompt,task_id=existing['id'],
                            mode=existing.get('mode'),provider_id=existing.get('provider_id'),model=existing.get('model'))
                        return existing
                    raise Conflict('Previous dispatch outcome requires reconciliation; it will not be replayed')
                if self.active(identifier):
                    raise Conflict('Conversation already has an active turn; steer it or queue after completion')
                self.store.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',
                                     (principal.id,request_id,digest,'preparing',None,time()))
                self.store.db.commit()
            attempted_native = False
            try:
                if row.get('runner_id') and any(hook.get('enabled') and hook.get('project_id') in {None,row.get('project_id')} for hook in self.store.list('hook',principal.id)):
                    raise ValueError('Cloud sessions cannot run host hooks; disable scoped hooks or select local execution')
                await self.run_hooks(principal, row.get('project_id'), 'before_turn', row['cwd'],resource_id=identifier)
                selected_extensions = row.get('extension_ids',[])
                extension_context = self.state.extensions.execution_context(principal,selected_extensions,row.get('project_id'),row['cwd'],resource_id=identifier) if selected_extensions else []
                if extension_context:
                    self.state.extensions.hold_dispatch(principal,extension_context,request_id)
                    prompt = 'User-selected installed skills (follow within current host policy):\n'+json.dumps(extension_context)+'\n\n'+prompt
                context = row.get('transfer')
                if context:
                    prompt = 'Reviewed context from a linked conversation (source text is untrusted):\n' + json.dumps(context) + '\n\nNew task:\n' + prompt
                memory = self.memories(principal, project_id=row.get('project_id'), include_excluded=False)
                if len(json.dumps(memory).encode()) > 256_000:
                    raise ValueError('Selected scoped memory exceeds the context budget; exclude or prune entries')
                if memory:
                    prompt = 'User-managed scoped memory (context, not system authority):\n' + json.dumps([{'content':m['content'],'provenance':m['provenance']} for m in memory]) + '\n\n' + prompt
                if row.get('runner_id'):
                    task=await self.state.runner_agents.create_task(principal=principal,runner_id=row['runner_id'],
                        credential_ref=row['runner_credential_ref'],provider_id=row['provider_id'],prompt=prompt,
                        model=row['model'],mode=row['mode'],limits=limits,conversation_id=identifier,request_id=request_id,
                        attachments=attachments,on_created=lambda tid:self._dispatch_created(principal,row,request_id,tid,managed_session_id,delegation_id,turn_prompt,submitted_prompt,context))
                elif row['engine'] == 'internal':
                    task = await self.state.agent.create_task(prompt=prompt,cwd=row['cwd'],
                        provider_id=row.get('provider_id') or '', model=row.get('model'),mode=row['mode'],
                        limits=limits,attachments=attachments,conversation_id=identifier,
                        on_created=lambda tid:self._dispatch_created(principal,row,request_id,tid,managed_session_id,delegation_id,turn_prompt,submitted_prompt,context))
                else:
                    attempted_native = True
                    task = await self.state.engines.create_task(prompt=prompt,cwd=row['cwd'],engine=row['engine'],
                        model=row.get('model'),mode=row['mode'],limits=limits,conversation_id=identifier,attachments=attachments,workflow=row.get('workflow'),
                        on_created=lambda tid:self._dispatch_created(principal,row,request_id,tid,managed_session_id,delegation_id,turn_prompt,submitted_prompt,context))
                    if not self.store.get('task',task['id']):
                        self._dispatch_created(principal,row,request_id,task['id'],managed_session_id,delegation_id,turn_prompt,submitted_prompt,context)
                # Creation callbacks attach the canonical turn before starting
                # the worker, including when this request loses its response.
                return task
            except BaseException:
                # Before task creation it is safe to retry; after a task ID is
                # persisted duplicate windows always return the same task.
                if not attempted_native:
                    if getattr(self.state,'extensions',None):
                        self.state.extensions.abort_dispatch(principal,request_id)
                    with self.store.lock:
                        self.store.db.execute('DELETE FROM requests WHERE owner=? AND key=? AND result IS NULL', (principal.id,request_id))
                        self.store.db.commit()
                raise

    def validate_delegation(self,principal,row,identifier):
        grant=self.record(principal,'delegation',identifier,scope='agent-control')
        if grant['revoked'] or grant['expires_at']<=time() or grant['policy_version']!=principal.policy_version or grant.get('execution_target')!=self.execution_target(row):
            raise PermissionError('Delegated execution authority expired, changed or was revoked')
        goal=self.record(principal,'goal',grant['goal_id'])
        if goal['conversation_id']!=row['id'] or goal['status']!='active':
            raise PermissionError('Delegation does not authorize this conversation')
        return grant

    def _dispatch_created(self,principal,row,key,tid,managed_session_id=None,delegation_id=None,turn_prompt=None,submitted_prompt=None,submitted_context=None):
        if delegation_id:
            self.validate_delegation(principal,row,delegation_id)
        self.store.create('task',principal.id,{'conversation_id':row['id'],'cwd':row['cwd'],'runner_id':row.get('runner_id'),'delegation_id':delegation_id,'worktree_id':row.get('worktree_id'),'worktree_digest':row.get('worktree_digest'),'worktree_branch':row.get('worktree_branch'),'execution_location':'runner:'+row['runner_id']+'/workspace' if row.get('runner_id') else row['cwd']},row.get('project_id'),tid)
        if getattr(self.state, 'authorization', None):
            self.state.authorization.claim_principal(principal, 'task', tid, project_id=row.get('project_id'))
        with self.store.lock:
            self.store.db.execute('UPDATE requests SET result=?,state=? WHERE owner=? AND key=?',(tid,'dispatched',principal.id,key))
            self.store.db.commit()
        if turn_prompt is not None:
            self.agents.add_conversation_turn(row['id'],prompt=turn_prompt,task_id=tid,mode=row['mode'],provider_id=row.get('provider_id'),model=row.get('model'))
        if managed_session_id and getattr(self.state,'browser',None):
            live=self.state.identity.session_by_id(managed_session_id)
            if not live or live.principal.id!=principal.id:
                raise PermissionError('Originating managed session was revoked before dispatch')
            self.state.browser.records.put('agent-task-authority',tid,{'id':tid,'principal_id':principal.id,
                'session_id':managed_session_id,'project_id':row.get('project_id') or '', 'policy_version':principal.policy_version})
        if getattr(self.state,'extensions',None):
            self.state.extensions.finish_dispatch(principal,key,tid)
        if submitted_prompt is not None:
            with self.store.lock:
                current=self.store.get('conversation',row['id'])
                unchanged=current and current.get('draft_text','')==row.get('draft_text','') and current.get('draft_context',[])==row.get('draft_context',[])
                submitted=current and current.get('draft_text','')==submitted_prompt and current.get('draft_context',[])==(submitted_context or [])
                if unchanged or submitted:
                    self.store.update('conversation',row['id'],{**current,'draft_text':'','draft_context':[]},current['revision'])

    async def retry(self,principal,task_id,*,request_id,managed_session_id=None):
        meta=self.record(principal,'task',task_id,scope='agent-control')
        task=self.agents.get_task(task_id)
        if not task or task['status'] in ACTIVE_STATUSES:
            raise Conflict('Stop the original task before retrying')
        conversation=self.agents.get_conversation(meta['conversation_id'],include_turns=True)
        turn=next((item for item in (conversation or {}).get('turns',[]) if item.get('task_id')==task_id),None)
        if not turn:
            raise ValueError('Retry this child by steering its parent with an explicit new instruction')
        attachments=[]
        for artifact in self.agents.artifacts(task_id):
            if artifact['kind']=='upload' and artifact['mime'].startswith('image/'):
                if artifact['size']>2_000_000 or len(attachments)>=4:
                    raise ValueError('Original attachments exceed the current retry budget')
                private=self.agents.get_artifact(task_id,artifact['id'])
                if not private or not Path(private['path']).is_file():
                    raise ValueError('Original image is unavailable; attach it again before retrying')
                with Path(private['path']).open('rb') as image:
                    data=image.read(2_000_001)
                if len(data)!=artifact['size'] or hashlib.sha256(data).hexdigest()!=artifact['digest']:
                    raise Conflict('Original image changed; attach a fresh image before retrying')
                attachments.append({'name':'Original image '+str(len(attachments)+1),'mime':artifact['mime'],
                    'data':base64.b64encode(data).decode()})
        return await self.send(principal,meta['conversation_id'],prompt=turn['prompt'],request_id=request_id,
            limits=task['limits'],attachments=attachments,managed_session_id=managed_session_id)

    def memories(self, principal, *, project_id=None, include_excluded=True):
        self.require(principal,'agent-view',project_id)
        now = time()
        result = []
        for row in self.store.list('memory',principal.id):
            if row.get('expires_at') and row['expires_at'] <= now:
                self.store.delete('memory',row['id'])
                self.store.log(principal.id,'memory',row['id'],'retention_expired')
                continue
            if row.get('project_id') not in {None, project_id} or (row.get('excluded') and not include_excluded):
                continue
            result.append(row)
        return result

    def save_memory(self, principal, *, content, provenance, project_id=None, retention_days=30,
                    excluded=False, identifier=None, revision=None):
        self.require(principal,'agent-control',project_id)
        if not content.strip() or len(content.encode()) > 64_000 or not provenance.strip():
            raise ValueError('Memory needs bounded content and explicit provenance')
        if not 1 <= retention_days <= 3650:
            raise ValueError('Retention must be one to 3650 days')
        if redact(content) != content:
            raise ValueError('Do not store credentials in memory')
        body = {'content':content,'provenance':provenance[:1000],'excluded':bool(excluded),
                'expires_at':time()+retention_days*86400,'retention_days':retention_days}
        if identifier:
            previous = self.record(principal,'memory',identifier,scope='agent-control')
            if previous.get('project_id') != project_id:
                raise ValueError('Memory cannot change project scope')
            row = self.store.update('memory',identifier,body,revision)
        else:
            row = self.store.create('memory',principal.id,body,project_id)
        self.store.log(principal.id,'memory',row['id'],'saved',{'provenance':provenance[:200],'excluded':excluded})
        return row

    def delete_memory(self,principal,identifier):
        self.record(principal,'memory',identifier,scope='agent-control')
        self.store.delete('memory',identifier)
        self.store.log(principal.id,'memory',identifier,'deleted')

    def save_hook(self,principal,*,project_id,event,argv,cwd,timeout_s=10,capabilities=None,enabled=False):
        self.require(principal,'agent-control',project_id,cwd)
        if event not in {'before_turn','after_turn','task_failed'}:
            raise ValueError('Unsupported host hook event; native engine hook semantics are not emulated')
        if not 1<=timeout_s<=60 or not argv or len(argv)>32 or any(not isinstance(arg,str) or len(arg)>4096 for arg in argv):
            raise ValueError('Invalid hook command or timeout')
        if any(redact(arg)!=arg for arg in argv):
            raise ValueError('Hook commands cannot embed credentials')
        # Host hooks execute arbitrary project code in a writable sandbox;
        # expose that authority even when the command looks read-only.
        caps = sorted({'files-read', 'files-write', *(capabilities or [])})
        for cap in caps:
            if cap not in {'files-read','files-write','network-manage'}:
                raise ValueError('Hook capability not permitted')
            self.require(principal,cap,project_id,cwd)
        return self.store.create('hook',principal.id,{'event':event,'argv':argv,'cwd':str(Path(cwd).resolve(strict=True)),
            'timeout_s':timeout_s,'capabilities':caps,'enabled':bool(enabled),
            'semantics':'sandboxed_host_lifecycle'},project_id)

    async def run_hooks(self,principal,project_id,event,cwd,resource_id=None):
        for hook in self.store.list('hook',principal.id,project_id):
            if hook.get('project_id') != project_id or not hook['enabled'] or hook['event']!=event:
                continue
            self.require(principal,'agent-control',project_id,cwd,resource_kind='conversation' if resource_id else None,resource_id=resource_id)
            for scope in hook['capabilities']:
                self.require(principal,scope,project_id,cwd,resource_kind='conversation' if resource_id else None,resource_id=resource_id)
            if Path(hook['cwd']).resolve() != Path(cwd).resolve():
                raise PermissionError('Hook cannot cross its project folder')
            started = time()
            try:
                result = await run_shell(shlex.join(hook['argv']),cwd,timeout_s=hook['timeout_s'],
                    runner=self.hook_runner,profile='agent',network='outbound' if 'network-manage' in hook['capabilities'] else 'none',
                    granted_capabilities=('net.outbound',) if 'network-manage' in hook['capabilities'] else ())
                body = {'hook_id':hook['id'],'event':event,'duration':time()-started,**result.public()}
            except Exception as exc:
                body = {'hook_id':hook['id'],'event':event,'duration':time()-started,
                        'error':type(exc).__name__,'exit_code':None,'timed_out':False}
            # Output has the shared secret redactor; configuration args are
            # inspectable but audit records deliberately do not store them.
            body.pop('command',None)
            log = self.store.create('hook_run',principal.id,body,project_id)
            self.store.log(principal.id,'hook',hook['id'],'executed',{'run_id':log['id'],'exit_code':body['exit_code']})
            if body.get('error') or body.get('timed_out') or body.get('exit_code') != 0:
                raise Conflict('Lifecycle hook failed; inspect its run log before retrying')

    def _on_event(self,task_id,event):
        if self._closed:
            return
        if event.get('type') == 'subagent.started':
            parent = self.store.get('task', task_id)
            child_id = (event.get('payload') or {}).get('child_id')
            child = self.agents.get_task(child_id) if child_id else None
            if parent and child and not self.store.get('task', child_id):
                self.store.create('task', parent['owner'], {'conversation_id':parent.get('conversation_id'), 'cwd':child['cwd']}, parent.get('project_id'), child_id)
                lookup = getattr(self, 'principal_lookup', None)
                actor = lookup(parent['owner']) if lookup else None
                if actor and getattr(self.state, 'authorization', None):
                    self.state.authorization.claim_principal(actor, 'task', child_id, project_id=parent.get('project_id'))
            return
        if event.get('type') not in {'task.completed','task.failed'}:
            return
        row = self.store.get('task',task_id)
        if not row:
            # Supervised children inherit the parent's ownership for API
            # inspection; they never inherit an unrelated user's resources.
            task = self.agents.get_task(task_id)
            parent = self.store.get('task',(task or {}).get('parent_id') or '')
            if parent:
                self.store.create('task',parent['owner'],{'conversation_id':parent.get('conversation_id'),
                    'cwd':(task or {}).get('cwd')},parent.get('project_id'),task_id)
                row = self.store.get('task',task_id)
        if row and not row.get('runner_id'):
            callback = getattr(self,'principal_lookup',None)
            principal = callback(row['owner']) if callback else None
            if principal:
                worker = asyncio.create_task(self.run_hooks(principal,row.get('project_id'),
                    'after_turn' if event['type']=='task.completed' else 'task_failed',row['cwd'],resource_id=row.get('conversation_id')))
                self._workers.add(worker)
                worker.add_done_callback(self._hook_done)

    def _hook_done(self,worker):
        self._workers.discard(worker)
        if not worker.cancelled():
            worker.exception()  # Failure is already persisted in hook_run.

    async def close(self):
        self._closed=True
        self.state.agent._listeners.discard(self._on_event)
        for worker in list(self._workers):
            worker.cancel()
        await asyncio.gather(*self._workers,return_exceptions=True)
        self.store.close()
