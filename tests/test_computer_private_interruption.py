"""Private-mode transitions stop multi-event OS operations without replay."""
import asyncio
import pytest
from termx.agent.computer import ComputerController
from termx.desktop.recording import set_capture_private, clear_capture_private

@pytest.mark.parametrize('action',[
    {'type':'type','text':'secret'},
    {'type':'paste_text','text':'secret'},
    {'type':'double_click','x':10,'y':10},
    {'type':'drag','path':[{'x':1,'y':1},{'x':2,'y':2},{'x':3,'y':3}]},
    {'type':'keypress','keys':['a','b']},
    {'type':'key_down','keys':['a','b']},
])
@pytest.mark.parametrize('transient',[False,True])
def test_private_epoch_stops_remaining_events_and_releases_keys(monkeypatch,action,transient):
    import termx.agent.computer as module
    identifier='computer-interruption-fixture';events=[];clipboard_reads=[]
    def first(event,*_):
        events.append(event)
        if event.get('type')=='release_all':return
        set_capture_private(identifier,expires_at=float('inf'),valid=lambda:True)
        if transient:clear_capture_private(identifier)
    def clipboard(text):first({'type':'clipboard','data':text})
    monkeypatch.setattr(module,'apply_event',first)
    monkeypatch.setattr(module,'clipboard_set',clipboard)
    monkeypatch.setattr(module,'clipboard_get',lambda:clipboard_reads.append(True) or 'secret')
    monkeypatch.setattr(module,'list_displays',lambda:[{'id':'fixture','width':100,'height':100,'main':True}])
    monkeypatch.setattr(module,'pointer_target',lambda _:None)
    monkeypatch.setattr(module,'grab_jpeg',lambda _:pytest.fail('Interrupted input must not capture private pixels'))
    try:
        with pytest.raises(PermissionError,match='event may have been sent'):
            asyncio.run(ComputerController().execute([action]))
        assert len(events)==2 and events[-1]=={'type':'release_all'}
        assert clipboard_reads==[]
    finally:clear_capture_private(identifier)
