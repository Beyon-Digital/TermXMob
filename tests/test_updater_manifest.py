import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location('updater_manifest', Path(__file__).parents[1] / 'desktop/scripts/updater_manifest.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fragments():
    return [{'version': '0.2.8', 'platforms': {platform: {'signature': 'signed', 'url': f'https://github.com/Beyon-Digital/TermXMob/releases/download/v0.2.8/{platform}.zip'}}} for platform in sorted(module.PLATFORMS)]


def merge(values):
    return module.merge(values, tag='v0.2.8', repository='Beyon-Digital/TermXMob')


def test_complete_manifest():
    assert set(merge(fragments())['platforms']) == module.PLATFORMS


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'version', 'shared_payload', 'unsigned', 'wrong_release'])
def test_rejects_incomplete_or_misrouted_updates(change):
    values = fragments()
    first = next(iter(values[0]['platforms'].values()))
    second = next(iter(values[1]['platforms'].values()))
    if change == 'missing': values.pop()
    elif change == 'duplicate': values.append(values[0])
    elif change == 'version': values[0]['version'] = '0.2.7'
    elif change == 'shared_payload': second['url'] = first['url']
    elif change == 'unsigned': first['signature'] = ''
    elif change == 'wrong_release': first['url'] = first['url'].replace('/v0.2.8/', '/v0.2.7/')
    with pytest.raises(ValueError): merge(values)


def test_only_the_complete_qualified_release_is_published():
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/desktop.yml').read_text())
    build = next(step for step in workflow['jobs']['build']['steps'] if step.get('name') == 'Build installers')
    assert build['with']['releaseDraft'] is True
    assert build['with']['includeUpdaterJson'] is False
    publish = workflow['jobs']['updater']
    assert publish['needs'] == 'build'
    assert 'success()' in publish['if'] and 'always()' not in publish['if']
    assert 'inputs.release_tag' in publish['if']
