# Interactive CLI sessions

TermX can run real coding-agent CLIs (`claude`, `codex`, `gemini`, `aider`) as
interactive terminal UIs inside TermX PTY sessions and track each one as a
first-class **CLI session**: it has a status, optional Git-worktree isolation,
and can be steered (send input, interrupt, stop) by the Main agent and by remote
clients. This complements the protocol engines (Codex app-server, Claude Agent
SDK, ACP) in `src/termx/engines/`, which stay the default for orchestrated work;
CLI sessions are for when you want the CLI's own TUI, sign-in and behaviour.

A CLI session is two things: a normal terminal (a `SessionManager` PTY, streamed
by the existing `/api/sessions/{pty_session_id}/pty` websocket, so no second
streaming path exists) and a durable `cli_session` record in the workspace store.

## Statuses

| status | meaning |
| --- | --- |
| `starting` | launched, nothing seen yet |
| `working` | the CLI is producing output or running a turn |
| `waiting_input` | at its prompt (turn finished) or asking a question; the next move is a message |
| `needs_approval` | blocked on a permission decision |
| `idle` | quiet with no recognised prompt (unknown, not necessarily ready) |
| `done` | the process exited 0, was stopped, or its terminal was closed |
| `failed` | the process exited non-zero or was killed |
| `lost` | the host restarted (or stopped) while it was live and its terminal did not survive; the process is gone (it can usually be resumed, see below) |

`waiting_input` and `needs_approval` set `attention: true`. Every change carries
a `status_source`:

* `hook`: the CLI told TermX itself.
  * **Claude Code**: TermX writes a private `--settings` file with command hooks for
    `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PermissionRequest`,
    `Notification` (`permission_prompt` -> needs_approval; `idle_prompt` and
    `elicitation_dialog` -> waiting_input), `Stop`, `StopFailure` and `SessionEnd`.
    Once hooks arrive they own working/waiting; output heuristics only escalate to
    `needs_approval` when a quiet screen shows an approval dialog no hook reported.
  * **Codex**: `-c notify=[forwarder]`. Codex's notify channel reports finished turns
    only (`agent-turn-complete` -> waiting_input), so working and approvals come from
    heuristics.
  * **gemini / aider**: no hook channel; heuristics only.
* `heuristic`: derived from the PTY: time since the last output plus per-CLI prompt
  patterns on the rendered output tail (`src/termx/cli_sessions/status.py`; a small
  line-oriented terminal model honours cursor moves and erases, because real TUIs place
  each word with cursor-column escapes and have no space characters to match). The pattern tables
  are plain data and **best-effort**: CLI wording drifts, a miss degrades to the
  quiet-time rule, and the latest marker on screen wins so a stale dialog does not
  stick. The tests use transcripts authored for them, not recordings.

## Security model

* **Argv is typed, never raw.** A caller picks a catalogue id and a few typed options
  (prompt, `mode`, `model`, isolation). The model-facing tool cannot pass any of them
  beyond `cli`, `project_id`, `prompt` and `isolation`. A prompt that starts with `-`,
  is long, or is a single word (a bare word can be a subcommand name even after `--`:
  `claude -- mcp` runs `claude mcp`) is typed into the TUI after start instead of going
  on the command line.
* **Permission-bypass flags are refused** (`--dangerously-skip-permissions`,
  `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--yes-always`, ...) unless
  the session runs in a restricted sandbox profile (`sandbox_profile: workspace`) **and**
  a human sent `confirm_bypass: true` over REST. The Agent tools can never reach bypass.
  An independent check re-scans the finished argv. Note a restricted launch uses the
  sandbox's own minimal environment and home, so the CLI is not signed in there.
* **Approvals.** `cli_session_start` always asks the owner (risk `delegation`: Auto and
  remembered rules never answer for them). `cli_session_send` is policy-gated, and
  while the session is `needs_approval` it becomes an always-ask `delegation` decision,
  because typing into a permission prompt answers for the human. If a prompt appears
  after the decision, execution refuses instead of typing blindly. Tools are
  Main-agent-only and hidden from child tasks and bots.
* **Untrusted output.** `cli_session_read` returns the cleaned screen text, redacted,
  capped (max 20,000 characters) and marked `untrusted: true`. The final screen of a
  finished or lost session is persisted with its record (the last 8,000 characters of the
  cleaned screen, redacted, with any hook URL token removed) so it stays readable after a
  host restart; it is only ever served through `read`, never in the session record.
* **Environment scrubbing.** The child gets the host environment minus every `TERMX_*`
  variable, credential-shaped names, and variables inherited from a parent coding agent
  (`CLAUDECODE`, `CODEX_*`); only the CLI's own provider variables pass through
  (`ANTHROPIC_API_KEY` for claude, `OPENAI_API_KEY` for codex, ...).
* **Hook tokens.** One random 256-bit token per session, held in memory as a SHA-256 digest,
  delivered to the CLI through `TERMX_CLI_HOOK_URL` (never on a command line or in a
  file), compared in constant time, rate-limited, revoked on exit, never logged. The token
  itself is never stored; only its digest is persisted with the record (a digest of 256
  random bits is not a credential) so the hook of a CLI that outlives a host restart keeps
  working. `POST /api/cli-sessions/hook/{token}` is loopback-only, capped at 16 KiB, and
  its payload is untrusted: a closed set of event names maps to a status hint, free text
  from the CLI is not carried into the status, and the only effect is that one session's
  status. The forwarder script itself sends only the event-name fields
  (`hook_event_name`, `notification_type`, `type`): prompts, tool inputs and replies in
  the CLI's event never leave the CLI process.
* **Process hygiene.** Stop sends SIGINT, then SIGTERM, then SIGKILL, each after a grace
  period, then reaps the PTY. With the PTY holder (the default on macOS and Linux, see
  [autonomous-agents.md](autonomous-agents.md#always-on-host-durable-terminals-keep-awake-and-health))
  the terminals are owned by the holder and survive a host stop; otherwise sessions are
  children of the host and closing it marks them `lost` and kills their terminals.
* **Isolation.** `isolation: worktree` creates a branch `termx/cli-<id>` checkout under the
  delivery service (needs the project's `git-read` and `git-write` scopes). The checkout
  and branch are kept after the session ends so the work can be reviewed; the user's
  checkout is untouched.
* **Authorization.** No new scopes. Viewing needs `terminal-view`; starting and steering
  need `terminal-control` (plus the project grant for a managed principal, as for
  `/api/sessions/{id}/pty`); the session's terminal is claimed for its principal so it
  streams for the owner only. Records are private to their principal; a compatibility
  passcode or device token acts as the single host owner and never sees managed users'
  sessions. Starting a CLI always needs `terminal-control`, also from the Main agent's tools
  and from a task-graph node (agent scopes alone are not enough). The full table of surfaces
  and scopes is in [remote-access.md](remote-access.md).

## REST contract

All paths are under `/api`. Credentials are the usual `Authorization: Bearer`,
`X-Termx-Passcode` or session cookie (with CSRF) already used for terminals.

`GET /cli-agents` (`terminal-view`)

```json
{"agents": [{"id": "claude", "label": "Claude Code", "installed": true, "version": "2.1.326 (Claude Code)",
             "hooks": "claude-settings", "resume": true, "modes": ["bypass", "default", "read_only"],
             "bypass_flags": ["--dangerously-skip-permissions", "..."], "verified": true, "note": ""}]}
```

`GET /cli-sessions` (`terminal-view`) -> `{"sessions": [Session]}`, newest first (max 200).

`POST /cli-sessions` (`terminal-control`) -> `201 Session`

```json
{"cli": "claude", "project_id": "<enrolled project id>", "cwd": null, "prompt": "optional first instruction",
 "isolation": "none | worktree", "mode": "default | read_only | bypass", "model": null,
 "sandbox_profile": "host | workspace", "confirm_bypass": false, "title": null}
```

Extra fields are rejected (`422`); there is no argv field. Without `project_id` the
folder is `cwd` or the terminal default and must be authorised.

`GET /cli-sessions/{id}` (`terminal-view`) -> `Session`.
`POST /cli-sessions/{id}/input` (`terminal-control`) body `{"text": "...", "submit": true}`:
types the text (multi-line text is pasted; control bytes other than line breaks are
dropped; max 16,000 characters) and presses Enter when `submit`.
`POST /cli-sessions/{id}/interrupt` writes Ctrl-C. `POST /cli-sessions/{id}/stop` ends it.
`POST /cli-sessions/{id}/resume` (`terminal-control`, plus the project grant for a managed
principal) body `{"confirm_bypass": false}` (optional): see below. -> `201 Session`.

Errors: `401` no/invalid credential, `403` missing scope, grant or refused bypass, `404`
unknown or someone else's session, `409` CLI not installed / session already ended / still
running (resume) / already resumed / cannot resume / folder gone / worktree could not be made,
`400` bad request, `503` could not spawn.

`Session`:

```json
{"id": "…", "cli": "claude", "label": "Claude Code", "title": "Claude Code · repo",
 "status": "waiting_input", "status_source": "hook", "status_detail": "hook:Stop",
 "status_updated_at": 1794965871.3, "attention": true, "project_id": "…", "cwd": "/…",
 "isolation": "worktree", "worktree": {"id": "…", "path": "/…", "branch": "termx/cli-…"},
 "pty_session_id": "…", "sandbox_profile": "host", "mode": "default", "started_at": 0,
 "updated_at": 0, "ended_at": null, "exit_code": null, "ended_reason": null,
 "native_session_id": "<uuid or null>", "resumable": true, "prompt_chars": 12,
 "prompt_digest": "…", "resumed_from": null, "resumed_to": null}
```

`ended_reason` is `exited | stopped | closed | lost | monitor error`. The prompt text,
argv and tokens are never stored or returned. Stream the TUI itself from
`/api/sessions/{pty_session_id}/pty` (needs `terminal-control`, as for any terminal).

## After a host restart

With the PTY holder, a CLI session's terminal (and the CLI inside it) survives the host
stopping, restarting or being killed. When the new host starts, `recover()` runs before
anything is marked lost:

* **Re-adopted.** A record whose terminal the holder still owns goes back to live: a monitor
  is restarted and the status is **re-derived from the replayed output tail** (the quiet
  screen shows the prompt, a dialog, or busy output), with `status_detail: "recovered after
  host restart"`. The CLI's own hook keeps working (the digest is persisted), as long as the
  host listens on the same port (a service does). Steering (send, interrupt, stop, the pty
  websocket) works as before, and the replay of what happened while no host ran is intact.
* **Finished while the host was down.** A held process that exited meanwhile is finished with
  its real exit code (`done` or `failed`, `ended_reason: exited`) and its final screen.
* **Lost.** Everything else (no holder, holder gone, machine rebooted) is marked `lost` with
  `ended_reason: lost`. `resumable` stays true when the CLI can resume its own saved
  conversation (`claude --resume <native id>`, `codex resume`), and the last screen stays
  readable.

**Resume** (`POST /api/cli-sessions/{id}/resume`) starts a NEW terminal and a NEW record for an
ended or lost session using that CLI's own resume command with fresh hooks. The new record
carries `resumed_from`, the old one `resumed_to` (a conversation is resumed once; the old record
stays as history); folder, worktree, sandbox profile and mode are the originals. A session that
ran in `bypass` mode is resumed in it only with `confirm_bypass: true` **and** a restricted
sandbox profile, exactly as for creation (otherwise `403`); resume never adds a bypass flag to an
ordinary session. Errors: `409` while the session is still running, already resumed, for a CLI
without resume, or if the folder no longer exists.

The resume command restores the conversation, not the process: running tool calls, background
processes and the screen are gone, and a CLI may ask to trust the folder again.

## GraphQL / events

```graphql
subscription { cli_session_events { type session { id cli status status_source attention pty_session_id } } }
```

`terminal-view` required; the scope is re-checked on every poll. The stream is a
1-second poll-and-diff of the caller's sessions (like `activity_events`):

* `cli.session.created`: a session is first seen. On connect every existing session
  arrives this way (ended ones are followed by `exited`).
* `cli.session.status`: the status or its source changed.
* `cli.session.exited`: the status became `done`, `failed` or `lost`.

`session` (including `resumed_from` / `resumed_to`) is the same shape as the REST `Session` (types `CliSession`, `CliSessionEvent`
in `schema.graphql`). In-process listeners (for push notifications) can subscribe with
`CliSessionService.add_listener`.

## Agent tools

`cli_session_start {cli, project_id?, prompt?, isolation?}` (always asks),
`cli_session_send {session_id, text, submit?}` (policy; always-ask `delegation` while
`needs_approval`), `cli_session_read {session_id, max_chars?}` (read; untrusted),
`cli_session_status {session_id?}` (read), `cli_session_stop {session_id}` (policy).
Capabilities `cli.session.start` and `cli.session.control` are host-mediated.

## Verification

Flags and hook formats were checked against the CLIs installed when this was written
(`claude` 2.1.326: `--settings`, `--session-id`, `--permission-mode`, `--resume`,
`--continue`, hook event names in the binary; `codex-cli` 0.169.0: `-C`, `-s`, `-c`,
`resume [ID]`, `resume --last`, `legacy_notify` / `agent-turn-complete`). `gemini` and
`aider` were not installed: their entries are best-effort. Tests:
`tests/test_cli_sessions.py`, `tests/test_cli_session_hooks.py`,
`tests/test_cli_session_tools.py`, `tests/test_cli_sessions_api.py` (a fake CLI, no
network) and `tests/workspace_cli_sessions_e2e.py` (a real uvicorn host, real sockets and
the real hook forwarder; no browser).

Checked live against the installed Claude Code 2.1.326 (no prompt was sent): the generated
`--settings` file is accepted and its command hooks run (`SessionStart`,
`UserPromptSubmit` and `StopFailure` were seen with `hook_event_name` on stdin); through the
real forwarder, a `SessionStart` reached the host and moved the session to `waiting_input`
with source `hook`; a fresh folder showed Claude's folder-trust dialog, which the output
patterns report as `needs_approval`; stop ended it cleanly with no orphan process. The
codex `-c notify=[...]` override and `-C`/`-s`/`resume` parse on codex-cli 0.169.0.

Not covered: the `Notification`, `PermissionRequest`, `PostToolUse` and `Stop` payloads from
a real logged-in turn, any real codex session, and `--resume` against a real logged-in
conversation (resume is exercised with the fake CLI only).

Surviving a host restart is covered by `tests/test_cli_sessions_durability.py`,
`tests/test_cli_sessions_resume_api.py` and, with real `termx` processes stopped and killed
(`tests/workspace_pty_restart_e2e.py`): the CLI session and its hook survive both a SIGTERM and a
SIGKILL of the host.

Claude asks to trust every new folder, so a worktree session starts on that dialog
(`needs_approval`). TermX does not accept it for the user; answering it from the Main agent
is an always-ask delegation.
