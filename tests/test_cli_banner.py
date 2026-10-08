import io

import pytest

from termx.cli import print_banner


@pytest.mark.parametrize('encoding', ['cp1252', 'utf-8'])
def test_connection_banner_keeps_host_ready_for_redirected_output(monkeypatch, encoding):
    data = io.BytesIO()
    stream = io.TextIOWrapper(data, encoding=encoding)
    monkeypatch.setattr('sys.stdout', stream)
    print_banner(['http://127.0.0.1:8787'], None, 'owned-banner-fixture')
    output = data.getvalue().decode(encoding)
    assert 'http://127.0.0.1:8787?k=owned-banner-fixture' in output
    assert 'passcode owned-banner-fixture' in output
    if encoding == 'cp1252':
        assert 'Open the connection link below.' in output
        assert '\\u258' not in output
    else:
        assert any(char in output for char in '█▀▄')
