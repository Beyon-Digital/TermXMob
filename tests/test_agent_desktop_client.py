"""The agent-desktop client: discovery, hashes, argv allow-list, runner, envelopes, status."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys

import pytest
from agent_desktop_fixtures import FakeDesktop, SHIM_SOURCE, err, ok, sha, status_data

from termx.desktop import agent_desktop as ad

REF = "@s1abc2def3ghi:e7"


def run(coro):
    return asyncio.run(coro)


def test_pins_file_is_well_formed():
    pins = ad.load_pins()
    assert pins["version"] == ad.PINNED_VERSION and pins["envelope"] == ad.ENVELOPE_VERSION
    expected_keys = {"macos-arm64", "macos-x86_64", "linux-x86_64", "linux-aarch64", "windows-x86_64", "windows-aarch64"}
    assert set(pins["assets"]) == expected_keys
    triples = {"macos-arm64": "aarch64-apple-darwin", "macos-x86_64": "x86_64-apple-darwin", "linux-x86_64": "x86_64-unknown-linux-gnu",
               "linux-aarch64": "aarch64-unknown-linux-gnu", "windows-x86_64": "x86_64-pc-windows-msvc", "windows-aarch64": "aarch64-pc-windows-msvc"}
    for key, asset in pins["assets"].items():
        assert asset["asset"] == f"agent-desktop-v{pins['version']}-{triples[key]}.tar.gz"
        assert len(asset["sha256"]) == 64 and asset["sha256"] == asset["sha256"].lower()
        assert asset["files"] is None or set(asset["files"]) <= {"agent-desktop", "agent-desktop.exe", "agent-desktop-macos-helper"}


@pytest.mark.parametrize("system,machine,key", [
    (ad.HostPlatform("macos", "arm64"), None, "macos-arm64"),
    (ad.HostPlatform("linux", "x86_64"), None, "linux-x86_64"),
    (ad.HostPlatform("windows", "arm64"), None, "windows-aarch64"),
    (ad.HostPlatform("linux", "arm64"), None, "linux-aarch64"),
])
def test_platform_keys(system, machine, key):
    assert system.key == key


# -- argv builders ---------------------------------------------------------------------------------------------


def test_builders_only_produce_allowed_commands_and_use_equals_for_values():
    with pytest.raises(ValueError):
        ad.req_find(app_name="--headed", role="button")  # an application name never starts with a dash
    req = ad.req_find(app_name="Notes", role="button", name="--force", exact=True)
    assert req.command == "find"
    assert "--app=Notes" in req.argv and "--name=--force" in req.argv
    assert not any(item in ad.FORBIDDEN_FLAGS for item in req.argv)


# Pytest includes case IDs in PYTEST_CURRENT_TEST; Windows caps that value at 32,767 characters.
@pytest.mark.parametrize(
    "hostile",
    ["--headed", "--force", "-v", "--trace=/tmp/x", " spaced", "a" * 100_000, "@s1:e1 --headed", 'q"uo\\te'],
    ids=["headed-option", "force-option", "short-option", "trace-option", "leading-space",
         "oversized-100k", "reference-plus-option", "quotes-and-backslash"],
)
def test_hostile_strings_never_become_flags(hostile):
    for build in (
        lambda: ad.req_find(app_name="Notes", name=hostile),
        lambda: ad.req_snapshot(app_name="Notes", wait_for=hostile),
        lambda: ad.req_act("select", REF, option=hostile),
    ):
        try:
            req = build()
        except ValueError:
            continue
        head = req.argv[: req.argv.index("--")] if "--" in req.argv else req.argv
        assert not {item.split("=", 1)[0] for item in head} & ad.FORBIDDEN_FLAGS
        for item in head:
            assert item.startswith("-") or ad.valid_ref(item)  # values only ever follow `--flag=`


def test_text_never_appears_in_argv_and_goes_to_stdin():
    req = ad.req_act("set_value", REF, text="s3cret value")
    assert "s3cret value" not in " ".join(req.argv) and req.stdin == b"s3cret value" and "--stdin" in req.argv
    assert ad.req_act("type", REF, text="hello").stdin == b"hello"


def test_text_limits_are_bytes_and_checked_before_spawn():
    with pytest.raises(ValueError):
        ad.req_act("type", REF, text="漢" * 4000)  # 12,000 bytes
    with pytest.raises(ValueError):
        ad.req_act("type", REF, text="a\x00b")
    with pytest.raises(ValueError):
        ad.req_act("set_value", REF, text="\ud800")
    long = ad.req_act("type", REF, text="x" * 2400)
    assert f"--timeout-ms={5000 + 30 * 2400}" in long.argv and long.wall_s == (5000 + 72000) / 1000 + 5
    assert ad.req_act("set_value", REF, text="x" * 100_000).stdin is not None


def test_ref_regex_accepts_real_lengths_and_rejects_the_rest():
    for ok_ref in ("@s2lhvs7v75115a:e2", "@si2gpmz9xpg8i:e12", "@s8f3k2p9:e8"):
        assert ad.valid_ref(ok_ref)
    for bad in ("@e1", "notaref", "@s1:e1", "@sABCDE:e1", "@s1abcd:e1 --headed", "@s1abcd:e"):
        assert not ad.valid_ref(bad)
        with pytest.raises(ValueError):
            ad.req_act("click", bad)


def test_root_and_scope_rules_from_the_cli():
    with pytest.raises(ValueError):
        ad.req_snapshot()  # neither app nor root
    with pytest.raises(ValueError):
        ad.req_snapshot(app_name="Notes", root=REF)
    with pytest.raises(ValueError):
        ad.req_snapshot(root=REF, surface="menu")
    with pytest.raises(ValueError):
        ad.req_snapshot(root=REF, wait_for="button:Save")
    with pytest.raises(ValueError):
        ad.req_snapshot(app_name="Notes", wait_for="a:b", wait_for_gone="c:d")
    with pytest.raises(ValueError):
        ad.req_find(app_name="Notes")  # nothing to match
    root = ad.req_snapshot(root=REF)
    assert f"--root={REF}" in root.argv and not any(item.startswith(("--app", "--surface", "--window-id")) for item in root.argv)
    assert "--skeleton" not in ad.req_snapshot(app_name="Notes", surface="menu", mode="overview").argv
    assert "--skeleton" in ad.req_snapshot(app_name="Notes", mode="overview").argv


def test_wait_for_uses_its_own_timeout_and_longer_wall_limit():
    req = ad.req_snapshot(app_name="Notes", wait_for="button:Done", timeout_ms=10000)
    assert "--wait-timeout=10000" in req.argv and req.wall_s == 25.0
    assert ad.req_snapshot(app_name="Notes", timeout_ms=10000).wall_s == 15.0


def test_commands_without_a_builder_cannot_be_made():
    for command in ("batch", "mcp", "skills", "session", "launch", "close-app", "clipboard-get", "clipboard-set", "screenshot",
                    "press", "key-down", "mouse-down", "hover", "drag", "double-click", "focus-window", "permissions", "trace"):
        assert command not in ad.ALLOWED_COMMANDS
        with pytest.raises(ValueError):
            ad._assert_safe(command, ())
    with pytest.raises(ValueError):
        ad._assert_safe("click", ("--headed",))
    ad._assert_safe("select", (REF, "--", "--headed"))  # after `--` it is a value


def test_platform_action_matrix():
    assert ad.action_unsupported("type", "windows") and "set_value" in ad.action_unsupported("type", "windows")
    assert ad.action_unsupported("scroll", "linux") and ad.action_unsupported("focus", "windows")
    assert ad.action_unsupported("type", "macos") is None and ad.action_unsupported("click", "windows") is None


# -- discovery and hashes --------------------------------------------------------------------------------------


def test_managed_shim_is_discovered_verified_and_ready(tmp_path):
    desk = FakeDesktop(tmp_path)
    status = run(desk.client.status(refresh=True))
    assert status.ready and status.installed and status.source == "managed" and status.verified
    assert status.version == "1.2.0" and status.helper_ok and status.permission["state"] == "granted"
    assert desk.client.ready_cached()


def test_nothing_installed_reports_not_installed(tmp_path):
    desk = FakeDesktop(tmp_path, install=False)
    status = run(desk.client.status(refresh=True))
    assert not status.ready and status.issue == "not_installed" and desk.calls() == []


def test_wrong_hash_is_never_executed(tmp_path):
    desk = FakeDesktop(tmp_path)
    desk.binary.write_bytes(desk.binary.read_bytes() + b"\n# tampered")
    status = run(desk.client.status(refresh=True))
    assert status.issue == "hash_mismatch" and not status.ready
    assert desk.calls() == []  # hash first, execute second: not even `version` ran


def test_file_replaced_between_calls_is_refused_before_spawn(tmp_path):
    desk = FakeDesktop(tmp_path)
    run(desk.client.status(refresh=True))
    before = len(desk.calls())
    desk.binary.write_bytes(desk.binary.read_bytes() + b"\n# swapped")
    with pytest.raises(ad.AgentDesktopFailure) as caught:
        run(desk.client.run(ad.req_list_apps()))
    assert caught.value.kind == "hash_mismatch" and len(desk.calls()) == before
    assert not desk.client.ready_cached()


def test_version_mismatch_and_envelope_mismatch_fail_closed(tmp_path):
    desk = FakeDesktop(tmp_path)
    desk.version = "1.3.0"
    desk.write()
    assert run(desk.client.status(refresh=True)).issue == "version_mismatch"
    desk = FakeDesktop(tmp_path / "b")
    desk.rules({"match": {"command": "version"}, "respond": {"envelope": {**ok("version", {"os": "macos", "target": "aarch64", "version": "1.2.0"}), "version": "2.7"}}})
    assert run(desk.client.status(refresh=True)).issue in {"envelope_unsupported", "spawn_failed"}


def test_macos_helper_is_required_and_hashed(tmp_path):
    desk = FakeDesktop(tmp_path)
    desk.helper.unlink()
    assert run(desk.client.status(refresh=True)).issue == "helper_missing"
    desk = FakeDesktop(tmp_path / "b")
    run(desk.client.status(refresh=True))
    call = desk.calls()[0]
    assert call["env"]["AGENT_DESKTOP_MACOS_HELPER_PATH"] == str(desk.helper)


def test_incomplete_pin_blocks_managed_use(tmp_path):
    desk = FakeDesktop(tmp_path)
    pins = desk.pins()
    pins["assets"][desk.platform.key]["files"] = None
    client = desk.build_client(pins=pins)
    status = run(client.status(refresh=True))
    assert not status.ready and status.platform_reason == "pin_incomplete" and desk.calls() == []


def test_path_binary_is_hash_checked_then_adopted_by_copy(tmp_path):
    desk = FakeDesktop(tmp_path, install=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    source = outside / desk.platform.binary_name
    shutil.copyfile(SHIM_SOURCE, source)
    source.chmod(0o755)
    (outside / "agent-desktop-macos-helper").write_bytes(b"")
    (outside / "agent-desktop-macos-helper").chmod(0o755)
    pins = {"version": "1.2.0", "assets": {desk.platform.key: {"files": {source.name: sha(source), "agent-desktop-macos-helper": sha(outside / "agent-desktop-macos-helper")}}}}
    client = desk.build_client(pins=pins, search_path=[str(outside)])
    desk.bin_dir.mkdir(parents=True, exist_ok=True)
    (desk.bin_dir / "scenario.json").write_text(json.dumps([{"match": {"command": "version"}, "respond": {"envelope": ok("version", {"os": "macos", "target": "aarch64", "version": "1.2.0"})}},
                                                           {"match": {"command": "status"}, "respond": {"envelope": ok("status", status_data())}}]))
    status = run(client.status(refresh=True))
    assert status.ready and status.source == "path"
    assert status.path == str(desk.binary)  # the private copy runs, never the PATH file
    source.write_bytes(source.read_bytes() + b"\n# changed afterwards")
    assert run(client.run(ad.req_status())).ok  # a later change to the PATH file changes nothing


def test_unpinned_path_binary_is_never_executed(tmp_path):
    desk = FakeDesktop(tmp_path, install=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / desk.platform.binary_name
    shutil.copyfile(SHIM_SOURCE, source)
    source.chmod(0o755)
    client = desk.build_client(search_path=[str(outside)])
    status = run(client.status(refresh=True))
    assert status.issue == "not_pinned_build" and not (outside / "calls.jsonl").exists()


def test_relative_and_cwd_path_entries_are_skipped(tmp_path, monkeypatch):
    desk = FakeDesktop(tmp_path, install=False)
    shutil.copyfile(SHIM_SOURCE, tmp_path / desk.platform.binary_name)
    monkeypatch.chdir(tmp_path)
    client = desk.build_client(search_path=[".", "relative/bin", str(tmp_path)])
    assert run(client.status(refresh=True)).issue == "not_installed"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_configured_path_skips_the_hash_but_not_version_or_modes(tmp_path):
    desk = FakeDesktop(tmp_path, install=False)
    own = tmp_path / "own"
    own.mkdir(mode=0o700)
    binary = own / desk.platform.binary_name
    shutil.copyfile(SHIM_SOURCE, binary)
    binary.chmod(0o700)
    (own / "agent-desktop-macos-helper").write_bytes(b"")
    (own / "scenario.json").write_text(json.dumps([{"match": {"command": "version"}, "respond": {"envelope": ok("version", {"os": "macos", "target": "aarch64", "version": "1.2.0"})}},
                                                   {"match": {"command": "status"}, "respond": {"envelope": ok("status", status_data())}}]))
    client = desk.build_client(setting=str(binary), pins={"version": "1.2.0", "assets": {desk.platform.key: {"files": {"agent-desktop": "0" * 64}}}})
    status = run(client.status(refresh=True))
    assert status.ready and status.source == "configured" and status.verified is False
    binary.chmod(0o770)
    assert run(client.status(refresh=True)).issue == "configured_invalid"
    assert run(desk.build_client(setting="relative/agent-desktop").status(refresh=True)).issue == "configured_invalid"


def test_a_broken_explicit_path_stays_visible_when_the_pinned_copy_is_used_instead(tmp_path):
    """The owner chose a program; if it is unusable and another copy runs, the status says so rather than showing all clear."""
    desk = FakeDesktop(tmp_path)
    missing = str(tmp_path / "gone" / desk.platform.binary_name)
    client = desk.build_client(setting=missing)
    status = run(client.status(refresh=True))
    assert status.installed and status.source == "managed"
    assert [item["code"] for item in status.issues] == ["configured_invalid"]
    assert status.ready is False


# -- runner ----------------------------------------------------------------------------------------------------


def ready(tmp_path, platform="macos") -> FakeDesktop:
    desk = FakeDesktop(tmp_path, platform=platform)
    run(desk.client.status(refresh=True))
    return desk


def test_runner_environment_cwd_and_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMX_SECRET", "leak")
    monkeypatch.setenv("RUST_LOG", "trace")
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "snapshot"}, "respond": {"envelope": ok("snapshot", {"app": "A"})}})
    run(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id="task-1", owner="bot"))
    run(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id="task-1", owner="bot"))
    run(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id="task-2", owner="bot"))
    first, second, third = [call for call in desk.calls() if call["argv"][0] == "snapshot"]
    assert first["env"]["TERMX_SECRET"] is None and first["env"]["RUST_LOG"] is None
    home = first["env"]["AGENT_DESKTOP_HOME"]
    assert os.path.isabs(home) and first["cwd"] == os.path.realpath(home) and home.startswith(str(desk.home_root))
    if os.name != "nt":
        assert oct(os.stat(home).st_mode & 0o777) == "0o700"
    assert first["argv"][1] == second["argv"][1] != third["argv"][1] and first["argv"][1].startswith("--session=t")


def test_stdin_delivery_and_log_hygiene(tmp_path, caplog):
    desk = ready(tmp_path)
    caplog.set_level(logging.DEBUG, logger="termx.desktop.agent_desktop")
    run(desk.client.run(ad.req_act("set_value", REF, text="top secret text"), task_id="t"))
    assert desk.last_stdin() == "top secret text"
    call = desk.spawns()[-1]
    assert "top secret text" not in " ".join(call["argv"])
    assert "top secret text" not in caplog.text and "SECRET-ECHO" not in caplog.text and "Secret Name" not in caplog.text


def test_error_envelopes_are_parsed_with_exit_code_one_and_two(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "find"}, "respond": {"envelope": err("find", "INVALID_ARGS"), "exit": 2}},
               {"match": {"command": "get"}, "respond": {"envelope": err("get", "STALE_REF", retry="safe")}},
               {"match": {"command": "click"}, "respond": {"envelope": err("click", "ACTION_FAILED", retry="never", delivery="delivered_unverified", details={"checks": [{"check": "enabled"}]})}})
    env = run(desk.client.run(ad.req_find(app_name="Notes", name="x"), task_id="t"))
    assert not env.ok and env.error.code == "INVALID_ARGS" and ad.classify(env.error) == "failed"
    env = run(desk.client.run(ad.req_get(REF), task_id="t"))
    assert env.error.retry_safe and ad.classify(env.error) == "stale"
    env = run(desk.client.run(ad.req_act("click", REF), task_id="t"))
    assert not env.error.retry_safe and env.error.delivery == "delivered_unverified"
    assert "synthetic" not in repr(env)  # message text is dropped


def test_unknown_error_code_is_a_generic_failure(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "get"}, "respond": {"envelope": err("get", "SOMETHING_NEW")}})
    env = run(desk.client.run(ad.req_get(REF), task_id="t"))
    assert ad.classify(env.error) == "failed"


@pytest.mark.parametrize("respond,kind", [
    ({"stdout_raw": "not json"}, "bad_output"),
    ({"stdout_raw": ""}, "bad_output"),
    ({"stdout_raw": "[1,2]"}, "bad_output"),
    ({"stdout_raw": "[" * 200_000}, "bad_output"),
    ({"stdout_bytes": ad.MAX_STDOUT + 10}, "output_too_large"),
])
def test_unusable_output_is_a_typed_failure(tmp_path, respond, kind):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "list-apps"}, "respond": respond})
    with pytest.raises(ad.AgentDesktopFailure) as caught:
        run(desk.client.run(ad.req_list_apps()))
    assert caught.value.kind == kind and "not json" not in str(caught.value)


def test_stderr_is_ignored(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "list-apps"}, "respond": {"envelope": ok("list-apps", {"apps": []}), "stderr": "noise " * 1000}})
    assert run(desk.client.run(ad.req_list_apps())).ok


def test_wall_timeout_kills_the_process(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "list-apps"}, "respond": {"envelope": ok("list-apps", {"apps": []}), "delay_ms": 3000}})
    request = ad.Request("list-apps", ("--timeout-ms=5000",), wall_s=0.4, kind="meta", session=False)
    with pytest.raises(ad.AgentDesktopFailure) as caught:
        run(desk.client.run(request))
    assert caught.value.kind == "timeout" and caught.value.delivery_unknown


def test_cancel_terminates_the_child(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "snapshot"}, "respond": {"envelope": ok("snapshot", {}), "delay_ms": 3000}})

    async def go():
        cancel = asyncio.Event()
        task = asyncio.create_task(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id="t", cancel=cancel))
        await asyncio.sleep(0.4)
        cancel.set()
        with pytest.raises(ad.AgentDesktopFailure) as caught:
            await task
        return caught.value.kind

    assert run(go()) == "cancelled"


def test_kill_task_terminates_running_children(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "snapshot"}, "respond": {"envelope": ok("snapshot", {}), "delay_ms": 5000}})

    async def go():
        task = asyncio.create_task(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id="victim"))
        await asyncio.sleep(0.4)
        desk.client.kill_task("victim")
        with pytest.raises(ad.AgentDesktopFailure):
            await task

    started = asyncio.run(asyncio.wait_for(go(), 4))
    assert started is None


def test_reads_are_limited_to_four_at_once(tmp_path):
    desk = ready(tmp_path)
    desk.rules({"match": {"command": "snapshot"}, "respond": {"envelope": ok("snapshot", {"app": "A"}), "delay_ms": 300}})

    async def go():
        await asyncio.gather(*(desk.client.run(ad.req_snapshot(app_name="Notes"), task_id=f"t{i}") for i in range(8)))

    import time
    start = time.monotonic()
    run(go())
    assert time.monotonic() - start >= 0.55  # two waves of four


def test_thread_fallback_when_the_loop_cannot_spawn(tmp_path, monkeypatch):
    desk = ready(tmp_path)

    async def refuse(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    desk.rules({"match": {"command": "list-apps"}, "respond": {"envelope": ok("list-apps", {"apps": []})}})
    assert run(desk.client.run(ad.req_list_apps())).ok


def test_session_directory_is_removed_only_inside_sessions(tmp_path):
    desk = ready(tmp_path)
    home = desk.client.home_for("host")
    sid = desk.client.session_id("task-x")
    (home / "sessions" / sid / "snapshots").mkdir(parents=True)
    (home / "sessions" / sid / "snapshots" / "refmap.json").write_text("names")
    other = home / "sessions" / "keep-me"
    other.mkdir()
    desk.client.cleanup_task("task-x")
    assert not (home / "sessions" / sid).exists() and other.exists()


def test_status_for_denied_accessibility_and_macos_27_label(tmp_path):
    desk = FakeDesktop(tmp_path)
    desk.accessibility = "denied"
    desk.write()
    status = run(desk.client.status(refresh=True))
    assert not status.ready and status.issue == "permission_denied" and "Accessibility" in status.permission["guidance"]
    assert "Device Control and Data Access" in ad.accessibility_label((27, 0))


def test_linux_without_accessibility_bus_is_not_ready(tmp_path):
    desk = FakeDesktop(tmp_path, platform="linux")
    desk.surfaces = []
    desk.write()
    pins = desk.pins()
    status = run(desk.build_client(pins=pins).status(refresh=True))
    assert status.issue == "no_accessibility_bus" and not status.ready


def test_musl_linux_is_unsupported(tmp_path):
    desk = FakeDesktop(tmp_path, platform="linux")
    platform = ad.HostPlatform("linux", "x86_64", glibc=False)
    status = run(desk.build_client(platform=platform).status(refresh=True))
    assert not status.ready and status.platform_reason == "unsupported_platform"


def test_windows_status_needs_no_permission(tmp_path):
    desk = FakeDesktop(tmp_path, platform="windows")
    status = run(desk.client.status(refresh=True))
    assert status.ready and status.permission["state"] == "not_required"


def test_ensure_fresh_never_raises_and_makes_a_first_turn_ready(tmp_path):
    desk = FakeDesktop(tmp_path)
    assert not desk.client.ready_cached()
    assert run(desk.client.ensure_fresh()).ready and desk.client.ready_cached()
    empty = FakeDesktop(tmp_path / "e", install=False)
    assert not run(empty.client.ensure_fresh()).ready


def test_permission_denied_from_a_real_call_flips_the_cache(tmp_path):
    desk = ready(tmp_path)
    desk.client.mark_permission_denied()
    assert not desk.client.ready_cached()


def test_real_executable_shim_runs_without_the_launcher_seam(tmp_path):
    """One test through the production argv assembly: a real executable, no launcher."""
    if os.name == "nt":
        pytest.skip("needs a shebang executable")
    desk = FakeDesktop(tmp_path, install=False)
    desk.bin_dir.mkdir(parents=True, exist_ok=True)
    shim = desk.binary
    shim.write_text(f"#!{sys.executable}\n" + SHIM_SOURCE.read_text("utf-8"))
    shim.chmod(0o700)
    desk.helper.write_bytes(b"")
    desk.helper.chmod(0o700)
    desk.scenario.write_text(json.dumps([{"match": {"command": "version"}, "respond": {"envelope": ok("version", {"os": "macos", "target": "aarch64", "version": "1.2.0"})}},
                                         {"match": {"command": "status"}, "respond": {"envelope": ok("status", status_data())}}]))
    client = desk.build_client(launcher=())
    assert run(client.status(refresh=True)).ready
