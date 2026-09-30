"""Sandbox models, environment builder, and host-runner contract tests.

These prove the PR A invariants that do not need kernel isolation:
- restricted SpawnSpecs are rejected before any backend sees them;
- the clean environment never crosses credential material;
- the host backend preserves legacy spawn/stream/tree-kill behavior and
  reports its strength truthfully ("none" — never claimed otherwise).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from termx.sandbox import runner_for
from termx.sandbox.environment import (
    build_environment,
    host_environment,
    sandbox_home,
    sandbox_tmp,
)
from termx.sandbox.models import (
    HARD_DENIED_CAPABILITIES,
    EXECUTION_PROFILES,
    ResourceLimits,
    SandboxCapabilities,
    SandboxFailure,
    SpawnSpec,
    safe_env_name,
)


def _spec(**overrides) -> SpawnSpec:
    base = dict(
        profile="agent",
        argv=("echo", "hi"),
        cwd="/project",
        workspace_root="/project",
        writable_roots=("/project",),
    )
    base.update(overrides)
    return SpawnSpec(**base)


# ---------------------------------------------------------------------------
# SpawnSpec validation — restricted profiles are rejected before spawning.
# ---------------------------------------------------------------------------


def test_spawn_spec_rejects_unknown_profile():
    with pytest.raises(SandboxFailure) as exc:
        _spec(profile="sudo").validate()
    assert exc.value.reason == "invalid_profile"


def test_spawn_spec_requires_exactly_one_spawn_form():
    with pytest.raises(SandboxFailure) as exc:
        SpawnSpec(profile="host", cwd="/x").validate()
    assert exc.value.reason == "invalid_spawn"
    with pytest.raises(SandboxFailure):
        _spec(argv=("echo",), shell="echo hi").validate()


def test_spawn_spec_rejects_empty_shell_and_argv_entries():
    with pytest.raises(SandboxFailure):
        _spec(argv=None, shell="   ").validate()
    with pytest.raises(SandboxFailure):
        _spec(argv=("echo", "")).validate()


def test_restricted_profile_requires_workspace_root():
    with pytest.raises(SandboxFailure) as exc:
        _spec(profile="agent", workspace_root="", writable_roots=()).validate()
    assert exc.value.reason == "workspace_required"
    # host never needs a workspace root — backward compatible.
    SpawnSpec(profile="host", argv=("echo", "hi"), cwd="/tmp").validate()


@pytest.mark.parametrize("profile", ["agent", "workspace"])
def test_restricted_cwd_must_stay_inside_writable_roots(profile):
    _spec(profile=profile, cwd="/project/sub/dir").validate()
    with pytest.raises(SandboxFailure) as exc:
        _spec(profile=profile, cwd="/etc").validate()
    assert exc.value.reason == "outside_roots"
    # Sibling directory that merely shares the prefix is not inside.
    with pytest.raises(SandboxFailure):
        _spec(profile=profile, cwd="/project-evil").validate()


def test_cwd_resolution_rejects_dotdot_escape():
    with pytest.raises(SandboxFailure) as exc:
        _spec(cwd="/project/../outside").validate()
    assert exc.value.reason == "outside_roots"


def test_spawn_spec_network_and_limits_typed():
    spec = _spec(network="none", limits=ResourceLimits(cpu_s=5, memory_bytes=1024, pids=8, wall_s=30))
    spec.validate()
    with pytest.raises(SandboxFailure):
        _spec(network="everything").validate()


# ---------------------------------------------------------------------------
# SandboxCapabilities — truthful reporting, wildcard coverage.
# ---------------------------------------------------------------------------


def test_capabilities_cover_wildcards():
    caps = SandboxCapabilities(
        backend="test",
        profile="agent",
        strength="kernel",
        granted=frozenset({"fs.workspace:any", "net.outbound:any", "process.execute:any"}),
    )
    assert caps.covers("net.outbound:registry.npmjs.org")
    assert caps.covers("fs.workspace.write")
    assert caps.covers("process.execute.spawn")
    assert not caps.covers("net.listen:any")
    assert not caps.covers("privilege.elevate")


def test_host_backend_reports_no_isolation():
    runner = runner_for("agent")
    caps = runner.capabilities()
    assert caps.backend == "host"
    assert caps.strength == "none"
    assert caps.granted == frozenset({"*:any"})
    assert caps.network_control is False
    assert caps.filesystem_isolation is False
    assert caps.identity_isolation is False
    # Tree kill is the one control every backend must implement.
    assert caps.process_tree_kill is True


def test_runner_for_profiles():
    for profile in EXECUTION_PROFILES:
        assert runner_for(profile).profile == profile
    with pytest.raises(SandboxFailure):
        runner_for("root")


# ---------------------------------------------------------------------------
# Environment builder — no arbitrary secrets cross into restricted profiles.
# ---------------------------------------------------------------------------


def test_build_environment_strips_credential_material():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "AWS_SECRET_ACCESS_KEY": "ak-secret",
        "AWS_SESSION_TOKEN": "tok",
        "AZURE_CLIENT_SECRET": "x",
        "GCP_PROJECT": "p",
        "GOOGLE_APPLICATION_CREDENTIALS": "/key.json",
        "TERMX_AI_OPENAI_API_KEY": "sk-fake",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "KUBECONFIG": "/home/u/.kube/config",
        "DOCKER_HOST": "tcp://d:2375",
        "NPM_CONFIG__AUTH_TOKEN": "npm-tok",
        "GITHUB_TOKEN": "ghp_x",
        "A_COMPLETELY_MADE_UP_SECRET": "value",
    }
    env = build_environment("agent", base=base)
    for leaked in (
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "TERMX_AI_OPENAI_API_KEY",
        "SSH_AUTH_SOCK",
        "KUBECONFIG",
        "DOCKER_HOST",
        "NPM_CONFIG__AUTH_TOKEN",
        "GITHUB_TOKEN",
        "A_COMPLETELY_MADE_UP_SECRET",
    ):
        assert leaked not in env, leaked
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/u"


def test_build_environment_keeps_safe_toolchain_vars_and_proxies():
    base = {
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://corp:3128",
        "NPM_CONFIG_PREFIX": "/opt/npm",
        "GOPATH": "/go",
        "LC_ALL": "en_US.UTF-8",
        "TERMX_AGENT_FOO": "agent-scoped",
        "TERMX_AI_X": "provider-key",
    }
    env = build_environment("workspace", base=base)
    for kept in ("PATH", "HTTPS_PROXY", "NPM_CONFIG_PREFIX", "GOPATH", "LC_ALL", "TERMX_AGENT_FOO"):
        assert env.get(kept) == base[kept], kept
    assert "TERMX_AI_X" not in env


def test_build_environment_strips_url_credentials():
    """Review regression: allowed proxy/index vars may carry
    ``user:pass@host`` — the credentials must never cross into the sandbox."""
    env = build_environment(
        "agent",
        base={
            "PATH": "/usr/bin",
            "HTTPS_PROXY": "http://alice:s3cret@proxy.corp:3128",
            "PIP_INDEX_URL": "https://u:tok@pypi.internal/simple/",
            "http_proxy": "socks5://u:p@gw:1080",
        },
    )
    assert env["HTTPS_PROXY"] == "http://proxy.corp:3128"
    assert env["PIP_INDEX_URL"] == "https://pypi.internal/simple/"
    assert env["http_proxy"] == "socks5://gw:1080"
    assert "s3cret" not in env["HTTPS_PROXY"] and "tok" not in env["PIP_INDEX_URL"]


def test_build_environment_extra_allow_still_cannot_leak_denied_names():
    env = build_environment(
        "agent",
        base={"PATH": "/x", "PROJECT_TOOLCHAIN_DIR": "/opt/t", "AWS_TOKEN": "no"},
        extra_allow=("PROJECT_TOOLCHAIN_DIR", "AWS_TOKEN"),
    )
    assert env["PROJECT_TOOLCHAIN_DIR"] == "/opt/t"
    assert "AWS_TOKEN" not in env  # deny beats caller config


def test_build_environment_home_and_tmp_overrides():
    env = build_environment(
        "agent", base={"HOME": "/real", "TMPDIR": "/realtmp"}, home="/sandbox/home", tmp_dir="/sandbox/tmp"
    )
    assert env["HOME"] == "/sandbox/home"
    assert env["USERPROFILE"] == "/sandbox/home"
    assert env["TMPDIR"] == env["TEMP"] == env["TMP"] == "/sandbox/tmp"


def test_build_environment_rejects_host_profile():
    with pytest.raises(ValueError):
        build_environment("host", base={})


def test_host_environment_byte_compatible_with_legacy_regex():
    base = {
        "PATH": "/x",
        "MY_TOKEN": "t",
        "api_key_secret": "k",
        "PASSCODE": "p",
        "PRIVATE_KEY_PATH": "f",
        "CREDENTIALS_FILE": "c",
        "SSH_AUTH_SOCK": "/sock",  # legacy regex did NOT strip this — preserved for compat
        "AWS_SECRET": "w",
    }
    env = host_environment(base)
    for stripped in ("MY_TOKEN", "api_key_secret", "PASSCODE", "PRIVATE_KEY_PATH", "CREDENTIALS_FILE", "AWS_SECRET"):
        assert stripped not in env, stripped
    assert env["PATH"] == "/x"
    assert env["SSH_AUTH_SOCK"] == "/sock"


def test_sandbox_dirs_are_private_per_task(tmp_path):
    home = sandbox_home(tmp_path, "task-1")
    tmp = sandbox_tmp(tmp_path, "task-1")
    assert home.is_dir() and tmp.is_dir()
    assert "task-1" in str(home) and "task-1" in str(tmp)


def test_safe_env_name():
    assert safe_env_name("MY_VAR_1")
    assert not safe_env_name("MY-VAR")
    assert not safe_env_name("1VAR")


# ---------------------------------------------------------------------------
# Host backend spawn semantics — the compatibility baseline every strong
# backend must match (streaming, cwd, env plumbing, tree-kill).
# ---------------------------------------------------------------------------


def _read_all(stream, limit: int = 1_000_000) -> bytes:
    async def _go() -> bytes:
        out = b""
        while len(out) < limit:
            chunk = await stream.read(65536)
            if not chunk:
                break
            out += chunk
        return out

    return asyncio.run(_go())


def test_host_runner_spawn_argv_and_shell(tmp_path):
    async def run():
        runner = runner_for("host")
        spec = SpawnSpec(
            profile="host",
            argv=(sys.executable, "-c", "import sys;sys.stdout.write('ok')"),
            cwd=str(tmp_path),
        )
        spawned = await runner.spawn(spec)
        out = await spawned.process.stdout.read()
        assert await spawned.wait() == 0
        assert out == b"ok"
        assert spawned.metadata()["backend"] == "host"

    asyncio.run(run())


def test_host_runner_spawn_shell_command(tmp_path):
    async def run():
        runner = runner_for("host")
        spawned = await runner.spawn(
            SpawnSpec(profile="host", shell="printf hello", cwd=str(tmp_path))
        )
        out = await spawned.process.stdout.read()
        assert await spawned.wait() == 0
        assert out == b"hello"

    asyncio.run(run())


def test_host_runner_respects_spawn_env_and_cwd(tmp_path):
    async def run():
        runner = runner_for("host")
        spawned = await runner.spawn(
            SpawnSpec(
                profile="host",
                argv=(sys.executable, "-c", "import os;print(os.environ.get('TERM_X'), end='')"),
                cwd=str(tmp_path),
                env={"TERM_X": "injected"},
            )
        )
        out = await spawned.process.stdout.read()
        assert await spawned.wait() == 0
        assert out == b"injected"

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX killpg tree-kill semantics")
def test_host_runner_terminate_kills_process_tree(tmp_path):
    """Cancel must kill the whole spawned tree — the child a shell backgrounds
    dies with it, not just the leader."""

    async def run():
        pidfile = tmp_path / "child.pid"
        runner = runner_for("host")
        spawned = await runner.spawn(
            SpawnSpec(
                profile="host",
                shell=f"sleep 30 & echo $! > {pidfile}; sleep 30",
                cwd=str(tmp_path),
            )
        )
        for _ in range(50):
            if pidfile.exists():
                break
            await asyncio.sleep(0.05)
        assert pidfile.exists()
        child_pid = int(pidfile.read_text().strip())

        await spawned.terminate()
        assert spawned.process.returncode is not None
        # The backgrounded child is dead (ESRCH once reaped by init).
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"descendant pid {child_pid} survived tree kill")

    asyncio.run(run())


def test_host_runner_spawn_failure_is_structured(tmp_path):
    async def run():
        runner = runner_for("host")
        with pytest.raises(SandboxFailure) as exc:
            await runner.spawn(
                SpawnSpec(profile="host", argv=("/definitely/not/a/binary",), cwd=str(tmp_path))
            )
        assert exc.value.reason == "spawn_failed"

    asyncio.run(run())


def test_host_runner_rejects_invalid_spec_before_spawn(tmp_path):
    async def run():
        runner = runner_for("agent")
        with pytest.raises(SandboxFailure) as exc:
            await runner.spawn(
                SpawnSpec(
                    profile="agent",
                    argv=("echo", "hi"),
                    cwd="/etc",
                    workspace_root="/project",
                )
            )
        assert exc.value.reason == "outside_roots"

    asyncio.run(run())


def test_agent_profile_env_scrubs_process_env(tmp_path, monkeypatch):
    """An Agent-profile spawn through the host backend must not inherit the
    host process's credential material — the env allowlist runs even when the
    kernel boundary doesn't exist yet."""
    monkeypatch.setenv("TERMX_AI_TESTKEY", "sk-fake-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ak-fake")

    async def run():
        runner = runner_for("agent")
        spawned = await runner.spawn(
            SpawnSpec(
                profile="agent",
                argv=(
                    sys.executable,
                    "-c",
                    "import os;print(sorted(k for k in os.environ if 'KEY' in k or 'AWS' in k))",
                ),
                cwd=str(tmp_path),
                workspace_root=str(tmp_path),
                writable_roots=(str(tmp_path),),
            )
        )
        out = await spawned.process.stdout.read()
        assert await spawned.wait() == 0
        assert b"TERMX_AI_TESTKEY" not in out
        assert b"AWS_SECRET_ACCESS_KEY" not in out

    asyncio.run(run())


def test_streamed_process_terminate_is_idempotent(tmp_path):
    async def run():
        runner = runner_for("host")
        spawned = await runner.spawn(
            SpawnSpec(profile="host", shell="sleep 30", cwd=str(tmp_path))
        )
        await spawned.terminate()
        await spawned.terminate()
        assert spawned.process.returncode is not None

    asyncio.run(run())


def test_time_bounded_terminate_returns(tmp_path):
    """terminate() must never hang the caller — a non-cooperating tree is
    SIGKILLed after the grace window, not waited on forever."""
    async def run():
        runner = runner_for("host")
        spawned = await runner.spawn(
            SpawnSpec(
                profile="host",
                shell="trap '' TERM; sleep 30",
                cwd=str(tmp_path),
            )
        )
        start = time.monotonic()
        await asyncio.wait_for(spawned.terminate(), timeout=8)
        assert time.monotonic() - start < 8
        assert spawned.process.returncode is not None

    if os.name == "nt":
        pytest.skip("POSIX signal semantics")
    asyncio.run(run())
