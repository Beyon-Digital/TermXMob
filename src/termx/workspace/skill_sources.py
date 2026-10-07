"""Scoped native SKILL.md authoring with immutable history and CAS writes.

Editing a source never enables an installed extension. Publishing uses the
same reviewed immutable bundle path as other imports. Compatibility sources
are read-only and can be imported explicitly into a managed .agents root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from termx.agent.policy import redact
from termx.discovery.roots import default_roots
from termx.discovery.safety import slugify
from termx.frontmatter import split_frontmatter
from termx.workspace.extensions import SECRETS
from termx.workspace.store import Conflict

MAX_SKILL_BYTES=512_000
MAX_SKILLS=500
FOLDER=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_markdown(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>MAX_SKILL_BYTES:
        raise ValueError('Skill must contain at most 512,000 UTF-8 bytes')
    if SECRETS.search(text) or redact(text)!=text:
        raise ValueError('Skill contains embedded credentials; remove them before saving or exporting')
    if text.startswith('---'):
        end=text.find('\n---',3)
        if end<0 or end>8192:
            raise ValueError('Skill metadata must have a closed, bounded YAML frontmatter block')
        # Alias expansion and tags do not belong in portable skill metadata.
        import yaml
        try:
            for token in yaml.scan(text[3:end]):
                if isinstance(token,(yaml.tokens.AliasToken,yaml.tokens.AnchorToken,yaml.tokens.TagToken)):
                    raise ValueError('Skill metadata cannot contain YAML aliases, anchors or custom tags')
            raw_metadata=yaml.safe_load(text[3:end])
            if raw_metadata is not None and not isinstance(raw_metadata,dict):raise ValueError('Skill frontmatter must be a mapping')
            meta,body=split_frontmatter(text)
            if len(json.dumps(meta).encode())>8192:
                raise ValueError('Skill metadata exceeds its byte budget')
        except (yaml.YAMLError,TypeError,RecursionError):
            raise ValueError('Skill frontmatter must be portable YAML metadata') from None
    else:
        meta,body={},text
    if not body.strip():
        raise ValueError('Skill instructions cannot be empty')
    for name in ('name','description'):
        if name in meta and (not isinstance(meta[name],str) or len(meta[name])>1000):
            raise ValueError('Skill name and description must be bounded text')
    return {'name':meta.get('name',''),'description':meta.get('description',''),'metadata':meta,'digest':digest(text),'bytes':len(text.encode())}


@contextmanager
def directory(base,parts,*,create=False):
    """Open each child directory without following links, retaining the final FD.

    POSIX operations use the FD rather than re-resolving an attacker-swappable
    ancestor. Windows rejects junctions/reparse links before every operation.
    """
    base=Path(base)
    if base.is_symlink() or getattr(base,'is_junction',lambda:False)():
        raise ValueError('Skill source root cannot be a symlink or junction')
    if create:
        base.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=='posix':
        fd=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts:
                if create:
                    try:os.mkdir(part,mode=0o700,dir_fd=fd)
                    except FileExistsError:pass
                following=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=following
            yield base.joinpath(*parts),fd
        finally:os.close(fd)
    else:
        path=base
        for part in parts:
            path=path/part
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                raise ValueError('Skill source cannot traverse a link or junction')
            if create:path.mkdir(exist_ok=True,mode=0o700)
            if not path.is_dir():raise ValueError('Skill directory is unavailable')
        yield path,None


def read_at(path,fd):
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)
    file=path/'SKILL.md'
    prior=None
    if fd is None:
        if file.is_symlink() or getattr(file,'is_junction',lambda:False)() or os.path.normcase(str(file.resolve()))!=os.path.normcase(str(file.absolute())):
            raise ValueError('Skill source cannot traverse a symlink or junction')
        prior=file.stat(follow_symlinks=False)
    handle=os.open('SKILL.md',flags,dir_fd=fd) if fd is not None else os.open(path/'SKILL.md',flags)
    try:
        info=os.fstat(handle)
        if prior and ((info.st_dev,info.st_ino)!=(prior.st_dev,prior.st_ino) or os.path.normcase(str(file.resolve()))!=os.path.normcase(str(file.absolute()))):
            raise Conflict('Skill path changed while reading; retry from the managed source')
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_SKILL_BYTES:
            raise ValueError('Skill must be a bounded regular file')
        chunks=[];received=0
        while True:
            block=os.read(handle,min(64_000,MAX_SKILL_BYTES+1-received))
            if not block:break
            chunks.append(block);received+=len(block)
            if received>MAX_SKILL_BYTES:raise ValueError('Skill exceeds its byte budget')
        data=b''.join(chunks)
        return data.decode('utf-8')
    finally:os.close(handle)


class SkillSourceService:
    def __init__(self,workspace,extensions,*,compatibility_home=None):
        self.workspace=workspace;self.extensions=extensions;self.store=workspace.store
        self.compatibility_home=Path(compatibility_home) if compatibility_home is not None else Path.home()

    def _root(self,principal,scope,project_id,write=False):
        if scope not in {'user','project'}:raise ValueError('Source scope must be user or project')
        if scope=='user':
            self.workspace.require(principal,'host-admin')
            configured=Path(getattr(self.workspace.state,'agents_root',Path.home()/'.agents')).expanduser().absolute()
            if configured.is_symlink() or getattr(configured,'is_junction',lambda:False)():
                raise ValueError('Global .agents root cannot be a link')
            return configured.parent.resolve(),[configured.name],None
        if not project_id:raise ValueError('Project skill source requires a project')
        projects=self.workspace.state.projects.projects()
        project=projects.get(project_id) if isinstance(projects,dict) else next((p for p in projects if p['id']==project_id),None)
        if not project:raise KeyError(project_id)
        # ProjectStore stores canonical path strings; alternate test/adapter
        # mappings may expose the same enrolled path as a path field.
        raw=Path(project['path'] if isinstance(project,dict) else project)
        if raw.is_symlink() or getattr(raw,'is_junction',lambda:False)():raise ValueError('Enrolled project root cannot become a link or junction')
        base=raw.resolve(strict=True)
        self.workspace.require(principal,'files-write' if write else 'files-read',project_id,str(base))
        return base,['.agents'],project_id

    def read(self,principal,*,scope,project_id=None,name):
        if not FOLDER.fullmatch(name):raise ValueError('Skill folder name must be a safe slug')
        base,parts,pid=self._root(principal,scope,project_id)
        with directory(base,[*parts,'skills',name]) as (path,fd):
            text=read_at(path,fd)
        metadata=validate_markdown(text)
        self._root(principal,scope,project_id)
        return {**metadata,'folder':name,'scope':scope,'project_id':pid,'markdown':text,'format':'skill-markdown/v1','path':str(path/'SKILL.md')}

    def list(self,principal,project_id=None):
        rows=[];diagnostics=[];roots=[]
        for scope in ('user','project') if project_id else ('user',):
            try:
                base,parts,pid=self._root(principal,scope,project_id)
            except PermissionError:
                if scope=='user':continue
                raise
            roots.append({'scope':scope,'project_id':pid,'path':str(base.joinpath(*parts))})
            try:
                with directory(base,[*parts,'skills']) as (path,fd):
                    # os.scandir(fd) follows the retained directory object.
                    with os.scandir(fd if fd is not None else path) as children:
                        names=[]
                        for index,child in enumerate(children):
                            if index>=MAX_SKILLS:
                                diagnostics.append({'scope':scope,'message':'Source scan capped at 500 entries'});break
                            if child.is_dir(follow_symlinks=False) and not child.is_symlink() and FOLDER.fullmatch(child.name):names.append(child.name)
                    for name in sorted(names):
                        try:
                            row=self.read(principal,scope=scope,project_id=project_id,name=name)
                            rows.append({k:v for k,v in row.items() if k!='markdown'})
                        except (ValueError,OSError,UnicodeError):
                            diagnostics.append({'scope':scope,'folder':name,'message':'Unsafe, invalid or credential-bearing skill; contents withheld'})
            except FileNotFoundError:pass
            except OSError:diagnostics.append({'scope':scope,'message':'Source is unavailable or contains a link'})
        selected={}
        # The inspector declares project-over-user precedence for explicit
        # portable publishing. Engine-owned autodiscovery remains native.
        for row in rows:
            if row['folder'] not in selected or row['scope']=='project':selected[row['folder']]=row
        for row in rows:
            active=selected[row['folder']]
            row['effective_for_publish']=row is active
            row['overridden_by']=None if row is active else {'scope':active['scope'],'project_id':active['project_id'],'digest':active['digest']}
        if project_id:self._root(principal,'project',project_id)
        return {'sources':rows,'roots':roots,'diagnostics':diagnostics,'precedence':'Project overrides user for explicit portable publishing; native engine discovery retains its own precedence'}

    def save(self,principal,*,scope,name,markdown,expected_digest,project_id=None):
        if not FOLDER.fullmatch(name):raise ValueError('Skill folder name must be a safe slug')
        meta=validate_markdown(markdown)
        base,parts,pid=self._root(principal,scope,project_id,True)
        with self.store.lock, directory(base,[*parts,'skills',name],create=True) as (path,fd):
            try:old=read_at(path,fd)
            except FileNotFoundError:old=None
            if (digest(old) if old is not None else None)!=expected_digest:
                raise Conflict('Skill changed on disk; compare the current source before saving')
            # Record immutable before/after snapshots before publishing the file.
            # A crash leaves a history entry, never a partially written source.
            self._root(principal,scope,project_id,True)
            for value in (old,markdown):
                if value is None:continue
                validate_markdown(value)
                key=hashlib.sha256((principal.id+str(path)+digest(value)).encode()).hexdigest()
                if not self.store.get('skill_version',key):self.store.create('skill_version',principal.id,{'scope':scope,'folder':name,'digest':digest(value),'markdown':value,'source_path':str(path)},project=pid,identifier=key)
            temporary='.termx-'+os.urandom(12).hex()
            flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
            handle=os.open(temporary,flags,0o600,dir_fd=fd) if fd is not None else os.open(path/temporary,flags,0o600)
            try:
                with os.fdopen(handle,'wb') as stream:
                    stream.write(markdown.encode());stream.flush();os.fsync(stream.fileno())
                try:latest=read_at(path,fd)
                except FileNotFoundError:latest=None
                if (digest(latest) if latest is not None else None)!=expected_digest:raise Conflict('Skill changed during save; reload before retrying')
                self._root(principal,scope,project_id,True)
                if fd is not None:os.replace(temporary,'SKILL.md',src_dir_fd=fd,dst_dir_fd=fd)
                else:
                    with directory(base,[*parts,'skills',name]):pass
                    os.replace(path/temporary,path/'SKILL.md')
            finally:
                try:os.unlink(temporary,dir_fd=fd) if fd is not None else (path/temporary).unlink()
                except FileNotFoundError:pass
            self.store.log(principal.id,'skill_source',name,'saved',{'digest':meta['digest'],'scope':scope})
        return self.read(principal,scope=scope,project_id=project_id,name=name)

    def versions(self,principal,*,scope,name,project_id=None):
        base,parts,pid=self._root(principal,scope,project_id)
        if not FOLDER.fullmatch(name):raise ValueError('Invalid skill name')
        path=str(base.joinpath(*parts,'skills',name))
        return [{k:v for k,v in row.items() if k!='markdown'} for row in self.store.list('skill_version',principal.id) if row['source_path']==path and row.get('project_id')==pid]

    def version(self,principal,identifier):
        current=self.store.get('skill_version',identifier)
        if not current:raise KeyError(identifier)
        row=self.workspace.record(principal,'skill_version',identifier,scope='host-admin' if not current.get('project_id') else 'files-read')
        self._root(principal,row['scope'],row.get('project_id'))
        validate_markdown(row['markdown'])
        return row

    def publish_preview(self,principal,*,scope,name,version,project_id=None):
        source=self.read(principal,scope=scope,project_id=project_id,name=name)
        preview=self.extensions.preview(principal,{'id':slugify(name),'version':version,'markdown':source['markdown']},'skill-markdown/v1')
        return self.store.update('extension_preview',preview['id'],{**preview,'provenance':{'kind':'native-skill-source','folder':name,'scope':scope,'project_id':project_id,'path':source['path'],'source_digest':source['digest']}})

    def compatibility(self,principal,project_id=None,identifier=None):
        projects={};global_allowed=False
        try:self.workspace.require(principal,'host-admin');global_allowed=True
        except PermissionError:pass
        if project_id:
            base,_,_=self._root(principal,'project',project_id)
            projects[project_id]=str(base)
        rows=[]
        roots=default_roots(projects,user_agents_dir=str(getattr(self.workspace.state,'agents_root',Path.home()/'.agents')))
        for root in roots:
            if not root.compat or root.kinds!=('skills',):continue
            path=Path(root.path)
            if path.is_relative_to(Path.home()) and self.compatibility_home!=Path.home():path=self.compatibility_home/path.relative_to(Path.home())
            project=next((pid for pid,p in projects.items() if path.is_relative_to(Path(p))),None)
            if project:
                anchor=Path(projects[project])
            elif global_allowed:anchor=self.compatibility_home
            else:continue
            if not path.is_relative_to(anchor):continue
            try:
                with directory(anchor,list(path.relative_to(anchor).parts)) as (opened,fd):
                    with os.scandir(fd if fd is not None else opened) as children:
                        import itertools
                        names=[child.name for child in itertools.islice(children,MAX_SKILLS) if child.is_dir(follow_symlinks=False) and not child.is_symlink() and FOLDER.fullmatch(child.name)]
                    for name in names:
                        try:
                            with directory(anchor,[*path.relative_to(anchor).parts,name]) as (folder,child_fd):
                                text=read_at(folder,child_fd)
                            meta=validate_markdown(text)
                            token=hashlib.sha256((str(path)+'/'+name).encode()).hexdigest()
                            row={**meta,'id':token,'folder':name,'project_id':project,'source':root.source,'path':str(folder/'SKILL.md'),'read_only':True,'trusted':False}
                            if identifier==token:
                                if project:self._root(principal,'project',project)
                                else:self.workspace.require(principal,'host-admin')
                                return {**row,'markdown':text}
                            rows.append(row)
                        except (ValueError,OSError,UnicodeError):continue
            except (ValueError,OSError):continue
        if identifier:raise KeyError(identifier)
        return rows
