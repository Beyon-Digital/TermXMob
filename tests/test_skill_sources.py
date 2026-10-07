from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from termx.authorization import AuthorizationService
from termx.workspace.extensions import ExtensionService
from termx.workspace.skill_sources import SkillSourceService,validate_markdown
from termx.workspace.store import Conflict
from test_durable_workspace import workspace

@pytest.fixture
def sources(workspace):
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


def test_compatibility_import_is_read_only_and_never_inherits_trust(sources):
    skills,extensions,owner,project=sources
    skills.compatibility_home=project.parent/'isolated-home'
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
