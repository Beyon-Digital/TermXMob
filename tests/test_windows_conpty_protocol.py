"""Portable hostile/fragmented transport checks; OS effects run on Windows CI."""
import pytest
from termx.sandbox._win_conpty_protocol import Decoder, MAX_FRAME, encode, validate_start


def valid():
    return {'type':'start','command':'python.exe fixture.py','cwd':'C:\\workspace',
        'env':{'PATH':'C:\\Windows'},'job':{'active_process_limit':16},'rows':24,'cols':80}


def test_console_frames_survive_arbitrary_fragmentation_and_multiple_messages():
    expected=[valid(),{'type':'input','data':'YWJjAA=='},{'type':'resize','rows':45,'cols':120}]
    stream=b''.join(map(encode,expected));decoder=Decoder();actual=[]
    for byte in stream:actual.extend(decoder.feed(bytes([byte])))
    assert actual==expected and not decoder.buffer
    assert Decoder().feed(stream)==expected


@pytest.mark.parametrize('data',[b'[]\n',b'{"value":NaN}\n',b'{"value":Infinity}\n',b'no json\n'])
def test_console_decoder_refuses_invalid_frame(data):
    with pytest.raises(ValueError):Decoder().feed(data)


def test_console_decoder_bounds_partial_and_complete_frames():
    with pytest.raises(ValueError,match='limit'):Decoder().feed(b'x'*(MAX_FRAME+1))
    with pytest.raises(ValueError,match='limit'):Decoder().feed(b'x'*(MAX_FRAME+1)+b'\n')
    with pytest.raises(ValueError,match='limit'):encode({'data':'x'*MAX_FRAME})


@pytest.mark.parametrize('patch',[
    {'type':'input'}, {'command':'bad\0.exe'}, {'cwd':''},
    {'env':{'BAD=NAME':'value'}},{'env':{'PATH':42}}, {'env':{'PATH':'bad\0path'}},
    {'rows':True},{'cols':32768},{'job':{'unlimited':1}},
    {'job':{'active_process_limit':-1}},{'job':{'process_memory_bytes':1.2}},
])
def test_console_launch_validation_cannot_change_process_boundary(patch):
    value=valid();value.update(patch)
    with pytest.raises(ValueError):validate_start(value)


def test_valid_console_launch_preserves_exact_configuration():
    value=valid();assert validate_start(value) is value


def test_frozen_shim_dispatch_uses_only_explicit_bundled_module(tmp_path,monkeypatch):
    import sys
    from termx.sandbox.windows_runner import WindowsSandboxRunner
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    request=tmp_path/'request.json'
    assert WindowsSandboxRunner._shim_argv(None,request)==[
        sys.executable,'--runtime-module','termx.sandbox._win_shim',str(request)]

@pytest.mark.parametrize('value,ready',[
    ({'type':'ready','pid':True},False),({'type':'ready','pid':1},True),
    ({'type':'ready','pid':2**32},False),({'type':'exit','code':False,'diagnostics':{}},True),
    ({'type':'exit','code':0,'diagnostics':{}},False),
    ({'type':'phase','stage':'creating-client'},True),({'type':'phase','stage':'arbitrary'},False),
    ({'type':'error','error':'x'*257,'category':'OSError','winerror':5},False),
    ({'type':'data','data':'YWJj'},False),({'type':'data','data':'x'*10925},True),
])
def test_console_responses_refuse_invalid_or_unbounded_broker_data(value,ready):
    from termx.sandbox._win_conpty_protocol import validate_response
    with pytest.raises(ValueError):validate_response(value,ready)


def test_console_output_limit_is_exact_worker_chunk_size():
    import base64
    from termx.sandbox._win_conpty_protocol import validate_response
    data=b'x'*8192
    assert validate_response({'type':'data','data':base64.b64encode(data).decode()},True)==data
    with pytest.raises(ValueError):
        validate_response({'type':'data','data':base64.b64encode(data+b'x').decode()},True)
