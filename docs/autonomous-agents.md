# Autonomous agents and remote control

TermX runs tasks and schedules in the host process. Closing a browser, losing a
remote connection, or hiding the desktop window does not stop that process.
The host must remain awake and running. The default desktop mode owns its backend
and stops it on Quit. Choose **Advanced → Run host as background service** to
install a separate user service and attach the desktop to it. Quit then detaches
the desktop while the host, tasks and schedules continue. **Access & sessions →
Background host service** also exposes explicit install, start, stop and uninstall
controls to host administrators.

The service uses systemd user services on Linux, a user LaunchAgent on macOS and
an interactive-user scheduled task on Windows. These start in the user's login
session; they do not establish unattended access before OS login. Computer
control requires a graphical session and the service's own platform permissions.
Desktop-bound native broker permissions do not transfer to the independent
service. Native Wayland input remains unsupported.

The equivalent command-line controls are:

```sh
termx service install --port 8787
termx service start
termx service status
termx service stop
termx service uninstall
```

Select a persistent private profile with `--config-dir`. App and service hosts
share an exclusive profile lock and cannot both own its state. The service binds
to a fixed loopback port. The desktop verifies a private challenge and the exact
profile/process identity before attaching. A stopped background service is not
silently restarted when the desktop is reopened.

## Always-on host: durable terminals, keep-awake and health

Three things make a TermX host safe to leave running.

**Terminals and CLI sessions survive a host restart.** Terminals are owned by a small
detached helper, the *PTY holder*, not by the host. Stop, restart, update or crash the host
(even `kill -9`) and the shells, `claude`/`codex` sessions and everything they run keep
going; the new host re-attaches with each terminal's recent output (256 KB per session)
intact, including what was printed while no host was running. Held mode is the default
on macOS and Linux (`TERMX_PTY_HOLDER=auto|held|direct`, or the saved host setting
`pty_holder`; the variable wins). What to know:

- The holder is a separate process per config directory (`termx pty-holder status` shows
  it; `termx pty-holder stop` drains it, `stop --kill` also ends every held terminal, which
  is the only thing that does). It exits by itself after 10 idle minutes with no terminal and
  no host (`TERMX_PTY_HOLDER_IDLE_S`). Quitting the app or stopping the service therefore does
  **not** end a running terminal; close it in the app or use `pty-holder stop --kill`.
- It is reachable only through a Unix socket in a private (0700) directory, checked on both
  ends against the owning user (`SO_PEERCRED` / `LOCAL_PEERCRED`); frames are length-prefixed
  with per-type size limits and JSON control messages, no pickle. It execs exactly the argv
  and environment the host hands it. A sandboxed terminal's wrapper and environment are built
  by the host first, so the holder can never loosen a sandbox. It logs ids and exit codes only,
  never terminal content (`pty-holder/holder.log`).
- If the holder cannot start (or on Windows) terminals fall back to *direct* mode and the host
  says so: `host_status.holder.reason`, a one-time warning notification and the audit log. With
  `pty_holder: held` it refuses to start a terminal instead of falling back. Direct terminals end
  with the host (`holder.direct_terminals` counts them).
- A holder from an older TermX build with nothing running is replaced on the next start; one that
  still owns sessions keeps serving them while it is compatible, and an incompatible one is drained
  (no new sessions, new terminals are direct, with the reason shown) until it empties.
- The supervisor definitions keep the holder out of reach of a stop or restart: the systemd unit
  uses `KillMode=process` and the launchd job `AbandonProcessGroup`. (systemd will note leftover
  processes in the unit's cgroup after a stop; that is the holder.) A host that was started by hand
  or by the desktop app is covered by the holder's own session and process group.
- A restarted host must listen on the same port: a surviving CLI keeps posting its status hook
  to the port it was started with. A service uses a fixed port; if a plain host starts on a
  different one, hooks are quiet and the output heuristics take over.

**The machine stays awake while work runs.** A refcounted lease holds the OS's idle-sleep
inhibitor (`caffeinate -i -w <host pid>` on macOS, `systemd-inhibit` around a watcher on Linux,
`SetThreadExecutionState` on Windows) while an agent task is running, a CLI session is working, or
the owner turned on **keep awake** (`update_host_settings(keep_awake: true)`, needs `host-admin`).
The inhibitor is tied to the host process, so a crash releases it. The display may still sleep.
`host_status.keep_awake` shows whether it is held, why, and any error (`supported: false` where the
OS offers no mechanism).

**Health.** `host_status` (query) and `host_status_events` (subscription, snake_case, `machine-view`;
a managed principal also needs `host-admin`) report uptime, version, the service supervisor state,
the holder (`mode`, `reason`, `sessions`, `direct_terminals`), recovery counts (recovering tasks, lost
and resumable CLI/engine sessions), keep-awake state and the last heartbeat. The unauthenticated
`health` query is unchanged. For watchdogs that cannot sign in, the host rewrites
`<config dir>/heartbeat.json` every ~10 seconds (atomic replace; `pid`, `version`, `port`, `at`,
`state` only): treat an `at` older than ~30 seconds as down.

After a restart the host also resumes interrupted tasks as before (once, from the task ledger) and
re-adopts CLI sessions ([cli-sessions.md](cli-sessions.md#after-a-host-restart)); native engine
sessions are flagged `resumable` when their engine supports it, and a new instruction in the same
conversation re-attaches them.

## Set up unattended work

1. Configure an AI connection in **Settings → AI & Models** and select it in a
   conversation with the intended project, engine and model.
2. For internal agents, an agent preset can select **Autonomous within host
   policy**. This uses the configured sandbox and tool policies. Browser consent,
   sensitive actions, missing capabilities and exhausted budgets can still need
   a user decision.
3. In **Automations → New scheduled task**, select the conversation, task prompt,
   success criteria, recurrence, run count, time budget and grant expiry. Select
   **Allow unattended runs within these limits** and create the schedule.
4. The unattended grant starts an internal Agent-mode plan automatically. It does
   not approve unrelated tool actions or increase the budget. Scheduled command
   jobs can run saved, explicitly authorized command steps without a model.
5. Use **Task supervision → Build agent team** on an active local internal
   Agent-mode coordinator to add workers. Editable workers get isolated
   workspaces; read-only workers inspect the authorized project. Team capacity
   and execution time are bounded across the parent and children.
6. For a local Internal Agent schedule, select **Allow this schedule to use
   specific browser tabs unattended**. Review the exact persistent-profile tabs,
   origins, actions, expiry and per-run duration. Each run then receives its own
   restricted browser grant within that saved consent, without a new manual
   handoff. Observation is required; tab creation, closing and file transfers are
   excluded from this unattended permission.

An unattended grant expires within 90 days and is bounded by its run count.
Changing its owner permissions or execution target invalidates it. Revocation
stops active runs and their descendants. Internal execution checks delegated
authority before planning, model turns, tool execution and checkpoint recovery;
the scheduler also reconciles active authority while work is in flight.

Slow provider planning does not block other schedules or remote cancellation.
The daemon admits at most eight concurrent schedule dispatches. Overlap policy
either skips an occurrence or retains one queued occurrence.

## Control work remotely

Connect to the host's HTTPS URL through a configured supported tunnel or private
network and sign in with a managed TermX account. The same workspace exposes
task history, approvals, team creation, steering and cancellation. Authorization
applies to each request and live socket. Reconnecting replays persisted events.
Revoking a session closes its sockets and rejects its subsequent requests.
An explicitly selected existing tunnel profile can be saved during service
installation for restart. No tunnel is enabled by default. Quick tunnel URLs can
change; a named stable profile is preferable for a persistent remote address.

**Pause** stops future scheduled occurrences. **Stop run** cancels a current run,
including one still planning. **Revoke and stop runs** revokes unattended
authority and cancels active work. Archiving retains history and does not stop an
already active run.

The scope each remote surface needs (REST, GraphQL operations and subscriptions, push
notifications) is listed in the [remote access matrix](remote-access.md). No new scopes were
added for CLI sessions, durable terminals, host health or task-graph executors.

## Interactive CLI sessions

The Main agent and remote clients can also run real coding CLIs (`claude`, `codex`,
`gemini`, `aider`) as live terminal sessions with status, optional worktree isolation
and steering. See [cli-sessions.md](cli-sessions.md) for statuses, the security model
and the REST/event contract.

## Browser and computer tools

The internal engine exposes managed browser tools through the task broker.
Interactive tasks need a task-specific handoff. Scheduled tasks can instead use
the explicit bounded consent described above. That authority is an execution
delegation, independent of the initiating login session. Taking over or closing
the tab, entering private login, revoking the delegation or reducing its owner's
permissions invalidates unattended control. Actions still follow the current
review policy; sensitive or unknown effects need exact human review.

Completed runs release their selected tabs for the next authorized run. A clean
host restart can reopen an authorized persistent-profile tab at its captured
starting URL within the same origin boundary. Private, explicitly closed,
taken-over or interrupted agent-controlled tabs are not silently restored.

Agents can read and operate other applications through their accessibility
trees (see [Desktop control](desktop-control.md)). Five tools do it:
`desktop_apps`, `desktop_snapshot`, `desktop_find`, `desktop_get` and
`desktop_act`. There are no screenshots, so any model that can call tools
works; it does not need to see images. Agent mode and a set-up, hash-verified
agent-desktop are required. Native engine capabilities differ: choosing Codex
does not automatically supply the internal engine's browser, desktop or team
tools. Bot presets, ceilings and the `computer` tool id keep their meaning: the
registry expands `computer` to the five desktop tools.

Desktop control follows the same policy rules as the other internal tools, and
the decision is made before any agent-desktop process starts. The model never
names an application: `desktop_apps` issues opaque handles, and every ref is
resolved to its application through the host's own table of delivered snapshots.
A ref the host did not deliver is refused. A capability deny for
`computer.control`, or a remembered deny for the application, stops the call. A
remembered allow binds the application's bundle ID or executable, not only its
display name, so another program that calls itself `Notes` does not inherit it
(allows written under the older name-only key ask once more).

These are asked every time, cannot be remembered, and are never approved by
**Autonomous within host policy** or Auto: credential-like text, any input to a
password-like or secure control, clicks on controls named like send, submit,
delete, pay or confirm (an English-only heuristic that is a seat belt, not a
boundary), text with a line break entered into a single-line field, input into a
control type the host does not recognise, applications that can run commands or
change security settings (terminals, System Settings, password managers), any
web browser (it may be showing TermX's own approval screen) and an
application the host cannot tell apart from another of the same name. TermX's
own interface and the application that started it cannot be controlled at all.
The approval names the action, the control and the application with its bundle
ID. Ask and Plan mode, restricted browser tasks, private windows and a user
takeover still block the desktop first.

The tools act in the background: they do not move the pointer, press keys or
bring an application forward, and they never pass `--headed`. Keyboard
shortcuts, drag, hover and coordinates do not exist. Text read from other
applications reaches the model between `SCREEN` markers with a fresh random id,
with control and direction characters removed, secrets redacted and size capped
(400 controls, 24,000 characters); it is data, never instructions. Persisted
events carry sizes and a title, never screen text or typed text.

Unattended runs cannot control the desktop unless the schedule carries a
`computer_permission` on its delegation: exact applications (up to 20), an expiry
inside the grant and the seconds of desktop control allowed per run. Without it a
scheduled run's desktop call is refused before any process starts. The grant
authorises applications and seconds only: anything that would ask a person
(credential-like text, consequential controls, command applications) is refused
instead. A remembered allow does not lift this. Revoking the grant or losing
`desktop-control` withdraws the permission at the next call. A schedule's
`computer_permission` is accepted only on a platform with a pinned agent-desktop
build.

Desktop skills ask for the `desktop-control` permission in their bundle and may
add `computer: {apps: [...]}` (up to 20 exact application names, executables or
bundle IDs). The permission is checked at every dispatch. While a list is active
(from the skill, the schedule permission, or both: every list must allow the
application) it is checked against the application each ref belongs to, before
the call. Entries are typed: a dotted entry without spaces (`com.apple.Notes`) or
an `.exe` name matches only a bundle ID or executable, and any other entry
matches only the display name. Lists match exactly, so two lists must name an
application the same way (`Notes` in both, or `com.apple.Notes` in both);
otherwise they overlap nowhere and the task is refused. A refusal emits
`desktop.app_blocked`. Desktop time for a run is kept on the run's task, survives
a restart, and ends the call that reaches it.

## Restart behavior and live verification

Schedule definitions, run reservations, task IDs and events are durable. Restart
reconciles a known task ID; it never automatically repeats an ambiguous dispatch.
Internal checkpoints can reuse committed results or resume a prepared call.
A possibly executed side effect requires explicit recovery confirmation. Native
engines follow their own supported session-resume contract.

Live acceptance harnesses run actual hosts and network clients:

- `tests/workspace_remote_control_e2e.py`: independent authenticated clients,
  disconnect/reconnect, task teams, cancellation, revocation, real Bash PTY and a CLI session steered by a
  second client over REST while the first client's subscription reports its status (fake CLI, no browser).
- `tests/workspace_autonomy_e2e.py`: daemon scheduling and process restart with
  durable state and actual harmless command effects.
- `tests/workspace_schedules_e2e.py`: rendered desktop schedule controls and
  execution while the browser is closed.
- `tests/workspace_scheduled_browser_e2e.py`: actual managed Chromium execution
  after the initiating browser closes, without a per-run handoff.
- `tests/workspace_cli_sessions_e2e.py`: a real host and sockets with a fake hook-capable
  CLI (no browser): hook-driven status, the pty websocket, the event subscription and stop.
- `tests/test_remote_scope_matrix.py`: every CLI, host-status and task-graph surface against its existing
  scope (401/403/success, managed grants, cross-user isolation); `tests/test_push_delivery.py` and
  `tests/test_push_event_sources.py`: the push kinds, their authority, limits and sources.
- `tests/workspace_pty_restart_e2e.py`: real `termx` host processes stopped with SIGTERM and killed
  with SIGKILL and replaced over the same config directory: the same shell process, the replay
  (including output produced while no host ran), a CLI session and its status hook all survive.

The harnesses explicitly identify scripted model adapters. They establish host
and tool behavior, not successful model inference, external phone connectivity,
or qualification of an installed native application on every platform. A release
claim for those capabilities needs the corresponding real provider, network and
device run.
