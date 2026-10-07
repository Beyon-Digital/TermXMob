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


def test_debug_api_binds_owned_conversation_checkout_and_rejects_removed_worktree(tmp_path):
    import subprocess
    from test_authorization import client_fixture
    pytest.importorskip('debugpy')
    identity, state, client, headers = client_fixture(tmp_path)
    root = tmp_path / 'project'
    root.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (root / 'main.py').write_text('print("main")\n')
    git('add', '.')
    git('commit', '-m', 'Fixture')
    project = state.projects.register(str(root))
    with client:
        tree = state.delivery.effect(project['id'], str(root), 'worktree-create', {'branch': 'codex/debug', 'base': 'main'})
        principal = identity.resolve(headers['Authorization'][7:]).principal
        conversation = state.workspace.create_session(principal, project_id=project['id'], worktree_id=tree['id'])
        response = client.post('/api/development/projects/' + project['id'] + '/debug', headers=headers,
                               json={'language': 'python', 'workspace_session': conversation['id']})
        assert response.status_code == 200, response.text
        created = response.json()
        assert created['cwd'] == tree['path'] and created['worktree_id'] == tree['id']
        recovered = client.get('/api/development/projects/' + project['id'] + '/debug', headers=headers,
                               params={'workspace_session': conversation['id']})
        assert [item['id'] for item in recovered.json()] == [created['id']]
        session = state.debug.sessions[created['id']]
        assert session.validate('launch', {'program': 'main.py'})['cwd'] == tree['path']
        # Initialization reaches the installed adapter, rather than a fake session.
        endpoint = '/api/development/debug/' + created['id']
        initialized = client.post(endpoint + '/command', headers=headers, json={'command': 'initialize'})
        assert initialized.status_code == 200, initialized.text
        assert initialized.json()['supportsConfigurationDoneRequest']
        other = state.projects.register(str(tmp_path))
        denied = client.post('/api/development/projects/' + other['id'] + '/debug', headers=headers,
                             json={'language': 'python', 'workspace_session': conversation['id']})
        assert denied.status_code == 403
        git('worktree', 'remove', tree['path'])
        assert client.get(endpoint + '/events', headers=headers).status_code == 409
        assert client.delete(endpoint, headers=headers).status_code == 200


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


def test_real_python_loopback_attach_breakpoint_and_inspection(tmp_path):
    import socket
    import sys
    pytest.importorskip('debugpy')
    (tmp_path/'attach.py').write_text('number = 41\nnumber += 1\nprint(number)\n')
    async def run():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        bootstrap=f"import debugpy,runpy;debugpy.listen(('127.0.0.1',{port}));print('ready',flush=True);debugpy.wait_for_client();runpy.run_path('attach.py',run_name='__main__')"
        target=await asyncio.create_subprocess_exec(sys.executable,'-c',bootstrap,cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        service=DebugService()
        connecting=None
        try:
            assert await asyncio.wait_for(target.stdout.readline(),20)==b'ready\n'
            session=await service.create('project',tmp_path,'python')
            await session.active.request('initialize',session.validate('initialize',{}))
            connecting=asyncio.create_task(session.request('attach',session.validate('attach',{'connect':{'host':'127.0.0.1','port':port}})))
            await wait_event(session,'initialized')
            await session.active.request('setBreakpoints',session.validate('setBreakpoints',{'source':{'path':'attach.py'},'breakpoints':[{'line':2}]}))
            await session.active.request('configurationDone',{})
            await connecting
            stopped=await wait_event(session,'stopped')
            stack=await session.active.request('stackTrace',{'threadId':stopped['body']['threadId']})
            assert stack['stackFrames'][0]['line']==2
            scopes=await session.active.request('scopes',{'frameId':stack['stackFrames'][0]['id']})
            values=await session.active.request('variables',{'variablesReference':scopes['scopes'][0]['variablesReference']})
            assert any(item['name']=='number' and item['value']=='41' for item in values['variables'])
            # Ending an attach session detaches; it must not terminate the user's process.
            await service.close()
            output,_=await asyncio.wait_for(target.communicate(),10)
            assert target.returncode==0 and b'42' in output
        finally:
            await service.close()
            if connecting:
                connecting.cancel()
                await asyncio.gather(connecting,return_exceptions=True)
            if target.returncode is None:
                target.kill()
            await target.communicate()
    asyncio.run(run())


@pytest.mark.parametrize('language',['javascript','typescript'])
def test_real_javascript_breakpoint_stack_and_variables(tmp_path, monkeypatch, language):
    import os
    adapter = os.environ.get('TERMX_JS_DEBUG_SERVER')
    if not adapter or not Path(adapter).is_file():
        if os.environ.get('TERMX_RUNTIME_QUALIFICATION') == '1':
            pytest.fail('Required pinned JavaScript debugger is missing')
        pytest.skip('Set TERMX_JS_DEBUG_SERVER to installed pinned Microsoft adapter')
    source='main.ts' if language=='typescript' else 'main.js'
    (tmp_path / source).write_text('let number'+(': number' if language=='typescript' else '')+' = 41;\nnumber += 1;\nconsole.log(number);\n')
    if language=='typescript':
        import subprocess
        from termx.desktop.runtime import assets_root,node_binary
        assets=assets_root()
        compiler=assets/'language/node_modules/typescript/bin/tsc' if assets else Path('/tmp/termx-language-runtime-public/node_modules/typescript/bin/tsc')
        if not compiler.is_file():
            if os.environ.get('TERMX_RUNTIME_QUALIFICATION') == '1':
                pytest.fail('Required packaged TypeScript compiler is missing')
            pytest.skip('Install the pinned packaged TypeScript compiler')
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
