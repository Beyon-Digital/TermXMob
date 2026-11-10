import asyncio
import io

import pytest
from PIL import Image

from termx.browser.storage import Records
from termx.desktop.recording import WindowRecordingService, assert_agent_capture_allowed


class Port:
    def __init__(self): self.events = []; self.during_capture = None
    def list(self): return [{'id': identifier, 'pid': int(identifier), 'app': 'Owned test fixture', 'title': 'Test', 'width': 80, 'height': 60} for identifier in ('1', '2')]
    def capture(self, row):
        if self.during_capture: self.during_capture()
        stream = io.BytesIO(); Image.new('RGB', (80, 60), 'white').save(stream, 'JPEG'); return stream.getvalue()
    def input(self, row, event): self.events.append((row['id'], event))


def service(tmp_path):
    port = Port(); alive = [True]
    manager = WindowRecordingService(Records(tmp_path), port=port, session_valid=lambda *args: alive[0])
    return manager, port, alive


def test_opt_in_redaction_private_capture_and_revocation(tmp_path):
    async def run():
        manager, port, alive = service(tmp_path)
        row = manager.start('owner', 'session', 1, '1')
        manager.configure(row['id'], 'owner', redactions=[{'x': 0, 'y': 0, 'width': .5, 'height': 1}])
        image = Image.open(io.BytesIO(await manager.frame(row['id'], 'owner')))
        assert sum(image.getpixel((5, 5))) < 10 and sum(image.getpixel((70, 5))) > 700
        manager.configure(row['id'], 'owner', private=True)
        with pytest.raises(PermissionError): assert_agent_capture_allowed()
        with pytest.raises(PermissionError): await manager.frame(row['id'], 'owner')
        assert await manager.frame(row['id'], 'owner', human=True)
        alive[0] = False
        with pytest.raises(PermissionError): await manager.frame(row['id'], 'owner', human=True)
        with pytest.raises(PermissionError): assert_agent_capture_allowed()
        manager.stop(row['id'], 'owner')
        assert_agent_capture_allowed()
    asyncio.run(run())


def test_private_started_during_capture_discards_inflight_frame(tmp_path):
    async def run():
        manager, port, _ = service(tmp_path)
        row = manager.start('owner', 'session', 1, '1')
        port.during_capture = lambda: manager.configure(row['id'], 'owner', private=True)
        try:
            with pytest.raises(PermissionError): await manager.frame(row['id'], 'owner')
        finally: manager.stop(row['id'], 'owner')
    asyncio.run(run())


def test_recording_a_window_into_a_skill_is_retired(tmp_path):
    """Recorded steps were 0..1 window coordinates. Desktop control acts on accessibility controls by name and role, which a
    coordinate cannot name, so there is nothing to draft, test or publish. The preview, private mode and redaction stay."""
    manager, _, _ = service(tmp_path)
    for name in ('draft', 'get_draft', 'edit', 'test', 'publish'):
        assert not hasattr(manager, name), name
    row = manager.start('owner', 'session', 1, '1')
    assert 'steps' not in row and 'recording' not in row
    with pytest.raises(TypeError): manager.configure(row['id'], 'owner', recording=True)
    with pytest.raises(TypeError): asyncio.run(manager.input(row['id'], 'owner', {'type': 'text', 'data': 'x'}, parameter='query'))


def test_the_human_preview_still_drives_its_window_and_keeps_nothing_it_typed(tmp_path):
    async def run():
        manager, port, _ = service(tmp_path)
        row = manager.start('owner', 'session', 1, '1')
        assert await manager.input(row['id'], 'owner', {'type': 'pointer', 'action': 'click', 'x': .5, 'y': .5}) == {'ok': True}
        assert await manager.input(row['id'], 'owner', {'type': 'text', 'data': 'DO_NOT_SAVE_THIS_CREDENTIAL'}) == {'ok': True}
        assert [event['type'] for _, event in port.events] == ['pointer', 'text']
        persisted = manager.records.path.read_bytes().decode(errors='ignore')
        assert 'DO_NOT_SAVE' not in persisted
        assert 'steps' not in manager.records.get('window-capture', row['id'])
    asyncio.run(run())


def test_rows_left_by_the_recording_feature_are_cleaned_up_on_start(tmp_path):
    """An old capture may hold recorded pointer steps and an old draft may hold the same steps as markdown. Neither is
    reachable any more, so both go when the service starts, and a private capture stays private."""
    records = Records(tmp_path)
    old = {'id': 'old', 'principal_id': 'owner', 'session_id': 's', 'policy_version': 1, 'window': {'id': '1', 'app': 'Notes'}, 'private': True,
           'recording': True, 'stopped': False, 'revision': 3, 'redactions': [], 'steps': [{'event': {'type': 'key', 'key': 'Tab', 'action': 'down'}}],
           'expires_at': 9e12}
    records.put('window-capture', 'old', old)
    records.put('window-skill', 'draft', {'id': 'draft', 'principal_id': 'owner', 'content': 'x', 'steps': old['steps']})
    manager = WindowRecordingService(records, port=Port(), session_valid=lambda *args: True)
    row = records.get('window-capture', 'old')
    assert 'steps' not in row and 'recording' not in row and row['private'] and row['stopped']
    assert records.get('window-skill', 'draft') is None and records.list('window-skill') == []
    with pytest.raises(PermissionError): assert_agent_capture_allowed()
    manager.stop('old', 'owner')
    assert_agent_capture_allowed()


def test_the_draft_routes_are_gone_and_the_preview_routes_remain(tmp_path):
    from fastapi import FastAPI
    from termx.desktop.recording_http import mount_window_recording
    from types import SimpleNamespace
    state = SimpleNamespace(browser=SimpleNamespace(records=Records(tmp_path), session_valid=lambda *args: True))
    app = FastAPI()
    mount_window_recording(app, state)
    paths = {(method.upper(), path) for path, item in app.openapi()['paths'].items() for method in item}
    prefix = '/api/desktop/recording'
    assert not [path for _, path in paths if 'draft' in path]
    assert {('POST', prefix + '/captures'), ('PATCH', prefix + '/captures/{identifier}'), ('DELETE', prefix + '/captures/{identifier}'),
            ('GET', prefix + '/captures/{identifier}/frame'), ('GET', prefix + '/captures/{identifier}/preview'),
            ('POST', prefix + '/captures/{identifier}/input')} <= paths
    state.window_recording.close()


def test_unowned_capture_restart_and_invalid_events_fail_closed(tmp_path):
    manager, _, _ = service(tmp_path)
    row = manager.start('owner', 'session', 1, '1')
    with pytest.raises(KeyError): manager.get(row['id'], 'other')
    with pytest.raises(ValueError): manager.event({'type': 'pointer', 'action': 'click', 'x': 1.1, 'y': .5})
    with pytest.raises(ValueError): manager.event({'type': 'clipboard', 'data': 'secret'})
    restarted = WindowRecordingService(manager.records, port=Port(), session_valid=lambda *args: True)
    with pytest.raises(PermissionError): restarted.get(row['id'], 'owner')


def test_private_entered_then_exited_during_a_desktop_read_still_moves_the_privacy_epoch(tmp_path):
    """Desktop control compares this revision before and after every call, so a quick round trip discards the result."""
    from termx.desktop.recording import capture_privacy_revision
    manager, _, _ = service(tmp_path)
    row = manager.start('owner', 'session', 1, '1')
    before = capture_privacy_revision()
    manager.configure(row['id'], 'owner', private=True)
    manager.configure(row['id'], 'owner', private=False)
    assert capture_privacy_revision() > before
    manager.close()


@pytest.mark.parametrize('expiry', ['session', 'time'])
def test_expired_private_preview_never_revives_control_and_requires_explicit_stop(tmp_path, expiry):
    from termx.desktop.recording import clear_capture_private
    manager, port, alive = service(tmp_path)
    row = manager.start('owner', 'first-session', 1, '1')
    manager.configure(row['id'], 'owner', private=True)
    retained = asyncio.run(manager.frame(row['id'], 'owner', human=True))
    assert retained # A second human window retains its already-served pixels.
    if expiry == 'session': alive[0] = False
    else:
        from time import time
        current = manager.records.get('window-capture', row['id'])
        current['expires_at'] = time() - 1
        manager.records.put('window-capture', row['id'], current)
    with pytest.raises(PermissionError): asyncio.run(manager.input(row['id'], 'owner', {'type':'key','key':'Tab','action':'down'}))
    with pytest.raises(PermissionError): asyncio.run(manager.frame(row['id'], 'owner', human=True))
    expired = manager.records.get('window-capture', row['id'])
    assert expired['private'] and expired['stopped'] and not port.events
    with pytest.raises(PermissionError): assert_agent_capture_allowed()
    # Simulate process memory loss: persisted private state reconstructs only
    # observation suppression, never an execution/capture consent.
    clear_capture_private(row['id'])
    recovered = WindowRecordingService(manager.records, port=port, session_valid=lambda *_: True)
    with pytest.raises(PermissionError): assert_agent_capture_allowed()
    with pytest.raises(PermissionError): asyncio.run(recovered.input(row['id'], 'owner', {'type':'key','key':'Tab','action':'down'}))
    with pytest.raises(KeyError): recovered.stop(row['id'], 'foreign')
    with pytest.raises(PermissionError): assert_agent_capture_allowed()
    recovered.stop(row['id'], 'owner')
    assert_agent_capture_allowed()
    assert not port.events
