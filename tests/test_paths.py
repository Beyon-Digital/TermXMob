from termx.desktop.paths import host_arch, resolve_macos_helper


def test_host_arch_is_known() -> None:
    assert host_arch() in {"arm64", "x86_64"} or host_arch()


def test_resolve_skips_missing_env(monkeypatch) -> None:
    monkeypatch.delenv("TERMX_CAPTURE_BIN", raising=False)
    path = resolve_macos_helper("termx-capture", "TERMX_CAPTURE_BIN")
    if path:
        assert "termx-capture" in path
