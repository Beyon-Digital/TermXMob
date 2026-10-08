from pathlib import Path
import importlib.util
import sys
import pytest
from termx.desktop import runtime
from termx.lsp import _command


def test_frozen_runtime_resolves_bundled_node_language_and_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.delenv('TERMX_JS_DEBUG_SERVER',raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    node=tmp_path/'nodejs_wheel'/('node.exe' if sys.platform=='win32' else 'bin/node')
    node.parent.mkdir(parents=True);node.write_text('node')
    source=tmp_path/'runtime/language/node_modules/typescript-language-server/lib/cli.mjs'
    source.parent.mkdir(parents=True);source.write_text('server')
    (tmp_path/'runtime/browsers').mkdir()
    debug=tmp_path/'runtime/js-debug/src/dapDebugServer.js'
    debug.parent.mkdir(parents=True);debug.write_text('debug')
    assert runtime.node_binary()==str(node)
    assert _command('typescript')==(str(node),str(source),'--stdio')
    assert runtime.debug_server()==str(debug)
    runtime.configure_bundle()
    assert runtime.os.environ['PLAYWRIGHT_BROWSERS_PATH']==str(tmp_path/'runtime/browsers')
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH')
    node.unlink()
    monkeypatch.setattr(runtime.shutil, 'which', lambda _: '/unqualified/host/node')
    assert runtime.node_binary() is None


def test_frozen_debug_adapter_never_uses_sidecar_as_target_interpreter(monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable','/bundle/termx-backend')
    monkeypatch.setattr(runtime.shutil,'which',lambda name:'/bundle/termx-backend')
    assert runtime.python_interpreter() is None
    assert runtime.python_adapter() is None
    monkeypatch.setattr(runtime.shutil,'which',lambda name:'/usr/bin/python3')
    monkeypatch.setattr(runtime.importlib.util,'find_spec',lambda name:object())
    assert runtime.python_adapter()==['/bundle/termx-backend','--runtime-module','debugpy.adapter']
    assert runtime.python_interpreter()=='/usr/bin/python3'


def test_runtime_preparation_rejects_unverified_debugger(tmp_path):
    spec=importlib.util.spec_from_file_location('prepare_runtime',Path(__file__).parents[1]/'desktop/scripts/prepare_runtime.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    bad=tmp_path/'debug.tar.gz';bad.write_bytes(b'wrong')
    with pytest.raises(ValueError,match='checksum'):module.unpack_debug(bad,tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_desktop_web_packaging_requires_workspace_contract(tmp_path):
    spec=importlib.util.spec_from_file_location('build_sidecar',Path(__file__).parents[1]/'desktop/scripts/build_sidecar.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'index.html').write_text('<html>Expo old bundle</html>')
    with pytest.raises(SystemExit,match='contract'):module.verify_web(tmp_path)
    (tmp_path/'index.html').write_text('<meta name="termx-ui-contract" content="3" />')
    module.verify_web(tmp_path)
