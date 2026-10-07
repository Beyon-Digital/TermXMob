import asyncio
from pathlib import Path
import pytest
from fastapi import HTTPException
from termx.development.debug import DebugService, DebugSession


async def wait_event(session, name, after=0):
    for _ in range(200):
        found = next((v for v in session.events if v['id'] > after and v.get('event') == name), None)
        if found:
            return found
        await asyncio.sleep(.05)
    raise AssertionError(f'Missing {name}: {list(session.events)[-10:]}')


def test_launch_rejects_outside_project_and_external_attach(tmp_path):
    session = DebugSession('project', tmp_path, 'python')
    with pytest.raises(HTTPException):
        session.validate('launch', {'program':'../private.py'})
    with pytest.raises(HTTPException):
        session.validate('launch', {'program':'main.py','env':{'TOKEN':'secret'}})
    with pytest.raises(HTTPException):
        session.validate('attach', {'connect':{'host':'external.example','port':9000}})
    with pytest.raises(HTTPException):
        session.validate('setBreakpoints', {'source':{'path':'../other.py'}})


def test_real_python_breakpoint_step_stack_and_variables(tmp_path):
    pytest.importorskip('debugpy')
    program = tmp_path / 'main.py'
    program.write_text('number = 41\nnumber += 1\nprint(number)\n')
    async def run():
        service = DebugService()
        session = await service.create('project', tmp_path, 'python')
        try:
            await session.active.request('initialize', session.validate('initialize', {}))
            launch = asyncio.create_task(session.active.request('launch', session.validate('launch', {'program':'main.py'})))
            await wait_event(session, 'initialized')
            breakpoints = await session.active.request('setBreakpoints', session.validate('setBreakpoints', {'source':{'path':'main.py'},'breakpoints':[{'line':2}]}))
            assert breakpoints['breakpoints'][0]['verified']
            await session.active.request('configurationDone', {})
            await launch
            stopped = await wait_event(session, 'stopped')
            thread = stopped['body']['threadId']
            stack = await session.active.request('stackTrace', {'threadId':thread})
            frame = stack['stackFrames'][0]
            assert frame['line'] == 2
            scopes = await session.active.request('scopes', {'frameId':frame['id']})
            locals_scope = next(v for v in scopes['scopes'] if v['name'] == 'Locals')
            values = await session.active.request('variables', {'variablesReference':locals_scope['variablesReference']})
            assert any(v['name'] == 'number' and v['value'] == '41' for v in values['variables'])
            cursor = session.cursor
            await session.active.request('next', {'threadId':thread})
            stepped = await wait_event(session, 'stopped', cursor)
            stack = await session.active.request('stackTrace', {'threadId':stepped['body']['threadId']})
            assert stack['stackFrames'][0]['line'] == 3
            await session.active.request('continue', {'threadId':thread})
            await wait_event(session, 'terminated')
            assert any(v.get('event') == 'output' and '42' in v.get('body',{}).get('output','') for v in session.events)
        finally:
            await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('language',['javascript','typescript'])
def test_real_javascript_breakpoint_stack_and_variables(tmp_path, monkeypatch, language):
    import os
    adapter = os.environ.get('TERMX_JS_DEBUG_SERVER')
    if not adapter or not Path(adapter).is_file():
        pytest.skip('Set TERMX_JS_DEBUG_SERVER to installed pinned Microsoft adapter')
    source='main.ts' if language=='typescript' else 'main.js'
    (tmp_path / source).write_text('let number'+(': number' if language=='typescript' else '')+' = 41;\nnumber += 1;\nconsole.log(number);\n')
    if language=='typescript':
        import subprocess
        from termx.desktop.runtime import assets_root,node_binary
        assets=assets_root()
        compiler=assets/'language/node_modules/typescript/bin/tsc' if assets else Path('/tmp/termx-language-runtime-public/node_modules/typescript/bin/tsc')
        if not compiler.is_file():pytest.skip('Install the pinned packaged TypeScript compiler')
        subprocess.run([node_binary(),str(compiler),source,'--sourceMap','--target','ES2020'],cwd=tmp_path,check=True,capture_output=True,timeout=30)
    async def run():
        service = DebugService()
        session = await service.create('project', tmp_path, language)
        try:
            await session.active.request('initialize', session.validate('initialize', {}))
            await wait_event(session,'initialized')
            await session.active.request('setBreakpoints', session.validate('setBreakpoints', {'source':{'path':source},'breakpoints':[{'line':2}]}))
            launch = asyncio.create_task(session.active.request('launch', session.validate('launch',{'program':'main.js'})))
            await session.active.request('configurationDone',{})
            await launch
            stopped = await wait_event(session, 'stopped')
            thread = stopped['body']['threadId']
            stack = await session.active.request('stackTrace', {'threadId':thread})
            frame = stack['stackFrames'][0]
            assert frame['line'] == 2
            assert Path(frame['source']['path']).name==source
            scopes = await session.active.request('scopes', {'frameId':frame['id']})
            variables = []
            for scope in scopes['scopes']:
                variables += (await session.active.request('variables', {'variablesReference':scope['variablesReference']})).get('variables',[])
            assert any(v['name']=='number' and v['value']=='41' for v in variables)
            cursor = session.cursor
            await session.active.request('next',{'threadId':thread})
            stepped = await wait_event(session,'stopped',cursor)
            stack = await session.active.request('stackTrace', {'threadId':stepped['body']['threadId']})
            assert stack['stackFrames'][0]['line'] == 3
            await session.active.request('continue',{'threadId':thread})
            await wait_event(session,'terminated')
        finally:
            await service.close()
    asyncio.run(run())
