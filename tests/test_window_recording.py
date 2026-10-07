import asyncio
import io
import json

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
        with pytest.raises(PermissionError): manager.configure(row['id'], 'owner', recording=True)
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


def test_real_recorded_steps_parameter_values_never_persist_and_test_digest_gates_publish(tmp_path):
    async def run():
        manager, port, _ = service(tmp_path)
        source = manager.start('owner', 'session', 1, '1'); manager.configure(source['id'], 'owner', recording=True)
        await manager.input(source['id'], 'owner', {'type': 'pointer', 'action': 'click', 'x': .5, 'y': .5})
        await manager.input(source['id'], 'owner', {'type': 'text', 'data': 'DO_NOT_SAVE_THIS_CREDENTIAL'})
        await manager.input(source['id'], 'owner', {'type': 'text', 'data': 'DO_NOT_SAVE_PARAMETER_VALUE'}, parameter='query')
        manager.configure(source['id'], 'owner', recording=False)
        draft = manager.draft(source['id'], 'owner', 'recorded-fixture')
        assert draft['parameters'] == ['query'] and len(draft['steps']) == 2
        with pytest.raises(PermissionError): manager.publish(draft['id'], 'owner', object(), object(), '1.0.0')
        with pytest.raises(ValueError): await manager.test(draft['id'], 'owner', source['id'], {'query': 'x'}, True)
        target = manager.start('owner', 'session', 1, '2')
        with pytest.raises(ValueError): await manager.test(draft['id'], 'owner', target['id'], {'query': 'x'}, False)
        tested = await manager.test(draft['id'], 'owner', target['id'], {'query': 'DO_NOT_SAVE_TEST_VALUE'}, True)
        assert tested['test']['steps_executed'] == 2
        assert port.events[-1] == ('2', {'type': 'text', 'data': 'DO_NOT_SAVE_TEST_VALUE'})
        persisted = manager.records.path.read_bytes().decode(errors='ignore')
        assert 'DO_NOT_SAVE' not in persisted
        class Extensions:
            def preview(self, principal, data, format):
                assert data['markdown'] == tested['content'] and format == 'skill-markdown/v1'
                return {'id': 'preview', 'digest': 'digest'}
            def install(self, principal, identifier, digest): return {'active_digest': digest}
        assert manager.publish(draft['id'], 'owner', object(), Extensions(), '1.0.0')['active_digest'] == 'digest'
        manager.edit(draft['id'], 'owner', tested['content'] + '\nEdited instructions\n')
        with pytest.raises(PermissionError): manager.publish(draft['id'], 'owner', object(), Extensions(), '1.0.1')
    asyncio.run(run())


def test_unowned_capture_restart_and_invalid_events_fail_closed(tmp_path):
    manager, _, _ = service(tmp_path)
    row = manager.start('owner', 'session', 1, '1')
    with pytest.raises(KeyError): manager.get(row['id'], 'other')
    with pytest.raises(ValueError): manager.event({'type': 'pointer', 'action': 'click', 'x': 1.1, 'y': .5})
    with pytest.raises(ValueError): manager.event({'type': 'clipboard', 'data': 'secret'})
    restarted = WindowRecordingService(manager.records, port=Port(), session_valid=lambda *args: True)
    with pytest.raises(PermissionError): restarted.get(row['id'], 'owner')


def test_private_entered_then_exited_during_computer_capture_still_discards_frame(tmp_path, monkeypatch):
    from termx.agent.computer import ComputerController
    async def run():
        manager, _, _ = service(tmp_path)
        row = manager.start('owner', 'session', 1, '1')
        def capture(_):
            manager.configure(row['id'], 'owner', private=True)
            manager.configure(row['id'], 'owner', private=False)
            return b'must-not-leak-private-frame'
        monkeypatch.setattr('termx.agent.computer.grab_jpeg', capture)
        with pytest.raises(PermissionError): await ComputerController().screenshot()
        manager.close()
    asyncio.run(run())


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
    assert expired['private'] and expired['stopped'] and not expired['recording'] and not port.events
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
