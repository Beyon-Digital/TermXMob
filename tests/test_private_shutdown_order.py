import asyncio
import pytest


def test_lifespan_stops_internal_and_native_workers_before_releasing_private_capture(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.desktop.recording import set_capture_private,clear_capture_private,assert_agent_capture_allowed
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    monkeypatch.setenv('TERMX_ENGINE_STARTUP_REFRESH','0')
    state=AppState();app=create_app(state)
    async def run():
        order=[];observations=[];identifier='shutdown-private-'+tmp_path.name
        set_capture_private(identifier,expires_at=float('inf'),valid=lambda:True)
        cancel=asyncio.Event()
        async def active_computer_worker():
            while not cancel.is_set():
                try:assert_agent_capture_allowed()
                except PermissionError:pass
                else:observations.append('private-observation-escaped')
                await asyncio.sleep(.001)
            with pytest.raises(PermissionError):assert_agent_capture_allowed()
            order.append('internal-worker-stopped')
        original_agent_close=state.agent.close
        async def agent_close():
            if 'barrier-released' not in order:
                with pytest.raises(PermissionError):assert_agent_capture_allowed()
            await original_agent_close()
            order.append('agent-close')
        state.agent.close=agent_close
        original_native=state.engines.shutdown
        async def native_shutdown(*,strict=False):
            assert 'internal-worker-stopped' in order
            with pytest.raises(PermissionError):assert_agent_capture_allowed()
            assert strict
            await original_native(strict=strict);order.append('native-workers-stopped')
        state.engines.shutdown=native_shutdown
        original_window=state.window_recording.close
        def window_close():
            assert 'native-workers-stopped' in order
            original_window();clear_capture_private(identifier);order.append('barrier-released')
        state.window_recording.close=window_close
        try:
            async with app.router.lifespan_context(app):
                state.agent._cancel['fixture-private']=cancel
                state.agent._workers['fixture-private']=asyncio.create_task(active_computer_worker())
                await asyncio.sleep(.015)
            assert not observations
            assert order.index('internal-worker-stopped')<order.index('native-workers-stopped')<order.index('barrier-released')
            # shutdown_state repeats AgentManager.close without closing its
            # store prematurely; the real manager tolerates both calls.
            assert order.count('agent-close')==2
        finally:clear_capture_private(identifier)
    asyncio.run(run())


@pytest.mark.parametrize('failure',['exception','timeout','active-worker'])
def test_failed_internal_shutdown_never_releases_private_barriers(tmp_path,monkeypatch,failure):
    from termx.app import AppState,create_app
    from termx.desktop.recording import set_capture_private,clear_capture_private,assert_agent_capture_allowed
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    monkeypatch.setenv('TERMX_ENGINE_STARTUP_REFRESH','0')
    state=AppState();app=create_app(state)
    async def run():
        identifier='failed-shutdown-'+tmp_path.name
        set_capture_private(identifier,expires_at=float('inf'),valid=lambda:True)
        original_close=state.agent.close
        released=[]
        state.window_recording.close=lambda:released.append('window')
        async def browser_close():released.append('browser')
        state.browser.close=browser_close
        async def broken_close():
            if failure=='exception':raise RuntimeError('Fixture close failed')
            if failure=='timeout':await asyncio.sleep(10)
        state.agent.close=broken_close
        real_wait_for=asyncio.wait_for
        async def short_wait(coro,timeout):return await real_wait_for(coro,.015 if timeout==3 else timeout)
        monkeypatch.setattr(asyncio,'wait_for',short_wait)
        task=None
        try:
            with pytest.raises((RuntimeError,TimeoutError)):
                async with app.router.lifespan_context(app):
                    if failure=='active-worker':
                        task=asyncio.create_task(asyncio.sleep(10));state.agent._workers['not-stopped']=task
            assert not released
            with pytest.raises(PermissionError):assert_agent_capture_allowed()
            # Storage is still live for surviving callbacks and owner recovery.
            assert state.agent_store.list_tasks()==[]
        finally:
            if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
            await original_close();state.agent_store.close();state.projects.close();clear_capture_private(identifier)
    asyncio.run(run())


def test_native_shutdown_failure_is_strict_and_reader_is_drained_before_transport_retry():
    from types import SimpleNamespace
    from termx.engines.claude import ClaudeEngine
    from termx.engines.gateway import EngineGateway
    from termx.engines.types import EngineSessionBinding
    async def run():
        reader=asyncio.create_task(asyncio.sleep(30));binding=EngineSessionBinding.new('claude','fixture')
        class Client:
            fail=True
            async def disconnect(self):
                if self.fail:raise RuntimeError('Fixture transport refusal')
        client=Client();engine=ClaudeEngine();engine._sessions[binding.binding_id]={'client':client,'reader':reader,'binding':binding};engine._bindings[binding.binding_id]=binding
        with pytest.raises(RuntimeError):await engine.shutdown()
        assert reader.done() and binding.binding_id in engine._sessions
        stopped=[]
        async def stop():stopped.append('other-adapter')
        async def noop():pass
        gateway=EngineGateway.__new__(EngineGateway);gateway.catalogue=SimpleNamespace(stop=noop);gateway.acp_registry=None;gateway._adapters={'claude':engine,'other':SimpleNamespace(shutdown=stop)}
        with pytest.raises(RuntimeError,match='private observation barriers remain active'):await gateway.shutdown(strict=True)
        assert stopped==['other-adapter'] and binding.binding_id in engine._sessions
        client.fail=False;await gateway.shutdown(strict=True)
        assert not engine._sessions and reader.done()
    asyncio.run(run())


def test_native_adapter_shutdown_rejects_live_process_even_after_cleanup_returns():
    from types import SimpleNamespace
    from termx.engines.claude import ClaudeEngine
    from termx.engines.acp import AcpEngine
    from termx.engines.codex import CodexEngine
    from termx.engines.types import EngineSessionBinding
    async def run():
        class Process:
            returncode=None
        async def noop(*_):pass
        process=Process();binding=EngineSessionBinding.new('claude','fixture')
        claude=ClaudeEngine();client=SimpleNamespace(disconnect=noop,_transport=SimpleNamespace(_process=process))
        claude._sessions[binding.binding_id]={'client':client,'binding':binding}
        with pytest.raises(RuntimeError,match='process remains active'):await claude.shutdown()
        assert binding.binding_id in claude._sessions
        acp=AcpEngine();binding=EngineSessionBinding.new('acp','fixture')
        acp._sessions[binding.binding_id]={'binding':binding,'proc':process,'client':SimpleNamespace(cleanup=noop),'ctx':SimpleNamespace(__aexit__=noop)}
        with pytest.raises(RuntimeError,match='process remains active'):await acp.shutdown()
        assert binding.binding_id in acp._sessions
        codex=CodexEngine();codex._conn=SimpleNamespace(close=noop,_proc=process);codex._initialized=True
        with pytest.raises(RuntimeError,match='process remains active'):await codex.shutdown()
        assert codex._initialized
        process.returncode=0
        await claude.shutdown();await acp.shutdown();await codex.shutdown()
        assert not claude._sessions and not acp._sessions and not codex._initialized
    asyncio.run(run())
