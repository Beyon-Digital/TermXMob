"""The OS credential adapter never exposes a key through argv or an error message."""
from __future__ import annotations

import subprocess

import pytest

from termx.agent import secrets as secrets_module
from termx.agent.secrets import CredentialStore

SECRET = 'dummy-key-"not-real"\\x'


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(secrets_module, "sys_platform", lambda: "darwin")
    monkeypatch.setattr(secrets_module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(secrets_module.os, "name", "posix")


def test_keychain_set_passes_the_key_on_stdin_not_argv(mac, monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"], seen["input"] = argv, kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(secrets_module.subprocess, "run", run)
    CredentialStore().set("acct", SECRET)
    assert SECRET not in " ".join(seen["argv"]) and "dummy-key" not in " ".join(seen["argv"])
    assert seen["argv"] == ["security", "-i"]
    assert "add-generic-password" in seen["input"] and "acct" in seen["input"]
    assert seen["input"].count("\n") == 1 and seen["input"].endswith("\n")


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(["security", "add-generic-password", "-w", SECRET], 10),
    OSError("could not run " + SECRET),
])
def test_keychain_failures_never_carry_the_key(mac, monkeypatch, failure):
    def run(argv, **kwargs):
        raise failure

    monkeypatch.setattr(secrets_module.subprocess, "run", run)
    with pytest.raises(RuntimeError) as info:
        CredentialStore().set("acct", SECRET)
    assert "dummy-key" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert "Keychain" in str(info.value)


def test_keychain_error_output_is_scrubbed(mac, monkeypatch):
    monkeypatch.setattr(
        secrets_module.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", f"bad input {SECRET} here"),
    )
    with pytest.raises(RuntimeError) as info:
        CredentialStore().set("acct", SECRET)
    assert "dummy-key" not in str(info.value)


def test_keychain_refuses_a_key_with_a_line_break(mac, monkeypatch):
    monkeypatch.setattr(secrets_module.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(ValueError, match="line break"):
        CredentialStore().set("acct", "first\nsecond")


def test_keychain_read_failure_is_a_plain_message(mac, monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(secrets_module.subprocess, "run", run)
    monkeypatch.delenv("TERMX_AI_ACCT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Keychain did not respond"):
        CredentialStore().get("acct")
