"""CLI sessions: catalogue/argv builders, the session service and its lifecycle (fake CLI, no network)."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_fake import alive, git_repo, posix_only, wait_for, write_fake_cli
from termx.cli_sessions import catalogue as C
from termx.cli_sessions import status as S
from termx.cli_sessions.service import CliSessionError, CliSessionService
from termx.development.delivery import DeliveryService
from termx.sessions import SessionManager
from termx.workspace.store import WorkspaceStore

# -- catalogue ---------------------------------------------------------------------


def test_every_cli_has_typed_argv_and_resolves_nothing_from_the_model():
    plan = C.build_launch("claude", executable="/bin/claude", working_dir="/work", prompt="Fix the bug", model="sonnet")
    assert plan.argv[0] == "/bin/claude"
    assert plan.argv[plan.argv.index("--model") + 1] == "sonnet"
    assert plan.argv[-2:] == ["--", "Fix the bug"] and plan.prompt_in_argv
    sid = plan.argv[plan.argv.index("--session-id") + 1]
    assert str(uuid.UUID(sid)) == sid == plan.native_session_id
    codex = C.build_launch("codex", executable="/bin/codex", working_dir="/work", prompt="Add tests")
    assert codex.argv[:3] == ["/bin/codex", "-C", "/work"]
    gemini = C.build_launch("gemini", executable="/bin/gemini", working_dir="/work", prompt="Explain")
    assert gemini.argv[-2:] == ["--prompt-interactive", "Explain"]
    aider = C.build_launch("aider", executable="/bin/aider", working_dir="/work", prompt="Refactor")
    assert aider.argv == ["/bin/aider"] and aider.typed_prompt == "Refactor" and not aider.prompt_in_argv
    with pytest.raises(C.UnknownCli):
        C.build_launch("rm", executable="/bin/rm", working_dir="/")


def test_read_only_modes_use_each_clis_own_flags_and_unverified_ones_refuse():
    assert C.build_launch("claude", executable="c", working_dir="/w", mode="read_only").argv[1:3] == ["--permission-mode", "plan"]
    assert C.build_launch("codex", executable="c", working_dir="/w", mode="read_only").argv[3:5] == ["-s", "read-only"]
    with pytest.raises(C.CatalogueError, match="no verified read only mode"):
        C.build_launch("aider", executable="a", working_dir="/w", mode="read_only")


@pytest.mark.parametrize("cli", sorted(C.CATALOGUE))
def test_bypass_is_refused_without_sandbox_and_human_approval(cli):
    with pytest.raises(C.BypassRefused):
        C.build_launch(cli, executable="x", working_dir="/w", mode="bypass")
    allowed = C.build_launch(cli, executable="x", working_dir="/w", mode="bypass", allow_bypass=True)
    flags = C.CATALOGUE[cli].bypass_flags
    assert any(token in flags for token in allowed.argv)
    allowed.check(allow_bypass=True)
    with pytest.raises(C.BypassRefused):
        allowed.check()  # the independent check on the finished vector still refuses it


@pytest.mark.parametrize(
    "cli, argv",
    [
        ("claude", ["c", "--dangerously-skip-permissions"]),
        ("claude", ["c", "--permission-mode", "bypassPermissions"]),
        ("claude", ["c", "--allow-dangerously-skip-permissions"]),
        ("codex", ["c", "--dangerously-bypass-approvals-and-sandbox"]),
        ("codex", ["c", "-s", "danger-full-access"]),
        ("codex", ["c", "--yolo"]),
        ("gemini", ["g", "--yolo"]),
        ("aider", ["a", "--yes-always"]),
    ],
)
def test_assert_no_bypass_catches_every_known_flag(cli, argv):
    with pytest.raises(C.BypassRefused):
        C.assert_no_bypass(cli, argv)
    C.assert_no_bypass(cli, argv, allow=True)


def test_a_prompt_cannot_smuggle_a_flag_or_trip_the_bypass_check():
    smuggled = C.build_launch("claude", executable="c", working_dir="/w", prompt="--dangerously-skip-permissions")
    assert smuggled.typed_prompt == "--dangerously-skip-permissions" and "--dangerously-skip-permissions" not in smuggled.argv
    smuggled.check()
    plain = C.build_launch("codex", executable="c", working_dir="/w", prompt="yolo")
    assert plain.argv[-1] == "yolo"
    plain.check()  # a prompt that spells a bypass token is only a prompt
    long_prompt = C.build_launch("claude", executable="c", working_dir="/w", prompt="x" * (C.ARGV_PROMPT_CHARS + 1))
    assert long_prompt.typed_prompt and not long_prompt.prompt_in_argv
    with pytest.raises(C.CatalogueError):
        C.build_launch("claude", executable="c", working_dir="/w", prompt="a\x00b")
    with pytest.raises(C.CatalogueError):
        C.build_launch("claude", executable="c", working_dir="/w", prompt="x" * (C.MAX_PROMPT_CHARS + 1))


def test_model_and_session_values_are_validated_tokens():
    for bad in ("--evil", "a b", "x;rm", "", "$(id)"):
        if bad:
            with pytest.raises(C.CatalogueError):
                C.build_launch("claude", executable="c", working_dir="/w", model=bad)
    with pytest.raises(C.CatalogueError):
        C.build_launch("claude", executable="c", working_dir="/w", native_session_id="not-a-uuid")
    with pytest.raises(C.CatalogueError):
        C.build_launch("claude", executable="c", working_dir="/w", mode="turbo")


def test_resume_builders_per_cli():
    sid = str(uuid.uuid4())
    claude = C.build_resume("claude", executable="c", working_dir="/w", native_session_id=sid)
    assert claude.argv == ["c", "--resume", sid]
    assert C.build_resume("claude", executable="c", working_dir="/w", native_session_id=None).argv == ["c", "--continue"]
    assert C.build_resume("codex", executable="c", working_dir="/w", native_session_id=None).argv == ["c", "-C", "/w", "resume", "--last"]
    assert C.build_resume("codex", executable="c", working_dir="/w", native_session_id="abc-1").argv[-2:] == ["resume", "abc-1"]
    with pytest.raises(C.CatalogueError):
        C.build_resume("aider", executable="a", working_dir="/w", native_session_id=None)
    with pytest.raises(C.CatalogueError):
        C.build_resume("claude", executable="c", working_dir="/w", native_session_id="--dangerously-skip-permissions")


def test_environment_is_scrubbed_of_termx_and_foreign_credentials():
    base = {
        "PATH": "/bin", "HOME": "/h", "TERMX_PASSCODE": "p", "TERMX_AI_KEY": "k", "AWS_SECRET_ACCESS_KEY": "s",
        "GITHUB_TOKEN": "t", "MY_PASSWORD": "pw", "OPENAI_API_KEY": "sk-o", "ANTHROPIC_API_KEY": "sk-a",
        "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CODEX_SANDBOX": "seatbelt", "EDITOR": "vi",
    }
    claude = C.scrub_environment(C.CATALOGUE["claude"], base)
    assert claude["ANTHROPIC_API_KEY"] == "sk-a"  # the CLI's own credential still reaches it
    for name in ("TERMX_PASSCODE", "TERMX_AI_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "MY_PASSWORD", "OPENAI_API_KEY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        assert name not in claude
    assert claude["PATH"] == "/bin" and claude["EDITOR"] == "vi" and claude["TERM"]
    codex = C.scrub_environment(C.CATALOGUE["codex"], base)
    assert codex["OPENAI_API_KEY"] == "sk-o" and "ANTHROPIC_API_KEY" not in codex and "CODEX_SANDBOX" not in codex


def test_detect_installed_reports_each_cli_and_degrades_when_missing(monkeypatch, tmp_path):
    fake = write_fake_cli(tmp_path)

    def resolve(name, **kwargs):
        return fake if name == "claude" else None

    monkeypatch.setattr(C, "resolve_executable", resolve)

    async def version(executable, args, timeout=8.0):
        return "9.9.9 (fake)"

    monkeypatch.setattr(C, "probe_version", version)
    found = {item["id"]: item for item in asyncio.run(C.detect_installed())}
    assert set(found) == {"claude", "codex", "gemini", "aider"}
    assert found["claude"]["installed"] is True and found["claude"]["version"] == "9.9.9 (fake)"
    assert found["codex"]["installed"] is False and found["codex"]["version"] is None
    assert "bypass_flags" in found["claude"] and "path" not in found["claude"]


# -- service -------------------------------------------------------------------------


def make_state(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    state = SimpleNamespace(
        workspace=SimpleNamespace(store=store),
        sessions=SessionManager(),
        delivery=DeliveryService(tmp_path / "delivery"),
        port=8787,
    )
    return state


def make_service(tmp_path, state=None, **kwargs):
    state = state or make_state(tmp_path)
    fake = write_fake_cli(tmp_path / "bin")
    kwargs.setdefault("executables", {"claude": fake, "codex": fake, "aider": fake})
    kwargs.setdefault("tick", 0.1)
    kwargs.setdefault("thresholds", {"quiet_after": 0.5, "idle_after": 3.0, "sustain": 0.6})
    kwargs.setdefault("grace", (0.6, 0.6))
    service = CliSessionService(state, directory=tmp_path / "cli-sessions", **kwargs)
    return state, service


async def settle(service, owner, session_id, wanted, timeout=8.0):
    return await wait_for(lambda: service.get(owner, session_id)["status"] in wanted and service.get(owner, session_id), timeout)


def pid_of(state, started):
    return state.sessions.get(started["pty_session_id"]).proc.pid


@posix_only
def test_lifecycle_start_status_send_read_stop(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o1", cli="claude", cwd=str(tmp_path), prompt="Do the thing")
        assert started["status"] in (S.STARTING, S.WORKING, S.WAITING_INPUT)
        assert "argv" not in started and "Do the thing" not in json.dumps(started)  # no argv or prompt text in the record
        pid = pid_of(state, started)
        pty = started["pty_session_id"]
        assert state.sessions.get(pty) is not None  # a real terminal the existing pty websocket can stream
        ready = await settle(service, "o1", started["id"], {S.WAITING_INPUT})
        assert ready["status_source"] == "heuristic" and ready["attention"] is True
        assert "FAKE READY" in service.read("o1", started["id"])["text"]
        sent = await service.send("o1", started["id"], "hello world")
        assert sent["status"] == S.WORKING  # not a stale prompt
        await wait_for(lambda: "ECHO:hello world" in service.read("o1", started["id"])["text"])
        assert (await settle(service, "o1", started["id"], {S.WAITING_INPUT}))["status"] == S.WAITING_INPUT
        stopped = await service.stop("o1", started["id"])
        assert stopped["status"] == S.DONE and stopped["ended_reason"] == "stopped" and stopped["ended_at"]
        assert state.sessions.get(pty) is None and not alive(pid)
        assert service.read("o1", started["id"])["text"]  # the final screen stays readable (in memory)
        assert service.store.get("cli_session", started["id"])["status"] == S.DONE
        with pytest.raises(CliSessionError) as ended:
            await service.send("o1", started["id"], "again")
        assert ended.value.status == 409

    asyncio.run(run())


@posix_only
def test_natural_exit_done_and_failed_and_events(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        events = []
        service.add_listener(lambda kind, row: events.append((kind, row["status"])))
        clean = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", clean["id"], {S.WAITING_INPUT})
        await service.send("o", clean["id"], "quit")
        done = await settle(service, "o", clean["id"], {S.DONE, S.FAILED})
        assert done["status"] == S.DONE and done["exit_code"] == 0 and done["ended_reason"] == "exited"
        broken = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", broken["id"], {S.WAITING_INPUT})
        await service.send("o", broken["id"], "fail")
        failed = await settle(service, "o", broken["id"], {S.DONE, S.FAILED})
        assert failed["status"] == S.FAILED and failed["exit_code"] == 3
        kinds = [kind for kind, _ in events]
        assert kinds.count("cli.session.created") == 2 and kinds.count("cli.session.exited") == 2
        assert "cli.session.status" in kinds

    asyncio.run(run())


@posix_only
def test_stop_escalates_to_sigkill_and_never_leaves_an_orphan(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "stubborn")

    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        pid = pid_of(state, started)
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        stopped = await asyncio.wait_for(service.stop("o", started["id"]), timeout=15)
        assert stopped["status"] == S.DONE and not alive(pid)
        assert state.sessions.list() == []

    asyncio.run(run())


@posix_only
def test_interrupt_sends_ctrl_c_and_does_not_end_the_session(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.interrupt("o", started["id"])
        await wait_for(lambda: "INTERRUPTED" in service.read("o", started["id"])["text"])
        assert service.get("o", started["id"])["status"] not in S.ENDED
        await service.stop("o", started["id"])

    asyncio.run(run())


@posix_only
def test_multiline_send_is_pasted_and_control_bytes_are_dropped(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.send("o", started["id"], "first\nsecond\x03\x1b[31mred")
        await wait_for(lambda: "ECHO:" in service.read("o", started["id"])["text"])
        text = service.read("o", started["id"])["text"]
        assert "first" in text and "second" in text and "red" in text
        with pytest.raises(CliSessionError) as big:
            await service.send("o", started["id"], "x" * 20_000)
        assert big.value.status == 400
        await service.stop("o", started["id"])

    asyncio.run(run())


@posix_only
def test_prompt_and_settings_reach_the_cli_and_settings_are_removed_after(tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_FILE", str(argv_file))

    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path), prompt="Summarise the repo", mode="read_only", model="sonnet")
        await wait_for(argv_file.exists)
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--permission-mode") + 1] == "plan" and argv[argv.index("--model") + 1] == "sonnet"
        assert argv[-2:] == ["--", "Summarise the repo"]
        settings = Path(argv[argv.index("--settings") + 1])
        hooks = json.loads(settings.read_text())["hooks"]
        assert set(hooks) >= {"Stop", "Notification", "UserPromptSubmit", "PermissionRequest"}
        assert oct(settings.stat().st_mode & 0o777) == "0o600"
        # the stored record has the prompt's size and digest, never its text or any path to the token
        record = json.dumps(service.store.get("cli_session", started["id"]))
        assert "Summarise" not in record and "clihook_" not in record and started["prompt_digest"]
        await service.stop("o", started["id"])
        assert not settings.exists()

    asyncio.run(run())


@posix_only
def test_a_prompt_for_a_cli_without_argv_prompt_is_typed_after_it_draws(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="aider", cwd=str(tmp_path), prompt="rename foo to bar")
        await wait_for(lambda: "ECHO:rename foo to bar" in service.read("o", started["id"])["text"], timeout=12)
        await service.stop("o", started["id"])

    asyncio.run(run())


@posix_only
def test_child_environment_is_scrubbed_but_keeps_hook_url_and_own_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMX_PASSCODE", "hunter2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-foreign")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-own")
    monkeypatch.setenv("CLAUDECODE", "1")

    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.send("o", started["id"], "env")
        await wait_for(lambda: "ENV:" in service.read("o", started["id"])["text"])
        names = service.read("o", started["id"])["text"].split("ENV:")[-1].replace("\n", "").split(",")
        assert "ANTHROPIC_API_KEY" in names and "TERMX_CLI_HOOK_URL" in names
        for gone in ("TERMX_PASSCODE", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "CLAUDECODE"):
            assert gone not in names
        await service.stop("o", started["id"])

    asyncio.run(run())


@posix_only
def test_bypass_is_refused_by_the_service_without_sandbox_and_confirmation(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        for kwargs in ({}, {"confirm_bypass": True}, {"sandbox_profile": "workspace"}):
            with pytest.raises(CliSessionError) as refused:
                await service.start(owner="o", cli="claude", cwd=str(tmp_path), mode="bypass", **kwargs)
            assert refused.value.status == 403
        assert state.sessions.list() == []
        # sandboxed + confirmed: the vector reaches the terminal layer (stubbed here; a real restricted launch
        # needs the host's sandbox backend), and only then.
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            from termx.terminals import TerminalError

            raise TerminalError("no sandbox backend in this test")

        state.sessions.create = create
        with pytest.raises(CliSessionError) as unavailable:
            await service.start(owner="o", cli="claude", cwd=str(tmp_path), mode="bypass", sandbox_profile="workspace", confirm_bypass=True)
        assert unavailable.value.status == 503
        assert "--dangerously-skip-permissions" in seen["argv"] and seen["sandbox_profile"] == "workspace"
        assert seen["env"] is None  # restricted sessions get the sandbox's own environment, not ours

    asyncio.run(run())


@posix_only
def test_a_missing_cli_and_bad_requests_are_refused(tmp_path):
    async def run():
        state, service = make_service(tmp_path, executables={})
        with pytest.raises(CliSessionError, match="not installed") as missing:
            await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        assert missing.value.status == 409
        with pytest.raises(CliSessionError) as unknown:
            await service.start(owner="o", cli="bash", cwd=str(tmp_path))
        assert unknown.value.status == 400
        state, service = make_service(tmp_path)
        with pytest.raises(CliSessionError) as isolation:
            await service.start(owner="o", cli="claude", cwd=str(tmp_path), isolation="worktree")
        assert isolation.value.status == 400  # no project
        with pytest.raises(CliSessionError) as not_git:
            await service.start(owner="o", cli="claude", cwd=str(tmp_path), isolation="worktree", project_id="p")
        assert not_git.value.status == 400
        assert state.sessions.list() == []

    asyncio.run(run())


@posix_only
def test_worktree_isolation_runs_the_cli_in_its_own_checkout(tmp_path):
    repo = git_repo(tmp_path / "repo")

    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(repo), project_id="proj", isolation="worktree")
        worktree = started["worktree"]
        assert started["isolation"] == "worktree" and started["cwd"] == worktree["path"] != str(repo)
        assert worktree["branch"].startswith("termx/cli-") and (Path(worktree["path"]) / "README.md").exists()
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.send("o", started["id"], "pwd")
        await wait_for(lambda: "CWD=" in service.read("o", started["id"])["text"])
        assert Path(service.read("o", started["id"])["text"].split("CWD=")[1].split()[0]).resolve() == Path(worktree["path"]).resolve()
        await service.stop("o", started["id"])
        # the checkout and its branch outlive the session: the owner reviews the work
        assert (Path(worktree["path"]) / "README.md").exists()
        assert state.delivery.worktrees("proj")[0]["branch"] == worktree["branch"]
        assert (repo / "README.md").read_text() == "hello\n"

    asyncio.run(run())


@posix_only
def test_a_failed_launch_after_the_worktree_leaves_no_orphan(tmp_path):
    repo = git_repo(tmp_path / "repo")

    async def run():
        state, service = make_service(tmp_path)

        def broken(**kwargs):
            from termx.terminals import TerminalError

            raise TerminalError("cannot spawn")

        state.sessions.create = broken
        with pytest.raises(CliSessionError):
            await service.start(owner="o", cli="claude", cwd=str(repo), project_id="proj", isolation="worktree")
        assert state.delivery.worktrees("proj") == []

    asyncio.run(run())


@posix_only
def test_sessions_belong_to_their_owner(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="alice", cli="claude", cwd=str(tmp_path))
        assert [row["id"] for row in service.list("alice")] == [started["id"]] and service.list("bob") == []
        for call in (
            lambda: service.get("bob", started["id"]),
            lambda: service.read("bob", started["id"]),
            lambda: service.row("bob", started["id"]),
        ):
            with pytest.raises(CliSessionError) as hidden:
                call()
            assert hidden.value.status == 404
        for coro in (service.send("bob", started["id"], "x"), service.interrupt("bob", started["id"]), service.stop("bob", started["id"])):
            with pytest.raises(CliSessionError) as hidden:
                await coro
            assert hidden.value.status == 404
        assert service.get("alice", started["id"])["status"] not in S.ENDED
        await service.stop("alice", started["id"])

    asyncio.run(run())


@posix_only
def test_a_restart_marks_what_was_live_as_lost(tmp_path):
    async def run():
        state, first = make_service(tmp_path)
        live = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        done = await first.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(first, "o", done["id"], {S.WAITING_INPUT})
        await first.stop("o", done["id"])
        pid = pid_of(state, live)
        # a new host process over the same store: nothing from the previous run is live
        second = CliSessionService(state, directory=tmp_path / "cli-sessions", tick=0.1, executables={})
        assert second.reconcile() == 1
        row = second.get("o", live["id"])
        assert row["status"] == S.LOST and row["status_detail"] == "host restarted" and row["ended_at"]
        assert second.get("o", done["id"])["status"] == S.DONE  # an ended record is left alone
        assert second.reconcile() == 0
        with pytest.raises(CliSessionError) as ended:
            await second.send("o", live["id"], "x")
        assert ended.value.status == 409
        await first.close()  # also ends the first run's monitor
        state.sessions.kill_all()
        assert not alive(pid)

    asyncio.run(run())


@posix_only
def test_close_marks_live_sessions_lost_and_revokes_hook_tokens(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        assert service.hooks.active() == 1
        await service.close()
        assert service.get("o", started["id"])["status"] == S.LOST and service.hooks.active() == 0
        with pytest.raises(CliSessionError) as closed:
            await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        assert closed.value.status == 503
        state.sessions.kill_all()

    asyncio.run(run())


@posix_only
def test_read_is_redacted_capped_and_marked_untrusted(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        await settle(service, "o", started["id"], {S.WAITING_INPUT})
        await service.send("o", started["id"], "secret")
        await wait_for(lambda: "leaked" in service.read("o", started["id"])["text"])
        out = service.read("o", started["id"])
        assert out["untrusted"] is True
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out["text"] and "hunter2hunter2" not in out["text"]
        await service.send("o", started["id"], "burst")
        await wait_for(lambda: "line 399" in service.read("o", started["id"], 20_000)["text"])
        capped = service.read("o", started["id"], 500)
        assert len(capped["text"]) <= 500 and capped["truncated"] is True
        assert len(service.read("o", started["id"], 10**9)["text"]) <= 20_000
        await service.stop("o", started["id"])

    asyncio.run(run())


def test_resume_plan_uses_the_stored_native_session(tmp_path):
    async def run():
        state, service = make_service(tmp_path)
        started = await service.start(owner="o", cli="claude", cwd=str(tmp_path))
        plan = service.resume_plan("o", started["id"])
        assert plan.argv[1:] == ["--resume", started["native_session_id"]]
        await service.stop("o", started["id"])

    if os.name != "nt":
        asyncio.run(run())
