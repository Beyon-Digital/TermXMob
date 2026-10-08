"""CI fixture isolation checks; actual native UI feasibility is a CI artifact."""
import importlib.util
from pathlib import Path

import pytest


def probe():
    source = Path(__file__).resolve().parents[1]/'desktop/scripts/macos_gui_feasibility.py'
    spec = importlib.util.spec_from_file_location('macos_gui_fixture', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_ui_probe_targets_exact_owned_pid_only():
    module = probe()
    assert 'whose unix id is 12345' in module.ui_script(12345)
    for invalid in [0, -1, True, '12345', '1\nget every window']:
        with pytest.raises(ValueError):
            module.ui_script(invalid)


def test_native_ui_probe_ready_file_is_bounded(tmp_path):
    module = probe()
    ready = tmp_path/'ready.json'
    assert module.read_state(ready) is None
    ready.write_text('{"pid":12345,"clicked":false}')
    assert module.read_state(ready)['pid'] == 12345
    ready.write_bytes(b' ' * 2049)
    assert module.read_state(ready) is None
