from pathlib import Path
from types import SimpleNamespace
import os
import pytest
import termx.workspace.skill_sources as source_module
from fastapi import HTTPException
from termx.authorization import AuthorizationService
from termx.workspace.extensions import ExtensionService
from termx.workspace.skill_sources import SkillSourceService,validate_markdown
from termx.workspace.store import Conflict
from test_durable_workspace import workspace

@pytest.fixture(params=['platform','checked-path'])
def skill_io(request,monkeypatch):
    if request.param=='checked-path':
        # Exercise the Windows path-checked implementation on every host
        # without changing global os.name or pathlib's native path class.
        operations=vars(os).copy();operations['name']='nt'
        monkeypatch.setattr(source_module,'os',SimpleNamespace(**operations))

@pytest.fixture
def sources(workspace,skill_io):
    service,owner,path=workspace
    project=path/'project';project.mkdir()
    service.state.agents_root=path/'user-agents'
    service.state.projects=SimpleNamespace(projects=lambda:[{'id':'p','path':str(project)}])
    service.state.authorization=AuthorizationService(service.state.identity)
    extensions=ExtensionService(service,path/'bundles')
    sources=SkillSourceService(service,extensions);service.state.skill_sources=sources
    return sources,extensions,owner,project

TEXT='---\nname: Useful skill\ndescription: Helps inspect source\n---\nInspect the selected files and explain the result.'

def save(sources,owner,**overrides):
    return sources.save(owner,scope='project',name='inspect',project_id='p',markdown=TEXT,expected_digest=None,**overrides)

def test_native_source_override_history_conflict_and_reviewed_runtime(sources):
    skills,extensions,owner,project=sources
    global_row=skills.save(owner,scope='user',name='inspect',markdown=TEXT+' Global',expected_digest=None)
    row=save(skills,owner)
    index=skills.list(owner,'p')
    assert len(index['sources'])==2
    assert next(r for r in index['sources'] if r['scope']=='user')['overridden_by']['digest']==row['digest']
    assert next(r for r in index['sources'] if r['scope']=='project')['effective_for_publish']
    newer=skills.save(owner,scope='project',name='inspect',project_id='p',markdown=TEXT+' Changed',expected_digest=row['digest'])
    with pytest.raises(Conflict):skills.save(owner,scope='project',name='inspect',project_id='p',markdown=TEXT+' Lost',expected_digest=row['digest'])
    versions=skills.versions(owner,scope='project',project_id='p',name='inspect')
    assert len(versions)==2
    assert {skills.version(owner,v['id'])['markdown'] for v in versions}=={TEXT,TEXT+' Changed'}
    preview=skills.publish_preview(owner,scope='project',project_id='p',name='inspect',version='1')
    assert not extensions.store.list('extension')
    extensions.install(owner,preview['id'],preview['digest'])
    context=extensions.execution_context(owner,['inspect'],'p',str(project))
    assert context[0]['instructions'][0]['content']==newer['markdown']
    assert global_row['markdown']==TEXT+' Global'

def test_source_changed_after_publish_review_refused(sources):
    skills,extensions,owner,project=sources
    row=save(skills,owner);preview=skills.publish_preview(owner,scope='project',project_id='p',name='inspect',version='1')
    skills.save(owner,scope='project',name='inspect',project_id='p',markdown=TEXT+' modified',expected_digest=row['digest'])
    with pytest.raises(Conflict,match='source changed'):extensions.install(owner,preview['id'],preview['digest'])
    assert not extensions.store.list('extension')

def test_links_secrets_aliases_and_oversize_never_exported(sources,tmp_path):
    skills,_,owner,project=sources
    for text in ('---\nname: x', '---\na: &a [x]\nb: *a\n---\nBody', 'password=embedded-secret-value', 'x'*512001):
        with pytest.raises(ValueError):validate_markdown(text)
    outside=tmp_path/'outside';outside.mkdir();(outside/'SKILL.md').write_text(TEXT)
    (project/'.agents').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):save(skills,owner)
    assert not (outside/'skills').exists()
    (project/'.agents').unlink();row=save(skills,owner)
    path=project/'.agents/skills/inspect/SKILL.md';path.unlink();path.symlink_to(outside/'SKILL.md')
    with pytest.raises(OSError):skills.read(owner,scope='project',name='inspect',project_id='p')
    assert skills.list(owner,'p')['sources']==[]

def test_project_grants_and_live_revocation_do_not_borrow_host_admin(sources):
    skills,_,owner,project=sources
    save(skills,owner)
    authz=skills.workspace.state.authorization;identity=skills.workspace.state.identity
    user=identity.create_principal('Project editor',['files-read','files-write','agent-view'])
    authz.set_role(user.id,'operator');authz.grant_project(user.id,'p',['files-read','files-write','agent-view'])
    user=identity.principal_by_id(user.id)
    assert skills.read(user,scope='project',project_id='p',name='inspect')['markdown']==TEXT
    assert all(r['scope']=='project' for r in skills.list(user,'p')['sources'])
    with pytest.raises((PermissionError,HTTPException)):skills.read(user,scope='user',name='inspect')
    authz.grant_project(user.id,'p',[])
    with pytest.raises((PermissionError,HTTPException)):skills.read(user,scope='project',project_id='p',name='inspect')


def test_skill_root_editability_is_advertised_without_creating_directories(sources):
    skills,_,owner,project=sources
    user_root=skills.workspace.state.agents_root
    assert not user_root.exists() and not (project/'.agents').exists()
    roots=skills.list(owner,'p')['roots']
    assert {(root['scope'],root['project_id'],root['editable']) for root in roots}=={
        ('user',None,True),('project','p',True)}
    assert all(root['read_only_reason'] is None for root in roots)
    assert not user_root.exists() and not (project/'.agents').exists()


def test_skill_root_readonly_and_live_write_revocation_keep_read_inventory(sources):
    skills,_,owner,project=sources
    saved=save(skills,owner)
    authz=skills.workspace.state.authorization;identity=skills.workspace.state.identity
    user=identity.create_principal('Scoped source editor',['files-read','files-write','agent-view'])
    authz.set_role(user.id,'operator');authz.grant_project(user.id,'p',['files-read','agent-view'])
    user=identity.principal_by_id(user.id)
    readonly=skills.list(user,'p')
    assert len(readonly['roots'])==1 and readonly['roots'][0]['scope']=='project'
    assert readonly['roots'][0]['editable'] is False and readonly['roots'][0]['read_only_reason']
    assert readonly['sources'][0]['folder']=='inspect'
    with pytest.raises((PermissionError,HTTPException)):
        skills.save(user,scope='project',project_id='p',name='inspect',markdown=TEXT+' denied',expected_digest=saved['digest'])
    authz.grant_project(user.id,'p',['files-read','files-write','agent-view'])
    assert skills.list(user,'p')['roots'][0]['editable'] is True
    updated=skills.save(user,scope='project',project_id='p',name='inspect',markdown=TEXT+' edited',expected_digest=saved['digest'])
    authz.grant_project(user.id,'p',['files-read','agent-view'])
    assert skills.list(user,'p')['roots'][0]['editable'] is False
    with pytest.raises((PermissionError,HTTPException)):
        skills.save(user,scope='project',project_id='p',name='inspect',markdown=TEXT+' revoked',expected_digest=updated['digest'])
    assert (project/'.agents/skills/inspect/SKILL.md').read_text()==TEXT+' edited'


def test_skill_root_editability_does_not_borrow_another_project_grant(sources):
    skills,_,owner,project=sources
    other=project.parent/'other-project';other.mkdir()
    skills.workspace.state.projects=SimpleNamespace(projects=lambda:[{'id':'p','path':str(project)},{'id':'q','path':str(other)}])
    authz=skills.workspace.state.authorization;identity=skills.workspace.state.identity
    user=identity.create_principal('Two project reader',['files-read','files-write','agent-view'])
    authz.set_role(user.id,'operator')
    authz.grant_project(user.id,'p',['files-read','files-write','agent-view'])
    authz.grant_project(user.id,'q',['files-read','agent-view'])
    user=identity.principal_by_id(user.id)
    first=skills.list(user,'p')['roots'];second=skills.list(user,'q')['roots']
    assert len(first)==len(second)==1
    assert first[0]['project_id']=='p' and first[0]['editable'] is True
    assert second[0]['project_id']=='q' and second[0]['editable'] is False
    with pytest.raises((PermissionError,HTTPException)):
        skills.save(user,scope='project',project_id='q',name='inspect',markdown=TEXT,expected_digest=None)
    assert not (other/'.agents').exists()


def test_compatibility_import_is_read_only_and_never_inherits_trust(sources,monkeypatch):
    skills,extensions,owner,project=sources
    skills.compatibility_home=project.parent/'isolated-home'
    # Windows test projects live beneath the user's home. Global compatibility
    # isolation must never rewrite that explicitly enrolled project root.
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:project.parent))
    legacy=project/'.claude/skills/LegacySkill';legacy.mkdir(parents=True)
    (legacy/'SKILL.md').write_text(TEXT+' Compatibility')
    entries=skills.compatibility(owner,'p')
    assert len(entries)==1 and entries[0]['read_only'] and not entries[0]['trusted']
    source=skills.compatibility(owner,'p',entries[0]['id'])
    saved=skills.save(owner,scope='project',name=source['folder'],project_id='p',markdown=source['markdown'],expected_digest=None)
    assert not extensions.store.list('extension')
    preview=skills.publish_preview(owner,scope='project',project_id='p',name='LegacySkill',version='1')
    extensions.install(owner,preview['id'],preview['digest'])
    assert preview['bundle']['id']=='legacyskill'
    assert extensions.export(owner,'legacyskill')['files']['skills/legacyskill/SKILL.md']==saved['markdown']
    assert (legacy/'SKILL.md').read_text()==TEXT+' Compatibility'

def test_missing_source_inventory_and_unsafe_file_exports_are_bounded(sources):
    skills,_,owner,project=sources
    assert skills.list(owner,'p')['sources']==[]
    save(skills,owner)
    path=project/'.agents/skills/inspect/SKILL.md'
    for text in ('x'*512001,'password=embedded-secret-value'):
        path.write_bytes(text.encode('utf-8'))
        with pytest.raises(ValueError):skills.read(owner,scope='project',project_id='p',name='inspect')
        index=skills.list(owner,'p')
        assert index['sources']==[] and index['diagnostics']
        with pytest.raises(ValueError):skills.publish_preview(owner,scope='project',project_id='p',name='inspect',version='1')
