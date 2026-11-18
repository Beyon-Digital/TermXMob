"""CLI sessions across a host restart: re-adoption from the PTY holder, lost + resume, and the persisted final screen."""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from types import SimpleNamespace

import pytest

from cli_fake import alive, posix_only, wait_for, write_fake_cli
from test_cli_sessions import make_service, make_state, pid_of, settle
from termx.cli_sessions import status as S
from termx.cli_sessions.service import FINAL_TAIL_CHARS, CliSessionError, CliSessionService
from termx.config import config_dir
from termx.development.delivery import DeliveryService
from termx.pty_holder_client import HolderSupervisor, stop
from termx.sessions import SessionManager

pytestmark = posix_only


@pytest.fixture
def held(monkeypatch):
    monkeypatch.setenv("TERMX_PTY_HOLDER", "held")
    yield
    try:
        stop(config_dir(), kill=True)
    except Exception:  # noqa: BLE001
        pass


def held_state(tmp_path, store=None):
    """A fresh host process's view: its own SessionManager (and holder client) over the shared workspace store."""
    base = make_state(tmp_path)
    if store is not None:
        base.workspace = SimpleNamespace(store=store)
    base.sessions = SessionManager(holder=HolderSupervisor(version="t", idle_exit_s=60))
    return base


def service_for(tmp_path, state):
    fake = write_fake_cli(tmp_path / "bin")
    return CliSessionService(
        state, directory=tmp_path / "cli-sessions", tick=0.1, executables={"claude": fake, "codex": fake, "aider": fake},
        thresholds={"quiet_after": 0.5, "idle_after": 3.0, "sustain": 0.6}, grace=(0.6, 0.6),
    )


def test_a_session_survives_a_host_restart_and_is_readopted_with_its_screen_and_hooks(tmp_path, held):
    async def run():
        first_state = held_state(tmp_path)
        first = service_for(tmp_path, first_state)
        started = await first.start(owner="o", cli="claude", cwd=str(tmp_path), prompt="Do the thing")
        assert first_state.sessions.get(started["pty_session_id"]).durable
        await settle(first, "o", started["id"], {S.WAITING_INPUT})
        await first.send("o", started["id"], "hookurl")
        pty = first_state.sessions.get(started["pty_session_id"])
        await wait_for(lambda: b"HOOKURL=" in pty.output_tail(4096))
        token = re.search(r"clihook_[A-Za-z0-9_-]+", pty.output_tail(4096).decode()).group(0)
        pid = pid_of(first_state, started)
        # the host stops: record kept as it was, terminal detached rather than killed
        await first.close()
        first_state.sessions.kill_all()
        assert alive(pid)
        mid = first.store.get("cli_session", started["id"])
        assert mid["status"] != S.LOST and mid["ended_at"] is None and mid["hook_digest"] and token not in json.dumps(mid)

        # a new host over the same store and the same holder
        second_state = held_state(tmp_path, first.store)
        second = service_for(tmp_path, second_state)
        outcome = await second.recover()
        assert outcome == {"adopted": 1, "finished": 0, "lost": 0}
        row = second.get("o", started["id"])
        assert row["status"] not in S.ENDED and row["pty_session_id"] == started["pty_session_id"]
        assert "FAKE READY" in second.read("o", started["id"])["text"] and "HOOKURL=" in second.read("o", started["id"])["text"]
        assert token not in json.dumps(second.store.get("cli_session", started["id"])), "a hook token the CLI printed never reaches disk"
        # status is re-derived from the replayed tail: the quiet screen shows the CLI's prompt again
        assert (await settle(second, "o", started["id"], {S.WAITING_INPUT, S.IDLE}))["status"] in (S.WAITING_INPUT, S.IDLE)
        # steering works against the surviving process
        sent = await second.send("o", started["id"], "after restart")
        assert sent["status"] == S.WORKING
        await wait_for(lambda: "ECHO:after restart" in second.read("o", started["id"])["text"])
        # the CLI's own hook (token from before the restart) still reaches the new host
        assert second.apply_hook(token, b'{"hook_event_name": "PermissionRequest"}') == {"ok": True}
        assert second.get("o", started["id"])["status"] == S.NEEDS_APPROVAL and second.get("o", started["id"])["status_source"] == "hook"
        stopped = await second.stop("o", started["id"])
        assert stopped["status"] == S.DONE and not alive(pid)
        assert second_state.sessions.holder.status()["sessions"] == 0

    asyncio.run(run())


def test_without_a_holder_a_restart_marks_sessions_lost_and_keeps_the_last_screen(tmp_path):
    async def run():
        state, first = make_service(tmp_path)
        started = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(first, "o", started["id"], {S.WAITING_INPUT})
        await first.send("o", started["id"], "secret")
        await wait_for(lambda: "leaked" in first.read("o", started["id"])["text"])
        await first.close()  # the host stops; its direct terminals die with it
        state.sessions.kill_all()
        second = CliSessionService(state, directory=tmp_path / "cli-sessions", tick=0.1, executables={})
        assert await second.recover() == {"adopted": 0, "finished": 0, "lost": 0}  # close() already marked it
        row = second.get("o", started["id"])
        assert row["status"] == S.LOST and row["resumable"] is True and row["ended_reason"] == "lost"
        stored = second.store.get("cli_session", started["id"])
        # the final screen survived the restart, redacted and bounded, and read() serves it from the record
        assert "FAKE READY" in stored["final_tail"] and len(stored["final_tail"]) <= FINAL_TAIL_CHARS
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in stored["final_tail"] and "hunter2hunter2" not in stored["final_tail"]
        out = second.read("o", started["id"])
        assert "FAKE READY" in out["text"] and out["untrusted"] is True and "hunter2hunter2" not in out["text"]
        assert "final_tail" not in second.public(stored), "the screen is only served through read(), never in the record"

    asyncio.run(run())


def test_a_crash_with_no_graceful_close_is_marked_lost_by_recover(tmp_path):
    async def run():
        state, first = make_service(tmp_path)
        started = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        pid = pid_of(state, started)
        os.kill(pid, signal.SIGKILL)  # nothing ran close(): the record still says live
        first._closed = True
        for live in first._live.values():
            live.task.cancel()
        second = CliSessionService(state, directory=tmp_path / "cli-sessions", tick=0.1, executables={})
        state.sessions.kill_all()
        result = await second.recover()
        assert result["lost"] == 1 and second.get("o", started["id"])["status"] == S.LOST
        assert second.get("o", started["id"])["resumable"] is True
        assert second.read("o", started["id"])["text"] == "", "no persisted screen exists after a hard crash, and nothing is invented"

    asyncio.run(run())


def test_a_held_process_that_ended_while_the_host_was_down_is_finished_with_its_real_exit(tmp_path, held):
    async def run():
        first_state = held_state(tmp_path)
        first = service_for(tmp_path, first_state)
        started = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(first, "o", started["id"], {S.WAITING_INPUT})
        await first.send("o", started["id"], "fail")  # exits 3 while no host is watching
        await first.close()
        first_state.sessions.kill_all()
        second_state = held_state(tmp_path, first.store)
        second = service_for(tmp_path, second_state)
        await wait_for(lambda: second_state.sessions.holder.acquire(create=False) is not None)
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            listed = second_state.sessions.holder.acquire(create=False).list()["sessions"]
            if listed and not listed[0]["alive"]:
                break
            await asyncio.sleep(0.1)
        outcome = await second.recover()
        assert outcome["finished"] == 1 and outcome["adopted"] == 0 and outcome["lost"] == 0
        row = second.get("o", started["id"])
        assert row["status"] == S.FAILED and row["exit_code"] == 3 and row["ended_reason"] == "exited"
        assert "FAKE READY" in second.store.get("cli_session", started["id"])["final_tail"]
        assert second_state.sessions.list() == [] and second_state.sessions.holder.status()["exited_sessions"] == 0

    asyncio.run(run())


def test_the_session_table_and_the_holder_agree_after_recovery_so_nothing_is_adopted_twice(tmp_path, held):
    async def run():
        first_state = held_state(tmp_path)
        first = service_for(tmp_path, first_state)
        started = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        await first.close()
        first_state.sessions.kill_all()
        second_state = held_state(tmp_path, first.store)
        second = service_for(tmp_path, second_state)
        assert (await second.recover())["adopted"] == 1
        assert (await second.recover()) == {"adopted": 0, "finished": 0, "lost": 0}, "recover is idempotent"
        assert len(second._live) == 1 and len(second_state.sessions.list()) == 1
        await second.stop("o", started["id"])

    asyncio.run(run())


# -- resume ------------------------------------------------------------------------------------


def lost_session(tmp_path, monkeypatch, **kwargs):
    """A lost claude session whose resume launch records its argv."""
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_FILE", str(argv_file))

    async def make():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path), **kwargs)
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.close()
        state.sessions.kill_all()
        fresh = CliSessionService(
            state, directory=tmp_path / "cli-sessions", tick=0.1, executables=service._executables,
            thresholds={"quiet_after": 0.5, "idle_after": 3.0, "sustain": 0.6}, grace=(0.6, 0.6),
        )
        return state, fresh, started

    return make, argv_file


def test_resume_starts_a_new_terminal_with_the_clis_own_resume_command_and_links_the_records(tmp_path, monkeypatch):
    make, argv_file = lost_session(tmp_path, monkeypatch)

    async def run():
        state, service, started = await make()
        assert service.get("o", started["id"])["status"] == S.LOST
        native = service.row("o", started["id"])["native_session_id"]
        argv_file.unlink()  # the first launch wrote it; the resumed launch is what is asserted on
        resumed = await service.resume("o", started["id"])
        assert resumed["id"] != started["id"] and resumed["resumed_from"] == started["id"] and resumed["cli"] == "claude"
        assert resumed["pty_session_id"] != started["pty_session_id"] and resumed["native_session_id"] == native
        assert resumed["status"] in (S.STARTING, S.WORKING, S.WAITING_INPUT) and resumed["resumed_to"] is None
        old = service.get("o", started["id"])
        assert old["resumed_to"] == resumed["id"] and old["status"] == S.LOST, "the old record stays as history"
        await wait_for(argv_file.exists)
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--resume") + 1] == native and "--settings" in argv, "the fresh terminal gets fresh hooks too"
        assert "--dangerously-skip-permissions" not in argv and "--session-id" not in argv
        assert service.hooks.active() == 1
        # a conversation is resumed once
        with pytest.raises(CliSessionError) as again:
            await service.resume("o", started["id"])
        assert again.value.status == 409
        await settle(service, "o", resumed["id"], {S.WAITING_INPUT})
        stopped = await service.stop("o", resumed["id"])
        assert stopped["status"] == S.DONE

    asyncio.run(run())


def test_resume_refusals_a_live_session_another_owner_a_cli_without_resume_a_missing_folder(tmp_path, monkeypatch):
    make, _ = lost_session(tmp_path, monkeypatch)

    async def run():
        state, service, started = await make()
        # someone else's session does not exist for the caller
        with pytest.raises(CliSessionError) as hidden:
            await service.resume("mallory", started["id"])
        assert hidden.value.status == 404
        # a live session cannot be resumed
        live = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        with pytest.raises(CliSessionError) as running:
            await service.resume("o", live["id"])
        assert running.value.status == 409 and "still running" in running.value.message
        # aider has no resume
        body = {k: v for k, v in service.row("o", started["id"]).items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        body.update(cli="aider", resumable=False, resumed_to=None)
        aider = service.store.create("cli_session", "o", body, None)
        with pytest.raises(CliSessionError) as unsupported:
            await service.resume("o", aider["id"])
        assert unsupported.value.status == 409 and "cannot resume" in unsupported.value.message
        # the folder is gone
        body.update(cli="claude", resumable=True, cwd=str(tmp_path / "gone"))
        gone = service.store.create("cli_session", "o", body, None)
        with pytest.raises(CliSessionError) as missing:
            await service.resume("o", gone["id"])
        assert missing.value.status == 409
        await service.stop("o", live["id"])

    asyncio.run(run())


def test_resuming_a_bypass_session_needs_the_same_human_reconfirmation_as_creation(tmp_path, monkeypatch):
    make, _ = lost_session(tmp_path, monkeypatch)

    async def run():
        state, service, started = await make()
        body = {k: v for k, v in service.row("o", started["id"]).items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        body.update(mode="bypass", sandbox_profile="workspace", resumed_to=None)
        row = service.store.create("cli_session", "o", body, None)
        for kwargs in ({}, {"confirm_bypass": False}):
            with pytest.raises(CliSessionError) as refused:
                await service.resume("o", row["id"], **kwargs)
            assert refused.value.status == 403 and "confirmation" in refused.value.message
        assert state.sessions.list() == []
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            from termx.terminals import TerminalError

            raise TerminalError("no sandbox backend in this test")

        state.sessions.create = create
        with pytest.raises(CliSessionError) as unavailable:
            await service.resume("o", row["id"], confirm_bypass=True)
        assert unavailable.value.status == 503
        assert "--dangerously-skip-permissions" in seen["argv"] and "--resume" in seen["argv"] and seen["sandbox_profile"] == "workspace"
        assert seen["env"] is None, "restricted sessions get the sandbox's own environment, never ours"
        # a bypass row that somehow runs on the host profile is never resumable into bypass
        body.update(sandbox_profile="host")
        host_row = service.store.create("cli_session", "o", body, None)
        with pytest.raises(CliSessionError) as host:
            await service.resume("o", host_row["id"], confirm_bypass=True)
        assert host.value.status == 403

    asyncio.run(run())


def test_resume_never_adds_a_bypass_flag_to_an_ordinary_session():
    from termx.cli_sessions import catalogue as C

    plain = C.build_resume("claude", executable="c", working_dir="/w", native_session_id="abc-1")
    assert plain.argv == ["c", "--resume", "abc-1"] and plain.mode == "default"
    with pytest.raises(C.BypassRefused):
        C.build_resume("claude", executable="c", working_dir="/w", native_session_id="abc-1", mode="bypass")
    allowed = C.build_resume("claude", executable="c", working_dir="/w", native_session_id="abc-1", mode="bypass", allow_bypass=True)
    assert "--dangerously-skip-permissions" in allowed.argv
    ro = C.build_resume("claude", executable="c", working_dir="/w", native_session_id="abc-1", mode="read_only")
    assert ro.argv == ["c", "--permission-mode", "plan", "--resume", "abc-1"]
    codex = C.build_resume("codex", executable="c", working_dir="/w", native_session_id=None, settings_args=("-c", "notify=[]"))
    assert codex.argv == ["c", "-C", "/w", "-c", "notify=[]", "resume", "--last"]


def test_the_persisted_tail_is_redacted_and_bounded():
    huge = ("x" * 100 + " sk-abcdefghijklmnopqrstuvwxyz123456 api_key=hunter2hunter2\n") * 400
    tail = CliSessionService._persistable_tail(huge)
    assert len(tail) <= FINAL_TAIL_CHARS and "sk-abcdefghijklmnopqrstuvwxyz123456" not in tail and "hunter2hunter2" not in tail
