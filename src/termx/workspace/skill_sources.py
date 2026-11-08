"""Scoped native SKILL.md authoring with immutable history and CAS writes.

Editing a source never enables an installed extension. Publishing uses the
same reviewed immutable bundle path as other imports. Compatibility sources
are read-only and can be imported explicitly into a managed .agents root.
"""
from __future__ import annotations

import hashlib
import errno
import itertools
import json
import os
import re
import stat
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException

from termx.agent.policy import redact
from termx.discovery.roots import default_roots
from termx.discovery.safety import slugify
from termx.workspace.extensions import SECRETS,SLUG,bundle_digest,credential_name,file_label
from termx.workspace.store import Conflict

MAX_SKILL_BYTES=512_000
MAX_SKILLS=500
MAX_FRONTMATTER_BYTES=8192
FOLDER=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _frontmatter(text):
    """The raw YAML block and body, split at the same line ``split_frontmatter`` closes it, within a byte bound."""
    lines=text.split('\n')
    size=0
    for index in range(1,len(lines)):
        if lines[index].strip()=='---':
            body='\n'.join(lines[index+1:])
            return '\n'.join(lines[1:index]),body[1:] if body.startswith('\n') else body
        size+=len(lines[index].encode())+1
        if size>MAX_FRONTMATTER_BYTES:break
    raise ValueError('Skill metadata must have a closed, bounded YAML frontmatter block')


def validate_markdown(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>MAX_SKILL_BYTES:
        raise ValueError('Skill must contain at most 512,000 UTF-8 bytes')
    if SECRETS.search(text) or redact(text)!=text:
        raise ValueError('Skill contains embedded credentials; remove them before saving or exporting')
    if text.startswith('---'):
        raw,body=_frontmatter(text)
        # Alias expansion and tags do not belong in portable skill metadata. The scan and the parse see the same block.
        import yaml
        try:
            for token in yaml.scan(raw):
                if isinstance(token,(yaml.tokens.AliasToken,yaml.tokens.AnchorToken,yaml.tokens.TagToken)):
                    raise ValueError('Skill metadata cannot contain YAML aliases, anchors or custom tags')
            meta=yaml.safe_load(raw)
            if meta is None:meta={}
            if not isinstance(meta,dict):raise ValueError('Skill frontmatter must be a mapping')
            if len(json.dumps(meta).encode())>MAX_FRONTMATTER_BYTES:
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
        raise OSError(errno.ELOOP,'Skill source root cannot be a symlink or junction',str(base))
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
                raise OSError(errno.ELOOP,'Skill source cannot traverse a link or junction',str(path))
            if create:path.mkdir(exist_ok=True,mode=0o700)
            if not path.exists():raise FileNotFoundError(errno.ENOENT,'Skill directory is unavailable',str(path))
            if not path.is_dir():raise NotADirectoryError(errno.ENOTDIR,'Skill source must be a directory',str(path))
        yield path,None


def read_at(path,fd,name='SKILL.md'):
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    file=path/name
    prior=None
    if fd is None:
        if file.is_symlink() or getattr(file,'is_junction',lambda:False)() or os.path.normcase(str(file.resolve()))!=os.path.normcase(str(file.absolute())):
            raise OSError(errno.ELOOP,'Skill source cannot traverse a symlink or junction',str(file))
        prior=file.stat(follow_symlinks=False)
    handle=os.open(name,flags,dir_fd=fd) if fd is not None else os.open(path/name,flags)
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


# -- folder import ---------------------------------------------------------------------

MAX_TREE_DEPTH=4
MAX_TREE_FILES=200
MAX_TREE_BYTES=2_000_000
MAX_TREE_ENTRIES=1000
SKIPPED_FOLDERS={'node_modules','__pycache__'}

def ascii_slug(value):
    """A bundle id from any folder name: ASCII letters and digits joined by hyphens, never empty."""
    plain=unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode()
    return re.sub('[^a-z0-9]+','-',plain.lower()).strip('-')[:80] or 'imported-skill'


def read_tree(base,parts):
    """Read a skill folder as bounded text without following links.

    Every directory is opened with O_NOFOLLOW from ``base``. Links, special files, credential files and anything over
    the limits fail the whole read; binaries and hidden entries are listed as skipped, never read.
    """
    files={};skipped=[];seen=[0];total=[0]
    def walk(relative):
        if len(relative)>MAX_TREE_DEPTH:raise ValueError('The folder nests deeper than 4 levels')
        with directory(base,[*parts,*relative]) as (path,fd):
            with os.scandir(fd if fd is not None else path) as children:
                entries=sorted((child.name,child.is_symlink(),child.is_dir(follow_symlinks=False),child.is_file(follow_symlinks=False))
                               for child in itertools.islice(children,MAX_TREE_ENTRIES+1))
            for name,link,folder,regular in entries:
                seen[0]+=1
                if seen[0]>MAX_TREE_ENTRIES:raise ValueError('The folder has more than 1,000 entries')
                shown='/'.join([*relative,name])
                if link:raise ValueError(shown+' is a link; links are never imported')
                if credential_name(name):raise ValueError(shown+' looks like a credential file and cannot be imported')
                if folder:
                    if name.startswith('.') or name in SKIPPED_FOLDERS:skipped.append({'path':shown,'reason':'hidden folder' if name.startswith('.') else 'dependency folder'})
                    else:walk([*relative,name])
                elif not regular:raise ValueError(shown+' is not a regular file')
                elif name.startswith('.'):skipped.append({'path':shown,'reason':'hidden file'})
                else:
                    if len(files)>=MAX_TREE_FILES:raise ValueError('The folder has more than 200 text files')
                    try:text=read_at(path,fd,name)
                    except UnicodeDecodeError:skipped.append({'path':shown,'reason':'binary'});continue
                    except ValueError:raise ValueError(shown+' is not a regular text file under 512 KB') from None
                    if '\x00' in text:skipped.append({'path':shown,'reason':'binary'});continue
                    total[0]+=len(text.encode())
                    if total[0]>MAX_TREE_BYTES:raise ValueError('The folder holds more than 2 MB of text')
                    files[shown]=text
    walk([])
    return {'files':files,'skipped':skipped,'bytes':total[0]}


def _relative(path):
    if not isinstance(path,str) or not path or len(path)>300 or '\\' in path or path.startswith('/') or any(ord(c)<32 for c in path):
        raise ValueError('Unsafe file path in the folder')
    parts=path.split('/')
    if len(parts)>MAX_TREE_DEPTH+1 or any(part in {'','.','..'} or len(part)>100 for part in parts):
        raise ValueError('Unsafe file path in the folder')
    return path


def _credential_check(path):
    if any(credential_name(part) for part in path.split('/')):raise ValueError(path+' looks like a credential file and cannot be imported')


def _skip_reason(path):
    """Why a posted path is never read, as ``read_tree`` would list it: a dependency folder or a hidden entry."""
    parts=path.split('/')
    if any(part in SKIPPED_FOLDERS for part in parts):return 'dependency folder'
    return 'hidden file' if any(part.startswith('.') for part in parts) else None


def tree_from_files(files,skipped=()):
    """The same tree from a browser-picked folder posted as a path to text map. Nothing here touches the host disk."""
    if not isinstance(files,dict) or not files:raise ValueError('Choose a folder that contains a SKILL.md')
    if len(files)>MAX_TREE_FILES:raise ValueError('The folder has more than 200 text files')
    if not isinstance(skipped,(list,tuple)) or len(skipped)>MAX_TREE_ENTRIES:raise ValueError('Too many skipped files')
    result={};listed=[];folded=set();total=0
    for path in sorted(files):
        _relative(path)
        text=files[path]
        if not isinstance(text,str):raise ValueError(path+' is not text')
        _credential_check(path)
        if _skip_reason(path):listed.append({'path':path,'reason':_skip_reason(path)});continue
        try:size=len(text.encode())
        except UnicodeEncodeError:size=-1
        if size<0 or '\x00' in text:listed.append({'path':path,'reason':'binary'});continue
        if size>MAX_SKILL_BYTES:raise ValueError(path+' is not a regular text file under 512 KB')
        total+=size
        if total>MAX_TREE_BYTES:raise ValueError('The folder holds more than 2 MB of text')
        if path.casefold() in folded:raise ValueError('The folder lists '+path+' twice')
        folded.add(path.casefold());result[path]=text
    for path in skipped:
        _credential_check(_relative(path))
        listed.append({'path':path,'reason':_skip_reason(path) or 'binary'})
    for path in result:
        parts=path.split('/')
        if any('/'.join(parts[:index]) in result for index in range(1,len(parts))):raise ValueError(path+' sits inside a file; use a folder')
    return {'files':result,'skipped':listed,'bytes':total}


def import_bundle(tree,*,name,version):
    """A termx-bundle/v1 dict for ``extensions.preview`` from a read tree: one skill at the root, or a folder of skills."""
    files=tree['files']
    for path,text in files.items():
        if SECRETS.search(text) or redact(text)!=text:
            raise ValueError(path+' appears to contain a credential; remove it before importing')
    if 'SKILL.md' in files:groups={ascii_slug(name):''}
    else:
        groups={}
        for folder in sorted({path.split('/')[0] for path in files if path.endswith('/SKILL.md') and path.count('/')==1}):
            identifier=ascii_slug(folder)
            if identifier in groups:raise ValueError('Two skill folders have the same name once cleaned up: '+identifier)
            groups[identifier]=folder+'/'
        if not groups:raise ValueError('Pick a skill folder with a SKILL.md, or a folder of skill folders')
    bundle_files={};skills=[];claimed=set()
    for identifier,prefix in groups.items():
        text=files[prefix+'SKILL.md']
        try:meta=validate_markdown(text)
        except ValueError as exc:raise ValueError(prefix+'SKILL.md: '+str(exc)) from None
        entries=[]
        for path in sorted(p for p in files if p.startswith(prefix)):
            key='skills/'+identifier+'/'+path[len(prefix):]
            claimed.add(path);bundle_files[key]=files[path]
            entries.append({'path':key,'bytes':len(files[path].encode()),**file_label(key)})
        skills.append({'id':identifier,'name':meta['name'] or identifier,'description':meta['description'],'files':entries})
    skipped=[*tree['skipped'],*({'path':path,'reason':'not part of a skill'} for path in sorted(files) if path not in claimed)]
    description=skills[0]['description'] if len(skills)==1 else f'{len(skills)} skills'
    bundle={'format':'termx-bundle/v1','id':ascii_slug(name),'version':version,'permissions':['files-read'],
            'description':description[:300],'files':bundle_files}
    return {'bundle':bundle,'skills':skills,'skipped':skipped,'bytes':sum(len(t.encode()) for t in bundle_files.values())}


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
            editable=True
            try:self._root(principal,scope,project_id,write=True)
            except PermissionError:editable=False
            except HTTPException as exc:
                if exc.status_code!=403:raise
                editable=False
            roots.append({'scope':scope,'project_id':pid,'path':str(base.joinpath(*parts)),
                'editable':editable,'read_only_reason':None if editable else
                    'Your current permissions allow reading this source, but not saving changes.'})
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

    def compatibility(self,principal,project_id=None,identifier=None,location=False):
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
            # An enrolled project may itself live under the account's home
            # (including Windows Temp). Only global compatibility roots are
            # relocated to the configured compatibility home.
            project=next((pid for pid,p in projects.items() if path.is_relative_to(Path(p))),None)
            if project is None and path.is_relative_to(Path.home()) and self.compatibility_home!=Path.home():path=self.compatibility_home/path.relative_to(Path.home())
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
                                if location:return {**row,'location':(anchor,[*path.relative_to(anchor).parts,name])}
                                return {**row,'markdown':text}
                            rows.append(row)
                        except (ValueError,OSError,UnicodeError):continue
            except (ValueError,OSError):continue
        if identifier:raise KeyError(identifier)
        return rows

    @staticmethod
    def _native_token(row):
        return hashlib.sha256('|'.join(('native',row['scope'],row.get('project_id') or '',row['folder'])).encode()).hexdigest()

    def detected(self,principal,project_id=None):
        """Skill folders this host already has (user, project and compatibility sources), named by an opaque token.

        A client or the model can only choose from these tokens; host paths are never accepted or returned.
        """
        rows=[{'token':self._native_token(row),'folder':row['folder'],'name':row['name'] or row['folder'],'description':row['description'],
               'source':'project' if row['scope']=='project' else 'user','project_id':row['project_id']}
              for row in self.list(principal,project_id)['sources']]
        rows+=[{'token':row['id'],'folder':row['folder'],'name':row['name'] or row['folder'],'description':row['description'],
                'source':row['source'],'project_id':row['project_id']} for row in self.compatibility(principal,project_id)]
        return rows

    def _detected_tree(self,principal,project_id,token):
        if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{64}',token):raise KeyError(token)
        for row in self.list(principal,project_id)['sources']:
            if self._native_token(row)==token:
                base,parts,_=self._root(principal,row['scope'],row['project_id'])
                return row['folder'],read_tree(base,[*parts,'skills',row['folder']])
        found=self.compatibility(principal,project_id,token,location=True)
        return found['folder'],read_tree(*found['location'])

    def preview_import(self,principal,*,files=None,skipped=(),token=None,name=None,project_id=None,version='1'):
        """Review a skill folder for install. Nothing is installed here; ``extensions.install`` stays a host-admin click."""
        self.workspace.require(principal,'host-admin')
        if (files is None)==(token is None):raise ValueError('Import either a posted folder or one detected source')
        if not isinstance(version,str) or not SLUG.fullmatch(version):raise ValueError('Version must be letters, digits, dots, dashes or underscores, such as 1 or 1.2.0')
        try:
            if token is not None:
                folder,tree=self._detected_tree(principal,project_id,token)
                provenance={'kind':'detected-skill-import','token':token,'project_id':project_id}
            else:
                if not isinstance(name,str) or not name.strip() or len(name)>200:raise ValueError('Name the folder you are importing')
                folder,tree=name,tree_from_files(files,skipped)
                provenance={'kind':'folder-import','name':name.strip()[:200]}
        except OSError:
            raise ValueError('The folder contains a link or cannot be read; links are never imported') from None
        built=import_bundle(tree,name=folder,version=version)
        preview=self.extensions.preview(principal,built['bundle'])
        provenance={**provenance,'version':version,'file_count':len(built['bundle']['files']),'bytes':built['bytes'],'skipped_count':len(built['skipped'])}
        return self.store.update('extension_preview',preview['id'],{**preview,'provenance':provenance,
            'import':{'skills':built['skills'],'skipped':built['skipped'],'bytes':built['bytes']}})

    def import_digest(self,principal,provenance):
        """Digest of a detected source as it is on disk now, to compare with the reviewed one at install."""
        try:
            folder,tree=self._detected_tree(principal,provenance.get('project_id'),provenance['token'])
            built=import_bundle(tree,name=folder,version=provenance['version'])
            return bundle_digest(self.extensions.normalize(built['bundle'],'termx-bundle/v1'))
        except (OSError,ValueError,KeyError):
            return None

    def propose_for_task(self,task_id,token=None):
        """``propose_skill_install``: list detected sources, or review one. The task's owner installs it with a click."""
        principal,row=self.extensions.task_owner(task_id)
        if token is None:
            self.workspace.require(principal,'host-admin')
            return {'sources':[{k:v for k,v in item.items() if k!='project_id'} for item in self.detected(principal,row.get('project_id'))]}
        preview=self.preview_import(principal,token=token,project_id=row.get('project_id'),version='1')
        return {'preview_id':preview['id'],'bundle_id':preview['bundle']['id'],'digest':preview['digest'],
                'permissions_added':preview['permission_diff']['added'],'file_count':preview['provenance']['file_count'],
                'skipped':[item['path'] for item in preview['import']['skipped']][:20],'installed':False,
                'message':'Review is ready in Settings, Plugins and skills. Only the owner can install it.'}
