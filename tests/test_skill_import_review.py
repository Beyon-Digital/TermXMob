"""Hardening found in review of the skill import: parser boundary, credentials, ids, size, approval, request bounds."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.workspace.extensions import SLUG, ExtensionService
from termx.workspace.skill_sources import import_bundle, tree_from_files, validate_markdown
from test_skill_import import make_skill, tool_context  # noqa: F401
from test_skill_sources import sources, skill_io, TEXT  # noqa: F401  (fixtures)
from test_durable_workspace import workspace  # noqa: F401  (fixture)


def test_a_fake_fence_cannot_hide_yaml_aliases_from_the_scan():
    with pytest.raises(ValueError, match='alias|anchor'):
        validate_markdown('---\na: 1\n---x: &a0 [1]\nb: *a0\n---\nbody')
    levels = ['---x: &a0 [1,1,1,1,1,1,1,1,1]']
    levels += [f'k{i}: &a{i} [' + ','.join([f'*a{i - 1}'] * 9) + ']' for i in range(1, 8)]
    started = time.monotonic()
    with pytest.raises(ValueError):
        validate_markdown('---\na: 1\n' + '\n'.join(levels) + '\n---\nbody')
    assert time.monotonic() - started < 0.5


def test_frontmatter_still_parses_normally():
    meta = validate_markdown('---\nname: Demo\ndescription: Does things\n---\nBody text\n')
    assert meta['name'] == 'Demo' and meta['description'] == 'Does things'
    with pytest.raises(ValueError, match='closed|frontmatter'):
        validate_markdown('---\nname: x\n---x\nBody')


def test_credential_names_are_matched_without_regard_to_case():
    for path in ('.ENV', '.Env.local', 'Id_Ecdsa', 'creds.p12', 'a/.PYPIRC', 'kubeconfig', 'store.KEYSTORE', '.git-credentials'):
        with pytest.raises(ValueError, match='credential'):
            tree_from_files({'SKILL.md': TEXT, path: 'x'})
    with pytest.raises(ValueError, match='credential'):
        tree_from_files({'SKILL.md': TEXT}, skipped=['.env'])
    service = ExtensionService.__new__(ExtensionService)
    service.adapters = {'termx-bundle/v1': lambda data: data}
    with pytest.raises(ValueError, match='Credential'):
        service.normalize({'format': 'termx-bundle/v1', 'id': 'x', 'version': '1', 'files': {'skills/x/.ENV': 'A=1'}}, 'termx-bundle/v1')


def test_posted_hidden_files_are_skipped_not_imported():
    tree = tree_from_files({'SKILL.md': TEXT, '.git/config': 'url = nothing', '.DS_Store': 'x'}, skipped=['.hidden-dir', 'node_modules'])
    assert set(tree['files']) == {'SKILL.md'}
    reasons = {item['path']: item['reason'] for item in tree['skipped']}
    assert reasons['.git/config'].startswith('hidden') and reasons['.DS_Store'].startswith('hidden') and reasons['.hidden-dir'].startswith('hidden')
    assert reasons['node_modules'] == 'dependency folder'
    assert tree_from_files({'SKILL.md': TEXT}, skipped=['logo.png'])['skipped'] == [{'path': 'logo.png', 'reason': 'binary'}]


def test_embedded_credentials_in_urls_and_key_value_forms_are_refused():
    for line in ('git clone https://user:ghp_abcdefghij1234@github.com/x/y.git', 'AWS_SECRET_ACCESS_KEY=abcdefghij1234567890',
                 'postgres://u:pw1234567@db.example/app'):
        tree = tree_from_files({'SKILL.md': TEXT, 'references/setup.md': line})
        with pytest.raises(ValueError, match='setup.md') as caught:
            import_bundle(tree, name='demo', version='1')
        assert 'ghp_abcdefghij1234' not in str(caught.value) and 'pw1234567' not in str(caught.value)
    ok = tree_from_files({'SKILL.md': TEXT, 'references/links.md': 'See https://example.com:8080/docs and git@github.com:x/y.git'})
    assert import_bundle(ok, name='demo', version='1')['bundle']['id'] == 'demo'


def test_non_ascii_folder_names_make_ascii_ids_and_versions_are_checked(sources):
    for name, expected in (('日本語', 'imported-skill'), ('Café skill', 'cafe-skill')):
        built = import_bundle(tree_from_files({'SKILL.md': TEXT}), name=name, version='1')
        assert built['bundle']['id'] == expected and SLUG.fullmatch(built['bundle']['id'])
        assert all(SLUG.fullmatch(skill['id']) for skill in built['skills'])
    multi = import_bundle(tree_from_files({'Café/SKILL.md': TEXT, 'plain/SKILL.md': TEXT}), name='pack', version='1')
    assert {skill['id'] for skill in multi['skills']} == {'cafe', 'plain'}
    skills, extensions, owner, project = sources
    preview = skills.preview_import(owner, files={'SKILL.md': TEXT}, name='日本語', version='1')
    assert preview['bundle']['id'] == 'imported-skill'
    with pytest.raises(ValueError, match='[Vv]ersion'):
        skills.preview_import(owner, files={'SKILL.md': TEXT}, name='demo', version='not a version!')


def test_a_very_large_skill_installs_and_loads_as_an_index_not_an_error(sources):
    skills, extensions, owner, project = sources
    service = skills.workspace
    service.state.extensions = extensions
    preview = skills.preview_import(owner, files={'SKILL.md': TEXT, 'references/a.md': 'A' * 400_000, 'references/b.md': 'B' * 400_000},
                                    name='huge', version='1')
    extensions.install(owner, preview['id'], preview['digest'])
    contexts = extensions.execution_context(owner, ['huge'], 'p', str(project))
    indexed = extensions.skill_prompt(contexts, on_demand=True)
    assert 'read_skill' in indexed and 'A' * 100 not in indexed
    with pytest.raises(ValueError, match='too large to load inline'):
        extensions.skill_prompt(contexts, on_demand=False)
    prompts = []
    create = service.state.agent.create_task

    async def dispatch(**kwargs):
        prompts.append(kwargs['prompt'])
        return await create(**kwargs)

    service.state.agent.create_task = dispatch
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    service.update_session(owner, row['id'], revision=row['revision'], changes={'extension_ids': ['huge']})
    asyncio.run(service.send(owner, row['id'], prompt='Use the skill', request_id='r-huge-request'))
    assert 'read_skill' in prompts[0] and 'A' * 100 not in prompts[0]


def _task(skills, owner, project):
    service = skills.workspace
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    task = service.agents.create_task(prompt='work', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, 'p', task['id'])
    return service, task


def test_propose_skill_install_is_refused_for_unattended_runs(sources):
    skills, extensions, owner, project = sources
    service, task = _task(skills, owner, project)
    ctx = tool_context(service, task)
    ctx.manager.is_unattended = lambda task_id: True
    decision = default_registry().get('propose_skill_install').decide(
        ProviderCall('function', 'c', 'propose_skill_install', {'token': 'a' * 64}), ctx)
    assert not decision.allowed and not decision.approval_required


def test_the_approval_names_the_skill_folder_and_unknown_tokens_get_no_prompt(sources, monkeypatch):
    skills, extensions, owner, project = sources
    skills.workspace.state.extensions = extensions
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: project.parent))
    skills.compatibility_home = project.parent / 'isolated-home'
    make_skill(project / '.claude' / 'skills' / 'Legacy')
    service, task = _task(skills, owner, project)
    ctx = tool_context(service, task)
    spec = default_registry().get('propose_skill_install')
    token = skills.detected(owner, 'p')[0]['token']
    known = spec.decide(ProviderCall('function', 'c', 'propose_skill_install', {'token': token}), ctx)
    assert known.approval_required and not known.allowed
    assert 'Legacy' in known.reason + known.consequence + str(known.intent)
    unknown = spec.decide(ProviderCall('function', 'c', 'propose_skill_install', {'token': 'b' * 64}), ctx)
    assert not unknown.allowed and not unknown.approval_required
    listing = spec.decide(ProviderCall('function', 'c', 'propose_skill_install', {}), ctx)
    assert listing.approval_required and not listing.allowed


def test_the_import_endpoint_bounds_the_body_before_parsing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from termx.app import AppState, create_app
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    state = AppState()
    state.identity.setup_owner('owner', 'strong-password-fixture')
    client = TestClient(create_app(state))
    token = asyncio.run(state.identity.login('local-password', {'username': 'owner', 'password': 'strong-password-fixture'},
                                             peer='local', device_name='owner')).access_token
    headers = {'Authorization': 'Bearer ' + token}
    big = client.post('/api/workspace/skill-sources/import-preview', headers=headers,
                      json={'files': {'SKILL.md': 'x' * 5_000_000}, 'name': 'big', 'version': '1'})
    assert big.status_code == 413
    many = client.post('/api/workspace/skill-sources/import-preview', headers=headers,
                       json={'files': {f'f{i}.md': 'x' for i in range(250)}, 'name': 'many', 'version': '1'})
    assert many.status_code == 422
