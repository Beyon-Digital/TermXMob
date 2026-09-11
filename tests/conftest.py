from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def termx_config_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "termx-config"))
