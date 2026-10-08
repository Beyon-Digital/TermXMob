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
