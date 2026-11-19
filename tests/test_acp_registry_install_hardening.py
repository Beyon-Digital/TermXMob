"""What a registry entry can make the host run at install time."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import termx.engines.acp_registry as registry_module
from termx.agent.store import AgentStore
from termx.engines.acp_registry import NO_CHECKSUM, AcpRegistry
from termx.engines.gateway import EngineGateway


def make_registry(tmp_path: Path) -> tuple[AcpRegistry, AgentStore]:
    store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
    gateway = EngineGateway(store, store.append_event)
    return AcpRegistry(gateway, root=tmp_path / "acp"), store


class Spy:
    """Stands in for subprocess.run: records argv and env, then writes the package a real install would."""

    def __init__(self, on_run=None, returncode=0, stderr=""):
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.on_run, self.returncode, self.stderr = on_run, returncode, stderr

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs.get("env") or {})))
        if self.on_run:
            self.on_run(argv)
        return type("Done", (), {"returncode": self.returncode, "stdout": "", "stderr": self.stderr})()


def _fake_npm_install(tmp_path):
    def run(argv):
        prefix = Path(argv[argv.index("--prefix") + 1])
        package = prefix / "node_modules" / "ok-agent"
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"bin": {"ok-agent": "cli.js"}}))
        (package / "cli.js").write_text("// fixture\n")
    return run


def test_npm_install_runs_no_package_scripts_and_hides_host_secrets(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    spy = Spy(on_run=_fake_npm_install(tmp_path))
    monkeypatch.setattr(registry_module.subprocess, "run", spy)
    monkeypatch.setattr(registry_module, "resolve_executable", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("TERMX_AI_OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("TERMX_PASSCODE", "host-secret")
    monkeypatch.setenv("TERMX_TEST_HARMLESS", "kept")
    entry = {"id": "ok", "version": "1.0.0", "distribution": {"npx": {"package": "ok-agent@1.0.0", "env": {"NODE_OPTIONS": "--require=/evil.js"}}}}
    registry._install_sync("ok", entry)
    argv, env = spy.calls[0]
    assert "--ignore-scripts" in argv and argv[-2:] == ["--", "ok-agent@1.0.0"]
    assert "TERMX_AI_OPENAI_API_KEY" not in env and "TERMX_PASSCODE" not in env
    assert env.get("TERMX_TEST_HARMLESS") == "kept"
    assert "NODE_OPTIONS" not in env  # registry variables are for the running agent, not the installer
    store.close()


@pytest.mark.parametrize("package", [
    "--registry=https://evil.example", "-g", "git+https://evil.example/x.git", "github:evil/x", "https://evil.example/x.tgz",
    "file:../x", "../x", "x y", "", "@scope", "@/x",
])
def test_npm_install_refuses_anything_but_an_ordinary_registry_package(tmp_path, monkeypatch, package):
    registry, store = make_registry(tmp_path)
    spy = Spy()
    monkeypatch.setattr(registry_module.subprocess, "run", spy)
    monkeypatch.setattr(registry_module, "resolve_executable", lambda name: "/usr/bin/" + name)
    entry = {"id": "bad", "version": "1", "distribution": {"npx": {"package": package}}}
    with pytest.raises(ValueError):
        registry._install_sync("bad", entry)
    assert spy.calls == []  # the package manager never ran
    store.close()


@pytest.mark.parametrize("package", ["agent==1.2.3", "agent@1.2.3", "agent[extra]>=1,<2", "my-agent", "agent~=1.4"])
def test_uv_install_accepts_ordinary_specs_without_building_sources(tmp_path, monkeypatch, package):
    registry, store = make_registry(tmp_path)

    def run(argv):
        bin_dir = tmp_path / "acp" / "u" / "1" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "agent").write_text("#!/bin/sh\n")
    spy = Spy(on_run=run)
    monkeypatch.setattr(registry_module.subprocess, "run", spy)
    monkeypatch.setattr(registry_module, "resolve_executable", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("TERMX_AI_X_API_KEY", "nope")
    registry._install_sync("u", {"id": "u", "version": "1", "distribution": {"uvx": {"package": package}}})
    argv, env = spy.calls[0]
    assert "--no-build" in argv and argv[-2:] == ["--", package]
    assert "TERMX_AI_X_API_KEY" not in env and "UV_TOOL_DIR" in env
    store.close()


@pytest.mark.parametrize("package", ["--index-url=https://evil.example", "git+https://evil.example/x", "agent @ https://evil.example/a.whl", "x;rm", "-e ."])
def test_uv_install_refuses_urls_and_options(tmp_path, monkeypatch, package):
    registry, store = make_registry(tmp_path)
    spy = Spy()
    monkeypatch.setattr(registry_module.subprocess, "run", spy)
    monkeypatch.setattr(registry_module, "resolve_executable", lambda name: "/usr/bin/" + name)
    with pytest.raises(ValueError):
        registry._install_sync("u", {"id": "u", "version": "1", "distribution": {"uvx": {"package": package}}})
    assert spy.calls == []
    store.close()


def test_a_failed_install_reports_the_tool_output_not_the_command_line(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    monkeypatch.setattr(registry_module.subprocess, "run", Spy(returncode=1, stderr="npm ERR! 404 Not Found - ok-agent\nnpm ERR! 404 not in this registry"))
    monkeypatch.setattr(registry_module, "resolve_executable", lambda name: "/usr/bin/" + name)
    with pytest.raises(ValueError) as caught:
        registry._install_sync("ok", {"id": "ok", "version": "1", "distribution": {"npx": {"package": "ok-agent@1.0.0"}}})
    message = str(caught.value)
    assert "404 not in this registry" in message and "/usr/bin/npm" not in message
    store.close()


def _zip_with(command: str, body: bytes = b"#!/bin/sh\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(command, body)
    return buffer.getvalue()


class _Download:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.requested = False

    def __call__(self, request, timeout=None):
        self.requested = True
        outer = self

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return Response(outer.payload)


def test_a_binary_without_a_published_checksum_is_refused_before_downloading(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    download = _Download(_zip_with("agent"))
    monkeypatch.setattr(registry_module.urllib.request, "urlopen", download)
    spec = {"archive": "https://example.com/agent.zip", "cmd": "agent"}
    with pytest.raises(ValueError, match="no SHA-256 checksum"):
        registry._install_binary(tmp_path / "t", spec)
    assert download.requested is False
    assert str(NO_CHECKSUM).startswith("The registry publishes no SHA-256 checksum")
    store.close()


def test_a_binary_with_a_matching_checksum_installs_and_is_marked_verified(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    payload = _zip_with("agent")
    monkeypatch.setattr(registry_module.urllib.request, "urlopen", _Download(payload))
    target = tmp_path / "t"
    target.mkdir()
    command, verified = registry._install_binary(target, {"archive": "https://example.com/agent.zip", "cmd": "agent", "sha256": hashlib.sha256(payload).hexdigest().upper()})
    assert verified is True and command.name == "agent"
    store.close()


@pytest.mark.parametrize("digest", ["abc", "z" * 64, "0" * 63])
def test_a_malformed_checksum_is_refused_even_when_unverified_downloads_are_allowed(tmp_path, monkeypatch, digest):
    registry, store = make_registry(tmp_path)
    download = _Download(_zip_with("agent"))
    monkeypatch.setattr(registry_module.urllib.request, "urlopen", download)
    with pytest.raises(ValueError, match="malformed"):
        registry._install_binary(tmp_path / "t", {"archive": "https://example.com/a.zip", "cmd": "agent", "sha256": digest}, allow_unverified=True)
    assert download.requested is False
    store.close()


def test_a_wrong_checksum_is_refused_and_an_explicit_allow_installs_unverified(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    payload = _zip_with("agent")
    monkeypatch.setattr(registry_module.urllib.request, "urlopen", _Download(payload))
    target = tmp_path / "t"
    target.mkdir()
    with pytest.raises(ValueError, match="verification failed"):
        registry._install_binary(target, {"archive": "https://example.com/a.zip", "cmd": "agent", "sha256": "0" * 64})
    command, verified = registry._install_binary(target, {"archive": "https://example.com/a.zip", "cmd": "agent"}, allow_unverified=True)
    assert verified is False and command.is_file()
    store.close()


def test_the_install_record_says_whether_the_download_was_verified(tmp_path, monkeypatch):
    registry, store = make_registry(tmp_path)
    payload = _zip_with("agent")
    monkeypatch.setattr(registry_module.urllib.request, "urlopen", _Download(payload))
    entry = {"id": "bin", "version": "2", "distribution": {"binary": {registry_module._platform_key(): {"archive": "https://example.com/a.zip", "cmd": "agent"}}}}
    registry._install_sync("bin", entry, allow_unverified=True)
    record = json.loads((tmp_path / "acp" / "bin" / "install.json").read_text())
    assert record["checksum_verified"] is False
    store.close()
