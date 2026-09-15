from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def termx_config_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "termx-config"))


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
