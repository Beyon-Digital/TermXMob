"""Live socket checks keep durable authorization off the host event loop."""
import asyncio
import threading
import time

from termx.app import AppState
from termx.identity_guard import SessionGuard


def test_each_socket_frame_checks_live_session_off_loop_before_effect(tmp_path):
    state = AppState(passcode=None)
    owner = state.identity.setup_owner('guard-owner', 'guard-fixture-password-123')

    async def run():
        credentials = await state.identity.login('local-password', {
            'username':'guard-owner','password':'guard-fixture-password-123'}, peer='local')
        original = state.identity.resolve
        loop_thread = threading.get_ident()
        checks = []

        def delayed_resolve(token):
            checks.append(threading.get_ident())
            time.sleep(.025)  # Model durable-store latency while host tasks remain runnable.
            return original(token)

        state.identity.resolve = delayed_resolve
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        effects, heartbeats = [], []

        async def app(scope, receive, send):
            await send({'type':'websocket.accept'})
            while True:
                message = await receive()
                if message['type'] == 'websocket.disconnect':
                    return
                effects.append(message['text'])
                await send({'type':'websocket.send','text':'ack'})

        async def heartbeat():
            while True:
                heartbeats.append(time.monotonic())
                await asyncio.sleep(.003)

        scope = {'type':'websocket','path':'/api/fixture','scheme':'ws',
                 'client':('127.0.0.1',1234),'query_string':b'',
                 'headers':[(b'authorization', ('Bearer '+credentials.access_token).encode())]}
        guard = asyncio.create_task(SessionGuard(app, state)(scope, incoming.get, outgoing.put))
        ticker = asyncio.create_task(heartbeat())
        try:
            assert (await asyncio.wait_for(outgoing.get(), 5))['type'] == 'websocket.accept'
            await incoming.put({'type':'websocket.receive','text':'allowed'})
            assert (await asyncio.wait_for(outgoing.get(), 5))['text'] == 'ack'
            await asyncio.to_thread(state.identity.revoke, credentials.session_id,
                                    owner.id)
            await incoming.put({'type':'websocket.receive','text':'revoked-effect'})
            close = await asyncio.wait_for(outgoing.get(), 5)
            assert close == {'type':'websocket.close','code':4401}
            await asyncio.wait_for(guard, 5)
            assert effects == ['allowed']
            # Every live check executes on an IO worker before transport effects.
            assert all(identifier != loop_thread for identifier in checks)
            assert len(checks) >= 4 and len(heartbeats) >= 10
        finally:
            guard.cancel();ticker.cancel()
            await asyncio.gather(guard,ticker,return_exceptions=True)

    asyncio.run(run())
