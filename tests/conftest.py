from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def test_client_uses_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host tests model local clients; remote tests opt into a real peer IP."""
    from starlette.testclient import TestClient
    original = TestClient.__init__

    def initialize(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        original(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", initialize)


@pytest.fixture(autouse=True)
def termx_config_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "termx-config"))
    # Keep file-backed agent/discovery writes hermetic: never touch the real
    # ~/.agents tree from tests unless a test opts in explicitly.
    monkeypatch.setenv("TERMX_AGENTS_DIR", str(tmp_path / "agents-root"))
    monkeypatch.setenv("TERMX_ACP_HOME", str(tmp_path / "acp-registry"))
    # Catalogue tests opt in; unrelated tests never start real vendor agents.
    monkeypatch.setenv("TERMX_ENGINE_STARTUP_REFRESH", "0")
    monkeypatch.setenv("TERMX_PROVIDER_STARTUP_REFRESH", "0")


@pytest.fixture(autouse=True)
def isolated_virtual_registry(monkeypatch: pytest.MonkeyPatch):
    """Keep the virtual-display registry hermetic.

    Real displays on the machine running the tests would otherwise be adopted
    into the process-wide registry, breaking tests that count entries and
    making listings spawn helper processes. Tests that exercise adoption
    re-enable it explicitly.
    """
    from termx.desktop import virtual

    monkeypatch.setattr(virtual, "_adopt_helper_displays", lambda *args, **kwargs: None)
    virtual._active.clear()
    virtual._impls.clear()
    yield
    virtual._active.clear()
    virtual._impls.clear()


@pytest.fixture
def desktop_platform(monkeypatch: pytest.MonkeyPatch):
    """Judge desktop actions as the named OS does (macos, linux, windows) without changing the real platform."""
    from termx.desktop import agent_desktop

    def choose(name: str = "macos") -> None:
        monkeypatch.setattr(agent_desktop, "host_os", lambda: name)

    choose("macos")
    return choose


@pytest.fixture
def fake_agent_desktop(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Factory for a ready fake agent-desktop CLI installed as the process-wide client.

    The shim is a Python script run by the interpreter that runs pytest, so the suite behaves the same on
    macOS, Linux and Windows hosts. Every denial test can assert on ``desk.calls()``; at teardown every call the
    shim saw must be a command and flag the client is allowed to build.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from agent_desktop_fixtures import FakeDesktop
    from termx.desktop import agent_desktop

    made: list[FakeDesktop] = []

    def make(platform: str = "macos") -> FakeDesktop:
        monkeypatch.setattr(agent_desktop, "host_os", lambda: platform)
        desk = FakeDesktop(tmp_path / f"fake-desktop-{len(made)}", platform=platform).install()
        with ThreadPoolExecutor(1) as pool:  # a private loop, so the fixture also works under a running one
            pool.submit(asyncio.run, desk.client.ensure_fresh(0)).result()
        made.append(desk)
        return desk

    yield make
    agent_desktop.set_client(None)
    for desk in made:
        for call in desk.calls():
            assert call["argv"][0] in agent_desktop.ALLOWED_COMMANDS
            head = call["argv"][: call["argv"].index("--")] if "--" in call["argv"] else call["argv"]
            assert not {item.split("=", 1)[0] for item in head} & agent_desktop.FORBIDDEN_FLAGS


@pytest.fixture
def ready_desktop(fake_agent_desktop):
    """A host where desktop control is set up, whatever the test runs on (macOS-shaped, with a complete pin)."""
    return fake_agent_desktop()
