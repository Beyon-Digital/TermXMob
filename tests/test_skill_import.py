"""Skill import from a folder: bounded reads, no links, no credentials, reviewed install, progressive loading."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from termx.agent.policies.engine import PolicyEngine
from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.agent.tools.registry import ToolContext
from termx.workspace.skill_sources import read_tree, tree_from_files, import_bundle
from termx.workspace.store import Conflict
from test_durable_workspace import workspace  # noqa: F401  (fixture)
from test_skill_sources import sources, skill_io, TEXT  # noqa: F401  (fixtures)

REFERENCE = 'Reference notes for the skill.'
SCRIPT = '#!/bin/sh\necho hello\n'


def make_skill(root: Path, extra: dict | None = None, skill: str = TEXT):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'SKILL.md').write_text(skill)
    (root / 'references').mkdir(exist_ok=True)
    (root / 'references' / 'notes.md').write_text(REFERENCE)
    (root / 'scripts').mkdir(exist_ok=True)
    (root / 'scripts' / 'run.sh').write_text(SCRIPT)
    for name, content in (extra or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


# -- read_tree --------------------------------------------------------------------

def test_read_tree_reads_text_and_lists_binaries_and_hidden_entries_as_skipped(sources, tmp_path):
    root = make_skill(tmp_path / 'tree', {'logo.png': b'\x89PNG\x00\x01\x02', 'latin.bin': b'\xff\xfe\xfa', '.DS_Store': 'x',
                                          '.git/config': 'hidden', 'node_modules/pkg/index.js': 'x'})
    tree = read_tree(root.parent, ['tree'])
    assert set(tree['files']) == {'SKILL.md', 'references/notes.md', 'scripts/run.sh'}
    skipped = {item['path']: item['reason'] for item in tree['skipped']}
    assert skipped['logo.png'] == 'binary' and skipped['latin.bin'] == 'binary'
    assert {'.DS_Store', '.git', 'node_modules'} <= set(skipped)
    assert tree['bytes'] == sum(len(v.encode()) for v in tree['files'].values())


def test_read_tree_rejects_links_anywhere(sources, tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_text('secret outside the folder')
    root = make_skill(tmp_path / 'tree')
    (root / 'references' / 'link.md').symlink_to(outside)
    with pytest.raises(ValueError, match='link'):
        read_tree(root.parent, ['tree'])
    (root / 'references' / 'link.md').unlink()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'a.md').write_text('x')
    (root / 'linked-dir').symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        read_tree(root.parent, ['tree'])
    (root / 'linked-dir').unlink()
    link_root = tmp_path / 'link-root'
    link_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        read_tree(tmp_path, ['link-root'])


def test_read_tree_enforces_depth_count_and_size_limits(sources, tmp_path):
    deep = make_skill(tmp_path / 'deep', {'a/b/c/d/ok.md': 'depth four is allowed'})
    assert 'a/b/c/d/ok.md' in read_tree(deep.parent, ['deep'])['files']
    (deep / 'a/b/c/d/e').mkdir()
    (deep / 'a/b/c/d/e/too-deep.md').write_text('x')
    with pytest.raises(ValueError, match='deeper'):
        read_tree(deep.parent, ['deep'])
    many = make_skill(tmp_path / 'many')
    for index in range(200):
        (many / f'f{index}.md').write_text('x')
    with pytest.raises(ValueError, match='200'):
        read_tree(many.parent, ['many'])
    big = make_skill(tmp_path / 'big')
    for index in range(5):
        (big / f'part{index}.md').write_text('x' * 450_000)
    with pytest.raises(ValueError, match='2 MB'):
        read_tree(big.parent, ['big'])
    huge = make_skill(tmp_path / 'huge', {'one.md': 'x' * 512_001})
    with pytest.raises(ValueError, match='one.md'):
        read_tree(huge.parent, ['huge'])


def test_read_tree_rejects_credential_files_without_reading_them(sources, tmp_path):
    for name in ('.env', '.env.local', '.ssh/id_rsa', 'credentials.json', 'server.pem', '.npmrc'):
        root = make_skill(tmp_path / ('c' + str(abs(hash(name)))), {name: 'harmless text'})
        with pytest.raises(ValueError, match='credential'):
            read_tree(root.parent, [root.name])


def test_read_tree_rejects_special_files(sources, tmp_path):
    if not hasattr(os, 'mkfifo'):
        pytest.skip('no fifo support')
    root = make_skill(tmp_path / 'tree')
    os.mkfifo(root / 'pipe')
    with pytest.raises(ValueError, match='regular'):
        read_tree(root.parent, ['tree'])


# -- files map and bundle building --------------------------------------------------

def test_files_map_rejects_unsafe_paths(sources):
    for path in ('../escape.md', '/abs/SKILL.md', 'a\\b.md', './SKILL.md', 'a//b.md', 'a/../b.md', 'x' * 200, '', 'a/b/c/d/e/f.md', 'nul\x00.md'):
        with pytest.raises(ValueError):
            tree_from_files({'SKILL.md': TEXT, path: 'x'})
    with pytest.raises(ValueError, match='twice'):
        tree_from_files({'SKILL.md': TEXT, 'Notes.md': 'a', 'notes.md': 'b'})
    with pytest.raises(ValueError, match='folder'):
        tree_from_files({'SKILL.md': TEXT, 'a': 'file', 'a/b.md': 'nested'})
    with pytest.raises(ValueError):
        tree_from_files({'SKILL.md': 3})
    with pytest.raises(ValueError):
        tree_from_files(['SKILL.md'])


def test_files_map_lists_binary_text_as_skipped_and_enforces_limits(sources):
    tree = tree_from_files({'SKILL.md': TEXT, 'blob.dat': 'a\x00b', 'lone.txt': 'x\ud800y'}, skipped=['logo.png'])
    assert set(tree['files']) == {'SKILL.md'}
    assert {(i['path'], i['reason']) for i in tree['skipped']} == {('blob.dat', 'binary'), ('lone.txt', 'binary'), ('logo.png', 'binary')}
    with pytest.raises(ValueError, match='200'):
        tree_from_files({f'f{i}.md': 'x' for i in range(201)})
    with pytest.raises(ValueError, match='2 MB'):
        tree_from_files({f'f{i}.md': 'x' * 450_000 for i in range(5)})
    with pytest.raises(ValueError, match='big.md'):
        tree_from_files({'big.md': 'x' * 512_001})
    with pytest.raises(ValueError, match='credential'):
        tree_from_files({'SKILL.md': TEXT, '.env': 'A=1'})


def test_bundle_rejects_embedded_credentials_without_echoing_them(sources):
    secret = 'sk-live-abcdefghijklmnop1234567890'
    tree = tree_from_files({'SKILL.md': TEXT, 'references/keys.md': f'api_key = {secret}'})
    with pytest.raises(ValueError, match='keys.md') as caught:
        import_bundle(tree, name='demo', version='1')
    assert secret not in str(caught.value)


def test_single_skill_layout_and_multi_skill_layout(sources):
    single = import_bundle(tree_from_files({'SKILL.md': TEXT, 'references/notes.md': REFERENCE, 'scripts/run.sh': SCRIPT}),
                           name='My Skill!', version='2')
    assert single['bundle']['id'] == 'my-skill'
    assert set(single['bundle']['files']) == {'skills/my-skill/SKILL.md', 'skills/my-skill/references/notes.md', 'skills/my-skill/scripts/run.sh'}
    kinds = {f['path']: f['kind'] for f in single['skills'][0]['files']}
    assert kinds == {'skills/my-skill/SKILL.md': 'instructions', 'skills/my-skill/references/notes.md': 'reference',
                     'skills/my-skill/scripts/run.sh': 'script'}
    multi = import_bundle(tree_from_files({'one/SKILL.md': TEXT, 'one/a.md': 'a', 'two/SKILL.md': TEXT + ' two', 'loose.txt': 'x', 'empty/readme.md': 'y'}),
                          name='pack', version='1')
    assert multi['bundle']['id'] == 'pack'
    assert {s['id'] for s in multi['skills']} == {'one', 'two'}
    assert 'skills/one/a.md' in multi['bundle']['files']
    assert {i['path'] for i in multi['skipped']} == {'loose.txt', 'empty/readme.md'}
    with pytest.raises(ValueError, match='SKILL.md'):
        import_bundle(tree_from_files({'readme.md': 'no skill here'}), name='x', version='1')
    with pytest.raises(ValueError, match='frontmatter|metadata'):
        import_bundle(tree_from_files({'SKILL.md': '---\nname: x'}), name='x', version='1')
    with pytest.raises(ValueError, match='twice|same'):
        import_bundle(tree_from_files({'A b/SKILL.md': TEXT, 'a-b/SKILL.md': TEXT}), name='x', version='1')


# -- preview and install ------------------------------------------------------------

def test_files_import_previews_then_installs_and_marks_references_and_scripts_inert(sources):
    skills, extensions, owner, project = sources
    preview = skills.preview_import(owner, files={'SKILL.md': TEXT, 'references/notes.md': REFERENCE, 'scripts/run.sh': SCRIPT},
                                    skipped=['logo.png'], name='inspect-pack', version='1')
    assert not extensions.store.list('extension')
    assert preview['bundle']['id'] == 'inspect-pack' and preview['provenance']['kind'] == 'folder-import'
    assert preview['import']['skipped'] == [{'path': 'logo.png', 'reason': 'binary'}]
    assert preview['permission_diff']['added'] == ['files-read']
    extensions.install(owner, preview['id'], preview['digest'])
    context = extensions.execution_context(owner, ['inspect-pack'], 'p', str(project))[0]
    by_path = {item['path']: item for item in context['instructions']}
    assert by_path['skills/inspect-pack/SKILL.md']['content'] == TEXT
    script = by_path['skills/inspect-pack/scripts/run.sh']
    assert script['content'] == SCRIPT and script['kind'] == 'script' and 'not run by TermX' in script['note']
    assert 'not run by TermX' in by_path['skills/inspect-pack/references/notes.md']['note']
    assert 'note' not in by_path['skills/inspect-pack/SKILL.md']


def test_import_needs_host_admin(sources):
    skills, extensions, owner, project = sources
    identity = skills.workspace.state.identity
    user = identity.create_principal('Reader', ['files-read', 'agent-view'])
    skills.workspace.state.authorization.set_role(user.id, 'operator')
    user = identity.principal_by_id(user.id)
    with pytest.raises((PermissionError, HTTPException)):
        skills.preview_import(user, files={'SKILL.md': TEXT}, name='x', version='1')
    assert not extensions.store.list('extension_preview')


def test_detected_sources_use_tokens_and_never_expose_paths(sources, monkeypatch):
    skills, extensions, owner, project = sources
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: project.parent))
    skills.compatibility_home = project.parent / 'isolated-home'
    skills.save(owner, scope='project', name='native', project_id='p', markdown=TEXT, expected_digest=None)
    make_skill(project / '.claude' / 'skills' / 'Legacy')
    rows = skills.detected(owner, 'p')
    assert {r['folder'] for r in rows} == {'native', 'Legacy'}
    assert all(r['token'] and 'path' not in r for r in rows)
    assert str(project) not in json.dumps(rows)
    legacy = next(r for r in rows if r['folder'] == 'Legacy')
    preview = skills.preview_import(owner, token=legacy['token'], project_id='p', version='3')
    assert preview['bundle']['id'] == 'legacy' and 'skills/legacy/scripts/run.sh' in preview['bundle']['files']
    assert preview['provenance']['kind'] == 'detected-skill-import'
    assert str(project) not in json.dumps(preview['provenance'])
    extensions.install(owner, preview['id'], preview['digest'])
    assert extensions.store.get('extension', 'legacy')['version'] == '3'
    native = next(r for r in rows if r['folder'] == 'native')
    assert skills.preview_import(owner, token=native['token'], project_id='p', version='1')['bundle']['id'] == 'native'
    with pytest.raises(KeyError):
        skills.preview_import(owner, token='0' * 64, project_id='p', version='1')


def test_a_client_path_is_never_a_source(sources):
    skills, _, owner, project = sources
    with pytest.raises(KeyError):
        skills.preview_import(owner, token=str(project), project_id='p', version='1')
    with pytest.raises(ValueError):
        skills.preview_import(owner, version='1')
    with pytest.raises(ValueError):
        skills.preview_import(owner, files={'SKILL.md': TEXT}, token='a' * 64, version='1')


def test_detected_source_changed_after_review_is_refused_at_install(sources, monkeypatch):
    skills, extensions, owner, project = sources
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: project.parent))
    skills.compatibility_home = project.parent / 'isolated-home'
    root = make_skill(project / '.claude' / 'skills' / 'Legacy')
    token = skills.detected(owner, 'p')[0]['token']
    preview = skills.preview_import(owner, token=token, project_id='p', version='1')
    (root / 'scripts' / 'run.sh').write_text('#!/bin/sh\necho changed\n')
    with pytest.raises(Conflict, match='changed'):
        extensions.install(owner, preview['id'], preview['digest'])
    assert not extensions.store.list('extension')
    fresh = skills.preview_import(owner, token=token, project_id='p', version='1')
    extensions.install(owner, fresh['id'], fresh['digest'])
    assert extensions.store.get('extension', 'legacy')


def test_import_with_a_credential_or_link_creates_no_preview(sources, monkeypatch):
    skills, extensions, owner, project = sources
    with pytest.raises(ValueError, match='credential'):
        skills.preview_import(owner, files={'SKILL.md': TEXT, 'references/a.md': 'password = hunter2hunter2'}, name='x', version='1')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: project.parent))
    skills.compatibility_home = project.parent / 'isolated-home'
    root = make_skill(project / '.claude' / 'skills' / 'Legacy')
    token = skills.detected(owner, 'p')[0]['token']
    (root / 'references' / 'l.md').symlink_to(project)
    with pytest.raises(ValueError, match='link'):
        skills.preview_import(owner, token=token, project_id='p', version='1')
    assert not extensions.store.list('extension_preview')


# -- progressive loading and read_skill ------------------------------------------------

def install_pack(skills, extensions, owner, project, big=False, name='pack'):
    files = {'SKILL.md': TEXT, 'references/notes.md': REFERENCE, 'scripts/run.sh': SCRIPT}
    if big:
        files['references/large.md'] = 'L' * 30_000
    preview = skills.preview_import(owner, files=files, name=name, version='1')
    extensions.install(owner, preview['id'], preview['digest'])
    return preview


def test_small_selection_stays_inline_and_large_selection_becomes_an_index(sources):
    skills, extensions, owner, project = sources
    install_pack(skills, extensions, owner, project)
    contexts = extensions.execution_context(owner, ['pack'], 'p', str(project))
    assert extensions.progressive(contexts) is None
    install_pack(skills, extensions, owner, project, big=True, name='large')
    contexts = extensions.execution_context(owner, ['large'], 'p', str(project))
    index = extensions.progressive(contexts)
    assert index is not None
    entry = index[0]
    assert entry['id'] == 'large' and entry['loading'] == 'on_demand' and 'instructions' not in entry
    listed = {item['path']: item for item in entry['files']}
    assert listed['skills/large/references/large.md']['bytes'] == 30_000
    assert 'L' * 100 not in json.dumps(index)
    assert listed['skills/large/scripts/run.sh']['kind'] == 'script' and 'not run by TermX' in listed['skills/large/scripts/run.sh']['note']
    skill = listed['skills/large/SKILL.md']
    assert skill['name'] == 'Useful skill' and skill['description'] == 'Helps inspect source' and skill['kind'] == 'instructions'


def delegate_task(skills, extensions, owner, project, row_ids, name='large'):
    service = skills.workspace
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    task = service.agents.create_task(prompt='work', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, 'p', task['id'])
    contexts = extensions.execution_context(owner, row_ids, 'p', str(project))
    extensions.hold_dispatch(owner, contexts, 'req-' + task['id'])
    extensions.finish_dispatch(owner, 'req-' + task['id'], task['id'])
    return service, row, task


def tool_context(service, task):
    stored = service.agents.get_task(task['id'])
    manager = SimpleNamespace(orchestrator=SimpleNamespace(workspace=service),
                              is_unattended=service.is_unattended_task)
    return ToolContext(task_id=task['id'], cwd=stored['cwd'], task=stored, read_only=False, cancel=asyncio.Event(),
                       emit=lambda *_: {}, store=service.agents, manager=manager, project_id='p',
                       policy_engine=PolicyEngine(service.agents))


def call_tool(name, arguments, ctx, call_id='c1'):
    return asyncio.run(default_registry().get(name).execute(ProviderCall('function', call_id, name, arguments), ctx))


def test_read_skill_serves_only_files_in_the_verified_manifest_of_a_leased_skill(sources):
    skills, extensions, owner, project = sources
    skills.workspace.state.extensions = extensions
    install_pack(skills, extensions, owner, project, big=True, name='large')
    install_pack(skills, extensions, owner, project, name='other')
    service, row, task = delegate_task(skills, extensions, owner, project, ['large'])
    ctx = tool_context(service, task)
    ok = call_tool('read_skill', {'skill_id': 'large', 'path': 'skills/large/scripts/run.sh'}, ctx)
    assert ok.result['ok'] and ok.result['content'] == SCRIPT and ok.result['untrusted'] is True
    assert 'not run by TermX' in ok.result['note']
    short = call_tool('read_skill', {'skill_id': 'large', 'path': 'references/notes.md'}, ctx, 'c2')
    assert short.result['ok'] and short.result['content'] == REFERENCE
    for path in ('skills/large/../other/SKILL.md', '../../etc/passwd', '/etc/passwd', 'skills/large/missing.md',
                 'manifest.json', 'skills/other/SKILL.md', 'skills/large/', ''):
        refused = call_tool('read_skill', {'skill_id': 'large', 'path': path}, ctx, 'c-' + path)
        assert refused.result.get('refused') and 'content' not in refused.result, path
    not_selected = call_tool('read_skill', {'skill_id': 'other', 'path': 'skills/other/SKILL.md'}, ctx, 'c3')
    assert not_selected.result.get('refused')
    assert call_tool('read_skill', {'skill_id': '../large', 'path': 'SKILL.md'}, ctx, 'c4').result.get('refused')


def test_read_skill_checks_the_manifest_digest_on_every_read(sources):
    skills, extensions, owner, project = sources
    skills.workspace.state.extensions = extensions
    install_pack(skills, extensions, owner, project, big=True, name='large')
    service, row, task = delegate_task(skills, extensions, owner, project, ['large'])
    ctx = tool_context(service, task)
    manifest = Path(extensions.store.get('extension', 'large')['path']) / 'manifest.json'
    data = json.loads(manifest.read_text())
    data['files']['skills/large/scripts/run.sh'] = '#!/bin/sh\necho tampered\n'
    manifest.write_text(json.dumps(data))
    refused = call_tool('read_skill', {'skill_id': 'large', 'path': 'skills/large/scripts/run.sh'}, ctx)
    assert refused.result.get('refused') and 'tampered' not in json.dumps(refused.result)


def test_read_skill_refuses_a_task_without_a_lease_and_revoked_authority(sources):
    skills, extensions, owner, project = sources
    skills.workspace.state.extensions = extensions
    install_pack(skills, extensions, owner, project, big=True, name='large')
    service = skills.workspace
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    task = service.agents.create_task(prompt='work', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, 'p', task['id'])
    assert call_tool('read_skill', {'skill_id': 'large', 'path': 'SKILL.md'}, tool_context(service, task)).result.get('refused')
    ghost = tool_context(service, task)
    ghost.task_id = 'missing-task'
    assert call_tool('read_skill', {'skill_id': 'large', 'path': 'SKILL.md'}, ghost).result.get('refused')


def test_read_skill_is_a_registered_read_only_tool(sources):
    spec = default_registry().get('read_skill')
    assert spec.mutability == 'read' and spec.approval == 'never'
    assert spec.parameters['required'] == ['skill_id', 'path'] and spec.parameters['additionalProperties'] is False
    assert 'read_skill' in [t['name'] for t in default_registry().provider_tools(read_only=True)]


# -- propose_skill_install --------------------------------------------------------------

def test_propose_skill_install_previews_only_and_install_stays_a_click(sources, monkeypatch):
    skills, extensions, owner, project = sources
    skills.workspace.state.extensions = extensions
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: project.parent))
    skills.compatibility_home = project.parent / 'isolated-home'
    make_skill(project / '.claude' / 'skills' / 'Legacy')
    service = skills.workspace
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    task = service.agents.create_task(prompt='work', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, 'p', task['id'])
    ctx = tool_context(service, task)
    listing = call_tool('propose_skill_install', {}, ctx)
    assert listing.result['ok'] and [s['folder'] for s in listing.result['sources']] == ['Legacy']
    assert str(project) not in json.dumps(listing.result)
    token = listing.result['sources'][0]['token']
    proposed = call_tool('propose_skill_install', {'token': token}, ctx, 'c2')
    assert proposed.result['ok'] and proposed.result['installed'] is False and proposed.result['preview_id']
    assert 'content' not in json.dumps(proposed.result) and 'echo hello' not in json.dumps(proposed.result)
    assert not extensions.store.list('extension')
    assert extensions.store.get('extension_preview', proposed.result['preview_id'])
    refused = call_tool('propose_skill_install', {'token': str(project)}, ctx, 'c3')
    assert refused.result.get('refused') and not extensions.store.list('extension')


def test_propose_skill_install_asks_the_owner_and_is_hidden_from_read_only_mode(sources):
    skills, extensions, owner, project = sources
    spec = default_registry().get('propose_skill_install')
    assert spec.approval == 'always' and not spec.expose_read_only and spec.parameters['additionalProperties'] is False
    assert 'propose_skill_install' not in [t['name'] for t in default_registry().provider_tools(read_only=True)]
    service = skills.workspace
    row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
    task = service.agents.create_task(prompt='work', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, 'p', task['id'])
    skills.workspace.state.extensions = extensions
    skills.save(owner, scope='project', name='native', project_id='p', markdown=TEXT, expected_digest=None)
    token = skills.detected(owner, 'p')[0]['token']
    decision = spec.decide(ProviderCall('function', 'c', 'propose_skill_install', {'token': token}), tool_context(service, task))
    assert decision.approval_required and not decision.allowed


# -- progressive prompt through send() ---------------------------------------------------

def test_send_injects_an_index_only_for_large_selections(sources):
    skills, extensions, owner, project = sources
    service = skills.workspace
    service.state.extensions = extensions
    install_pack(skills, extensions, owner, project, big=True, name='large')
    install_pack(skills, extensions, owner, project, name='small')
    prompts = []
    create = service.state.agent.create_task

    async def dispatch(**kwargs):
        prompts.append(kwargs['prompt'])
        return await create(**kwargs)

    service.state.agent.create_task = dispatch
    for ids, request in ((['small'], 'r1-small-request'), (['large'], 'r2-large-request')):
        row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture')
        service.update_session(owner, row['id'], revision=row['revision'], changes={'extension_ids': ids})
        asyncio.run(service.send(owner, row['id'], prompt='Use the skill', request_id=request))
    assert 'echo hello' in prompts[0] and 'read_skill' not in prompts[0]
    assert 'read_skill' in prompts[1] and 'L' * 100 not in prompts[1] and 'echo hello' not in prompts[1]
    assert 'not run by TermX' in prompts[1]


def test_a_preset_without_read_skill_keeps_large_skills_inline(sources):
    skills, extensions, owner, project = sources
    service = skills.workspace
    service.state.extensions = extensions
    install_pack(skills, extensions, owner, project, big=True, name='large')
    prompts = []
    create = service.state.agent.create_task

    async def dispatch(**kwargs):
        prompts.append(kwargs['prompt'])
        return await create(**kwargs)

    service.state.agent.create_task = dispatch
    for tools, request in ((['read_file'], 'r3-restricted'), (['read_file', 'read_skill'], 'r4-allowed')):
        profile = service.agents.create_custom_agent(name='Helper ' + request, instructions='Explain.', provider_id='fixture',
                                                     model='fixture', tools=tools)
        row = service.create_session(owner, project_id='p', cwd=str(project), provider_id='fixture', model='fixture',
                                     custom_agent_id=profile['id'])
        service.update_session(owner, row['id'], revision=row['revision'], changes={'extension_ids': ['large']})
        asyncio.run(service.send(owner, row['id'], prompt='Use the skill', request_id=request))
    assert 'L' * 100 in prompts[0] and 'read_skill(' not in prompts[0]
    assert 'L' * 100 not in prompts[1] and 'read_skill(' in prompts[1]


# -- REST and GraphQL --------------------------------------------------------------------

def test_rest_and_graphql_preview_a_posted_folder(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from termx.app import AppState, create_app
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    state = AppState()
    state.identity.setup_owner('owner', 'strong-password-fixture')
    client = TestClient(create_app(state))
    token = asyncio.run(state.identity.login('local-password', {'username': 'owner', 'password': 'strong-password-fixture'},
                                             peer='local', device_name='owner')).access_token
    headers = {'Authorization': 'Bearer ' + token}
    files = {'SKILL.md': TEXT, 'references/notes.md': REFERENCE, 'scripts/run.sh': SCRIPT}
    rest = client.post('/api/workspace/skill-sources/import-preview', headers=headers,
                       json={'files': files, 'skipped': ['logo.png'], 'name': 'folder pick', 'version': '1'})
    assert rest.status_code == 200, rest.text
    assert rest.json()['bundle']['id'] == 'folder-pick' and rest.json()['import']['skipped'][0]['path'] == 'logo.png'
    bad = client.post('/api/workspace/skill-sources/import-preview', headers=headers,
                      json={'files': {'SKILL.md': TEXT, '../x': 'y'}, 'name': 'x', 'version': '1'})
    assert bad.status_code == 400
    query = '''mutation($input: SkillImportInput!){preview_skill_import(input:$input){
        preview_id digest bundle_id version permissions_added file_count bytes progressive
        skills{id name description files{path bytes kind note}} skipped{path reason}}}'''
    result = client.post('/graphql', headers=headers, json={'query': query, 'variables': {'input': {
        'name': 'folder pick', 'version': '1', 'files': files, 'skipped': ['logo.png']}}}).json()
    assert not result.get('errors'), result
    preview = result['data']['preview_skill_import']
    assert preview['bundle_id'] == 'folder-pick' and preview['permissions_added'] == ['files-read'] and preview['progressive'] is False
    assert preview['file_count'] == 3 and preview['skipped'] == [{'path': 'logo.png', 'reason': 'binary'}]
    assert any(f['kind'] == 'script' and 'not run by TermX' in f['note'] for f in preview['skills'][0]['files'])
    assert 'echo hello' not in json.dumps(result)
    assert state.workspace.store.get('extension_preview', preview['preview_id'])
    assert not state.workspace.store.list('extension')
    failed = client.post('/graphql', headers=headers, json={'query': query, 'variables': {'input': {
        'name': 'x', 'version': '1', 'files': {'SKILL.md': TEXT, '.env': 'A=1'}}}}).json()
    assert failed['errors'][0]['extensions']['http_status'] == 400
    listing = client.get('/api/workspace/skill-sources/detected', headers=headers)
    assert listing.status_code == 200 and isinstance(listing.json()['sources'], list)
    assert client.post('/graphql', json={'query': query, 'variables': {'input': {
        'name': 'x', 'version': '1', 'files': files}}}).status_code == 401
