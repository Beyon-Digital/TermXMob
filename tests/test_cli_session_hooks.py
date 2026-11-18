"""CLI-session hooks (tokens, payload validation, forwarder) and the status machine (hook vs heuristic)."""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import cli_transcripts as T
from termx.cli_sessions import hooks as H
from termx.cli_sessions import status as S

# -- tokens ----------------------------------------------------------------------


def test_tokens_are_random_per_session_revocable_and_never_stored_raw():
    tokens = H.HookTokens()
    first, second = tokens.mint("s1", "claude"), tokens.mint("s2", "codex")
    assert first != second and first.startswith(H.TOKEN_PREFIX) and len(first) > 40
    assert tokens.resolve(first) == ("s1", "claude") and tokens.resolve(second) == ("s2", "codex")
    assert first not in tokens._grants and all(len(key) == 64 for key in tokens._grants)  # digests only
    assert tokens.revoke_session("s1") == 1
    assert tokens.resolve(first) is None and tokens.resolve(second) is not None
    assert tokens.active() == 1


@pytest.mark.parametrize("bad", ["", "nope", H.TOKEN_PREFIX, H.TOKEN_PREFIX + "x" * 500, None, 7, "mcpgw_abc"])
def test_malformed_tokens_never_resolve(bad):
    tokens = H.HookTokens()
    tokens.mint("s1", "claude")
    assert tokens.resolve(bad) is None


def test_token_comparison_is_constant_time(monkeypatch):
    tokens = H.HookTokens()
    token = tokens.mint("s1", "claude")
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(H.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert tokens.resolve(token) is not None and calls


def test_hook_posts_are_rate_limited_per_token(monkeypatch):
    monkeypatch.setattr(H, "RATE_LIMIT", (3, 60.0))
    tokens = H.HookTokens()
    token = tokens.mint("s1", "claude")
    assert [tokens.resolve(token) is not None for _ in range(5)] == [True, True, True, False, False]


# -- payload validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "payload, status, name",
    [
        ({"hook_event_name": "UserPromptSubmit"}, S.WORKING, "UserPromptSubmit"),
        ({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, S.WORKING, "PostToolUse"),
        ({"hook_event_name": "PermissionRequest"}, S.NEEDS_APPROVAL, "PermissionRequest"),
        ({"hook_event_name": "Stop"}, S.WAITING_INPUT, "Stop"),
        ({"hook_event_name": "SessionStart"}, S.WAITING_INPUT, "SessionStart"),
        ({"hook_event_name": "SessionEnd"}, None, "SessionEnd"),
        ({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission"}, S.NEEDS_APPROVAL, "Notification"),
        ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, S.WAITING_INPUT, "Notification"),
        ({"hook_event_name": "Notification", "notification_type": "elicitation_dialog"}, S.WAITING_INPUT, "Notification"),
        ({"hook_event_name": "Notification", "notification_type": "auth_success"}, None, "Notification"),
        ({"hook_event_name": "Notification", "notification_type": "brand_new_kind"}, None, "Notification"),
        ({"hook_event_name": "Notification"}, None, "Notification"),
    ],
)
def test_claude_hook_events_map_to_statuses(payload, status, name):
    event = H.parse_hook_payload("claude", json.dumps(payload).encode())
    assert (event.name, event.status) == (name, status)
    assert event.detail.startswith("hook:" + name)


def test_codex_notify_reports_finished_turns_only():
    body = {"type": "agent-turn-complete", "thread-id": "t", "turn-id": "u", "cwd": "/w", "input-messages": ["a"], "last-assistant-message": "done"}
    event = H.parse_hook_payload("codex", json.dumps(body).encode())
    assert event.status == S.WAITING_INPUT and "done" not in event.detail  # the assistant's words never become status text
    with pytest.raises(H.HookRejected):
        H.parse_hook_payload("codex", b'{"type":"approval-requested"}')


@pytest.mark.parametrize(
    "cli, body",
    [
        ("claude", b"not json"),
        ("claude", b"[]"),
        ("claude", b"null"),
        ("claude", b'"Stop"'),
        ("claude", b"{}"),
        ("claude", b'{"hook_event_name":"DropTables"}'),
        ("claude", b'{"hook_event_name":["Stop"]}'),
        ("claude", b'{"hook_event_name":"Stop; rm -rf /"}'),
        ("claude", b"\xff\xfe"),
        ("claude", b'{"hook_event_name":"Stop","pad":"' + b"x" * (H.MAX_HOOK_BYTES + 1) + b'"}'),
        ("codex", b"{}"),
        ("gemini", b'{"hook_event_name":"Stop"}'),
        ("aider", b"{}"),
    ],
)
def test_untrusted_payloads_are_rejected_without_other_effects(cli, body):
    with pytest.raises(H.HookRejected):
        H.parse_hook_payload(cli, body)


def test_notes_are_short_and_control_free():
    payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "\x1b[31mboom\x00\n" + "y" * 1000}
    event = H.parse_hook_payload("claude", payload)
    assert event.note == "permission_prompt"  # free text from the CLI is not carried into the status at all
    assert "\x1b" not in event.detail and len(event.detail) < 100


# -- generated configuration -------------------------------------------------------------


def test_claude_settings_subscribe_the_lifecycle_events_with_a_command_hook(tmp_path):
    forwarder = H.write_forwarder(tmp_path / "state")
    command = H.forwarder_command(forwarder, python="/usr/bin/python3")
    settings = H.claude_settings(command)
    json.dumps(settings)
    assert set(settings["hooks"]) == set(H.CLAUDE_SUBSCRIBED)
    for entries in settings["hooks"].values():
        (entry,) = entries
        assert entry["hooks"][0]["type"] == "command" and entry["hooks"][0]["command"] == command
    assert str(forwarder) in command and "clihook_" not in command  # the token lives only in the child's environment
    written = H.write_claude_settings(tmp_path / "state" / "s1", command)
    assert json.loads(written.read_text()) == settings
    if os.name == "posix":
        assert oct(written.stat().st_mode & 0o777) == "0o600" and oct(forwarder.parent.stat().st_mode & 0o777) == "0o700"


def test_codex_notify_override_is_a_toml_string_array(tmp_path):
    forwarder = H.write_forwarder(tmp_path)
    flag, value = H.codex_notify_args(forwarder, python="/usr/bin/python3")
    assert flag == "-c" and value.startswith("notify=")
    assert json.loads(value[len("notify="):]) == ["/usr/bin/python3", str(forwarder)]


def test_forwarder_is_refreshed_not_duplicated(tmp_path):
    path = H.write_forwarder(tmp_path)
    path.write_text("tampered", encoding="utf-8")
    assert H.write_forwarder(tmp_path).read_text(encoding="utf-8") == H.FORWARDER_SOURCE


class _Collector(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        self.seen.append((self.path, self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def _serve():
    _Collector.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_forwarder_posts_stdin_for_claude_and_argv_for_codex_and_always_exits_zero(tmp_path):
    forwarder = H.write_forwarder(tmp_path)
    server = _serve()
    url = f"http://127.0.0.1:{server.server_port}/api/cli-sessions/hook/clihook_x"
    env = {**os.environ, H.HOOK_ENV: url}
    try:
        done = subprocess.run([sys.executable, str(forwarder)], input=b'{"hook_event_name":"Stop"}', env=env, capture_output=True, timeout=20)
        assert done.returncode == 0 and done.stdout == b"" and done.stderr == b""
        payload = '{"type":"agent-turn-complete","last-assistant-message":"secret reply","input-messages":["a prompt"]}'
        done = subprocess.run([sys.executable, str(forwarder), payload], stdin=subprocess.DEVNULL, env=env, capture_output=True, timeout=20)
        assert done.returncode == 0
        # a long prompt in the CLI's event (Claude Code includes it) neither breaks the post nor is forwarded
        huge = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "p" * 200_000, "notification_type": 7}).encode()
        done = subprocess.run([sys.executable, str(forwarder)], input=huge, env=env, capture_output=True, timeout=20)
        assert done.returncode == 0
        assert [body for _, body in _Collector.seen] == [
            json.dumps({"hook_event_name": "Stop"}).encode(),
            json.dumps({"type": "agent-turn-complete"}).encode(),  # the assistant's words and the inputs stay local
            json.dumps({"hook_event_name": "UserPromptSubmit"}).encode(),
        ]
        assert all(path == "/api/cli-sessions/hook/clihook_x" for path, _ in _Collector.seen)
    finally:
        server.shutdown()
    for broken in ({**os.environ, H.HOOK_ENV: "http://127.0.0.1:9/never"}, {k: v for k, v in os.environ.items() if k != H.HOOK_ENV}):
        done = subprocess.run([sys.executable, str(forwarder)], input=b"{}", env=broken, capture_output=True, timeout=20)
        assert done.returncode == 0 and done.stderr == b""


# -- status machine ----------------------------------------------------------------------


def ticker(cli="aider", **kwargs):
    return S.StatusTracker(cli, quiet_after=1.0, idle_after=6.0, sustain=1.0, **kwargs)


def feed(tracker, transcript, *, quiet, now=100.0, **kwargs):
    return tracker.observe(now=now, quiet=quiet, tail=S.clean_text(transcript), **kwargs)


def test_clean_text_renders_what_a_person_would_see():
    text = S.clean_text("\x1b[2J\x1b[H\x1b[1mbold\x1b[0m\rspin\x1b]0;title\x07\x00ok")
    assert text == "spinok" and "\x1b" not in text  # a carriage return overwrites; escapes and NUL vanish
    assert S.clean_text("a" * 50, limit=10) == "a" * 10
    assert S.clean_text("one\r\ntwo\r\n\r\n") == "one\ntwo"
    assert S.clean_text("keep\x1b[2Jgone\r\nnew") == "gone\nnew"  # clear screen discards what came before
    assert S.clean_text("abc\x1b[2D\x1b[KZ") == "aZ"  # cursor left, erase to end of line, write
    assert S.clean_text("ab\x1b[3Cc") == "ab   c"  # cursor forward pads with spaces
    assert S.clean_text("first\r\nsecond\x1b[1A\x1b[2K\rX") == "X\nsecond"  # cursor up + erase line, then overwrite
    assert S.clean_text(b"caf\xc3\xa9 \xff") == "caf\u00e9 \ufffd"
    assert S.clean_text("x\n" * 1000).count("\n") < 400  # bounded


def test_words_placed_by_cursor_columns_are_read_with_their_spaces():
    text = S.clean_text(T.CLAUDE_TRUST_POSITIONED)
    assert "Quick safety check: Is this a project you created or one you trust?" in text
    assert "Yes, I trust this folder" in text and "Enter to confirm \u00b7 Esc to cancel" in text
    assert "Accessing workspace:" in text


def test_an_in_place_redraw_erases_the_stale_dialog():
    text = S.clean_text(T.CLAUDE_REDRAWN_IN_PLACE)
    assert "Do you want" not in text and "Ran git status." in text
    assert S.patterns_for("claude").classify(text) == "waiting"


@pytest.mark.parametrize(
    "cli, transcript, kind",
    [
        ("claude", T.CLAUDE_APPROVAL, "approval"),
        ("claude", T.CLAUDE_PROMPT, "waiting"),
        ("claude", T.CLAUDE_WORKING, "working"),
        ("claude", T.CLAUDE_STALE_APPROVAL_THEN_PROMPT, "waiting"),
        ("claude", T.CLAUDE_TRUST_POSITIONED, "approval"),
        ("claude", T.CLAUDE_MENU_POSITIONED, "waiting"),
        ("claude", T.CLAUDE_REDRAWN_IN_PLACE, "waiting"),
        ("codex", T.CODEX_APPROVAL, "approval"),
        ("codex", T.CODEX_PROMPT, "waiting"),
        ("aider", T.AIDER_YN, "approval"),
        ("aider", T.AIDER_PROMPT, "waiting"),
        ("aider", T.PLAIN_QUIET, None),
        ("unknown-cli", T.GENERIC_YN, "approval"),
    ],
)
def test_patterns_classify_authored_transcripts_with_the_latest_marker_winning(cli, transcript, kind):
    assert S.patterns_for(cli).classify(S.clean_text(transcript)) == kind


def test_a_new_session_is_starting_then_works_once_output_flows():
    tracker = ticker()
    assert tracker.status == S.STARTING
    assert tracker.observe(now=1.0, quiet=None, tail="") is None and tracker.status == S.STARTING
    change = tracker.observe(now=1.1, quiet=0.1, tail="Aider v0.1")
    assert (change.status, change.source) == (S.WORKING, "heuristic")


def test_quiet_with_an_approval_dialog_needs_approval_and_clears_on_a_prompt():
    tracker = ticker("codex")
    tracker.observe(now=1.0, quiet=0.1, tail="booting")
    assert feed(tracker, T.CODEX_APPROVAL, quiet=2.0).status == S.NEEDS_APPROVAL
    assert feed(tracker, T.CODEX_PROMPT, quiet=2.0).status == S.WAITING_INPUT


def test_a_dialog_still_being_drawn_does_not_flip_to_working_then_back():
    tracker = ticker("aider")
    tracker.observe(now=1.0, quiet=0.1, tail="x")
    assert tracker.status == S.WORKING
    assert feed(tracker, T.AIDER_YN, quiet=0.2, now=2.0) is None and tracker.status == S.WORKING
    assert feed(tracker, T.AIDER_YN, quiet=1.5, now=3.5).status == S.NEEDS_APPROVAL


def test_working_needs_sustained_output_after_a_prompt():
    tracker = ticker("aider")
    feed(tracker, T.AIDER_PROMPT, quiet=2.0)
    assert tracker.status == S.WAITING_INPUT
    # a blinking cursor / status-line refresh is not "working"
    assert feed(tracker, T.AIDER_PROMPT, quiet=0.1, now=10.0) is None and tracker.status == S.WAITING_INPUT
    assert feed(tracker, "tokens...", quiet=0.1, now=11.5).status == S.WORKING


def test_unrecognised_quiet_becomes_idle_after_a_while():
    tracker = ticker("aider")
    tracker.observe(now=1.0, quiet=0.1, tail="x")
    assert feed(tracker, T.PLAIN_QUIET, quiet=2.0, now=5.0) is None and tracker.status == S.WORKING
    assert feed(tracker, T.PLAIN_QUIET, quiet=7.0, now=10.0).status == S.IDLE


def test_exit_codes_decide_done_or_failed_and_end_is_final():
    clean = ticker()
    assert clean.observe(now=1, quiet=0.1, tail="", exited=True, exit_code=0).status == S.DONE
    broken = ticker()
    assert broken.observe(now=1, quiet=0.1, tail="", exited=True, exit_code=2).status == S.FAILED
    killed = ticker()
    assert killed.observe(now=1, quiet=0.1, tail="", exited=True, exit_code=None).status == S.FAILED
    stopped = ticker()
    assert stopped.observe(now=1, quiet=0.1, tail="", exited=True, exit_code=-15, stop_requested=True).status == S.DONE
    for tracker in (clean, broken, killed, stopped):
        assert tracker.observe(now=2, quiet=9, tail=T.CODEX_APPROVAL, exited=False) is None
        assert tracker.on_hook(S.WORKING, "hook:x") is None and tracker.lost() is None


def test_lost_is_terminal_and_only_from_a_live_state():
    tracker = ticker()
    assert tracker.lost("host restarted").status == S.LOST and tracker.detail == "host restarted"
    assert tracker.lost() is None


def test_hooks_set_status_with_hook_source_and_none_means_seen_only():
    tracker = ticker("claude", lifecycle_hooks=True)
    assert tracker.on_hook(None, "hook:SessionStart") is None and tracker.hook_seen and tracker.status == S.STARTING
    assert tracker.on_hook(S.WORKING, "hook:UserPromptSubmit").source == "hook"
    change = tracker.on_hook(S.NEEDS_APPROVAL, "hook:PermissionRequest")
    assert (change.status, change.source) == (S.NEEDS_APPROVAL, "hook")
    assert tracker.on_hook(S.NEEDS_APPROVAL, "hook:Notification (permission_prompt)") is None  # same status+source: no event
    assert tracker.detail == "hook:Notification (permission_prompt)"


def test_once_hooks_arrive_heuristics_no_longer_drive_working_or_waiting():
    tracker = ticker("claude", lifecycle_hooks=True)
    tracker.on_hook(S.WAITING_INPUT, "hook:Stop")
    # lots of output (a status line, a spinner, the user typing) does not override the hook...
    assert feed(tracker, "typing...", quiet=0.1, now=50.0) is None
    assert feed(tracker, "typing...", quiet=0.1, now=53.0) is None and tracker.status == S.WAITING_INPUT
    # ...and neither does a quiet screen without a recognised dialog
    assert feed(tracker, T.PLAIN_QUIET, quiet=30.0, now=90.0) is None and tracker.status == S.WAITING_INPUT


def test_a_quiet_approval_dialog_the_hooks_missed_still_escalates():
    tracker = ticker("claude", lifecycle_hooks=True)
    tracker.on_hook(S.WORKING, "hook:UserPromptSubmit")
    change = feed(tracker, T.CLAUDE_APPROVAL, quiet=3.0)
    assert (change.status, change.source) == (S.NEEDS_APPROVAL, "heuristic")


def test_a_hook_approval_clears_on_sustained_output_without_a_dialog():
    tracker = ticker("claude", lifecycle_hooks=True)
    tracker.on_hook(S.NEEDS_APPROVAL, "hook:PermissionRequest")
    assert feed(tracker, T.CLAUDE_WORKING, quiet=0.1, now=10.0) is None
    change = feed(tracker, T.CLAUDE_WORKING, quiet=0.1, now=11.5)
    assert (change.status, change.source) == (S.WORKING, "heuristic")


def test_before_any_hook_a_hook_capable_cli_falls_back_to_heuristics():
    tracker = ticker("claude", lifecycle_hooks=True)
    tracker.observe(now=1.0, quiet=0.1, tail="banner")
    assert tracker.status == S.WORKING and tracker.source == "heuristic"
    assert feed(tracker, T.CLAUDE_PROMPT, quiet=3.0, now=5.0).status == S.WAITING_INPUT


def test_codex_notify_marks_turn_complete_and_keeps_hook_attribution_over_a_matching_screen():
    tracker = ticker("codex")  # notify is not a lifecycle channel: heuristics still drive the rest
    tracker.observe(now=1.0, quiet=0.1, tail="working")
    assert tracker.on_hook(S.WAITING_INPUT, "hook:agent-turn-complete").source == "hook"
    assert feed(tracker, T.CODEX_PROMPT, quiet=2.0, now=5.0) is None and tracker.source == "hook"
    assert feed(tracker, T.CODEX_APPROVAL, quiet=2.0, now=9.0).status == S.NEEDS_APPROVAL


def test_input_sent_reports_working_not_a_stale_prompt_and_never_revives_a_session():
    tracker = ticker("aider")
    feed(tracker, T.AIDER_PROMPT, quiet=2.0)
    assert tracker.input_sent(50.0).status == S.WORKING
    ended = ticker()
    ended.observe(now=1, quiet=0, tail="", exited=True, exit_code=0)
    assert ended.input_sent(2.0) is None and ended.status == S.DONE
    fresh = ticker()
    assert fresh.input_sent(1.0) is None and fresh.status == S.STARTING


def test_pattern_tables_are_plain_data_per_known_cli():
    from termx.cli_sessions.catalogue import CATALOGUE

    assert set(S.PATTERNS) == set(CATALOGUE)
    for patterns in S.PATTERNS.values():
        assert all(isinstance(p, str) for p in patterns.approval + patterns.waiting + patterns.working)
        for kind in ("approval", "waiting", "working"):
            patterns.compiled(kind)  # every table compiles
