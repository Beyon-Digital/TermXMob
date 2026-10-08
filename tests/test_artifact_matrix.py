from pathlib import Path
import sys
import yaml
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop/scripts'))
from artifact_matrix import build_matrix  # noqa: E402


@pytest.mark.parametrize('scope,expected', [
    ('all', ['macos-arm64','macos-x86_64','windows','linux']),
    ('macos-and-windows',['macos-arm64','macos-x86_64','windows']),
    ('windows',['windows']),
])
def test_artifact_scope_is_explicit_and_mac_uses_app_first(scope, expected):
    rows = build_matrix(event='workflow_dispatch',release_tag='',scope=scope)['include']
    assert [row['name'] for row in rows] == expected
    assert all(row['bundles']=='app' for row in rows if row['name'].startswith('macos'))
    assert next(row for row in rows if row['name']=='windows')['bundles']=='msi,nsis'


@pytest.mark.parametrize('scope', ['macos-and-windows','windows'])
@pytest.mark.parametrize('event,tag', [('push',''),('push','v0.2.6'),('workflow_dispatch','v0.2.6')])
def test_tag_or_release_cannot_exclude_linux_or_change_production_bundler(event, tag, scope):
    rows = build_matrix(event=event,release_tag=tag,scope=scope)['include']
    assert [row['name'] for row in rows] == ['macos-arm64','macos-x86_64','windows','linux']
    assert all(row['bundles']=='dmg' for row in rows if row['name'].startswith('macos'))


def test_unknown_artifact_scope_is_rejected_and_optional_intel_flag_retained():
    with pytest.raises(ValueError,match='scope'):
        build_matrix(event='workflow_dispatch',release_tag='',scope='skip-platform-tests')
    rows=build_matrix(event='workflow_dispatch',release_tag='',scope='all',include_intel=False)['include']
    assert [row['name'] for row in rows]==['macos-arm64','windows','linux']


def test_ci_workflow_retains_full_checks_and_gates_final_container_ticket():
    workflow=yaml.safe_load((Path(__file__).parents[1]/'.github/workflows/desktop.yml').read_text())
    # PyYAML1.1 treats `on` as a bool; accept the parser's canonical equivalent.
    inputs=workflow.get('on',workflow.get(True))['workflow_dispatch']['inputs']
    assert inputs['artifactqualification_scope']['default']=='all'
    assert inputs['artifactqualification_scope']['options']==['all','macos-and-windows','windows']
    steps=workflow['jobs']['build']['steps']; names=[step.get('name','') for step in steps]
    for name in ('Verify complete backend suite','Verify native bridge boundaries','Verify rendered workspace accessibility'):
        step=steps[names.index(name)]
        assert 'continue-on-error' not in step and 'if' not in step
    cli=steps[names.index('Install Tauri CLI')]['run']
    assert '@tauri-apps/cli@2.12.1' in cli and 'tauri --version' in cli
    create=steps[names.index('Create signed artifact-only macOS container without layout mounting')]
    ticket=steps[names.index('Notarize and verify final artifact-only macOS DMG')]
    assert create['if']==ticket['if']=="runner.os == 'macOS' && steps.release.outputs.tag == ''"
    assert names.index('Build installers') < names.index(create['name']) < names.index(ticket['name'])
    assert 'create_ci_dmg.py' in create['run']
