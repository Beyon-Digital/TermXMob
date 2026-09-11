import asyncio
from types import SimpleNamespace

from termx.lifecycle import shutdown_state


def test_shutdown_state_order() -> None:
    order: list[str] = []

    class Tunnels:
        async def stop_all(self) -> None:
            order.append("tunnels")

    class Desktop:
        async def close(self) -> None:
            order.append("desktop")

    class Sessions:
        def kill_all(self, timeout: float = 3.0) -> None:
            order.append("sessions")
            assert timeout == 1.25

    state = SimpleNamespace(tunnels=Tunnels(), desktop=Desktop(), sessions=Sessions())
    asyncio.run(shutdown_state(state, timeout=1.25))
    assert order == ["tunnels", "desktop", "sessions"]


def test_shutdown_state_tolerates_missing_helpers() -> None:
    asyncio.run(shutdown_state(SimpleNamespace(), timeout=0.2))
