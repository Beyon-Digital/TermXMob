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

## Web, memory and usage tools

These Internal-engine tools run in the host process, so they work with every
provider and follow host policy.

| Tool | What it does | Approval |
| --- | --- | --- |
| `web_fetch {url, max_chars}` | Reads one http(s) page as text (HTML is reduced to its visible text). | Per host, remembered like other rules. |
| `web_search {query, limit}` | Returns titles, URLs and snippets from the configured search API. | Per backend host; the approval shows the query. |
| `memory_read {query, limit}` | Ranks saved notes by relevance. | None. |
| `usage_query {mode, kind, query, since, limit}` | Answers what the owner uses most (`top`), most recently (`recent`) or what matches some words (`search`). | None; read-only. |

**Web tools.** Both need Agent mode (they are not offered in Ask mode, because a
URL or query can carry data out) and ask before each new host. The approval names
the capability `net.outbound:<host>` and remembers the host, so `docs.example.com`
being allowed does not allow another host or another port. Bots deny
`net.outbound:any` by default, which also stops these tools until the owner
raises the ceiling. The sandbox profile does not limit them, and approving one
never gives a sandboxed shell network access. Autonomous approval mode and Auto do
not answer for the owner; only a remembered host rule does.

Every request resolves the host once through the browser's `NetworkPolicy` and
connects to that exact address, with TLS still verified against the name, so DNS
rebinding cannot swap in a private address. Redirects are followed by hand and
checked again at each hop (five at most). Private, loopback, link-local, multicast,
reserved and cloud-metadata destinations (including IPv4 addresses wrapped in
IPv6), non-http(s) schemes, credentials in URLs, HTTPS-to-HTTP redirects, binary
content and bodies over 2 MiB are refused. Proxy environment variables are
ignored. The reply is marked `untrusted`, redacted and capped at 50,000 characters
(20,000 by default); it is data, never instructions.

`web_search` is hidden until a backend is configured. One backend is implemented,
the Brave Search API. Store its key as the `web-search` credential or set
`TERMX_AI_WEB_SEARCH_API_KEY` on the host. `TERMX_WEB_SEARCH_URL` points it at
another endpoint (for example a compatible self-hosted service; that exact origin
is then allowed even if it is private) and `TERMX_WEB_SEARCH_PROVIDER` selects the
backend (`brave`). A new backend is a small class with `request()` and `parse()`
registered in `BACKENDS` in `agent/tools/web.py`.

**Memory recall.** `memory_read` ranks with SQLite FTS5 (bm25, word stems and
prefixes) when a query is given, and falls back to the old substring match when
the index is unavailable or finds nothing. The index only orders notes the agent
could already read, so the agent, project and user scopes, excluded notes and
unreviewed proposals behave as before. Databases from before the index are
backfilled when they are opened.

**Usage history.** `workspace/usage.py` keeps redacted events (`usage_events`,
with an FTS index over the subject) for the owner: a finished task records its
conversation title (or the first 80 characters of its redacted prompt) with
counters, never the prompt, result or tool arguments. Events older than 180 days
are dropped. Other features add events with `UsageHistory.record(owner, kind,
subject, detail=..., project_id=...)`. `usage_query` sees the owner's events with
no project plus those of the task's project.

## Restart behavior and live verification

Schedule definitions, run reservations, task IDs and events are durable. Restart
reconciles a known task ID; it never automatically repeats an ambiguous dispatch.
Internal checkpoints can reuse committed results or resume a prepared call.
A possibly executed side effect requires explicit recovery confirmation. Native
engines follow their own supported session-resume contract.

Live acceptance harnesses run actual hosts and network clients:

- `tests/workspace_remote_control_e2e.py`: independent authenticated clients,
  disconnect/reconnect, task teams, cancellation, revocation and real Bash PTY.
- `tests/workspace_autonomy_e2e.py`: daemon scheduling and process restart with
  durable state and actual harmless command effects.
- `tests/workspace_schedules_e2e.py`: rendered desktop schedule controls and
  execution while the browser is closed.
- `tests/workspace_scheduled_browser_e2e.py`: actual managed Chromium execution
  after the initiating browser closes, without a per-run handoff.

The harnesses explicitly identify scripted model adapters. They establish host
and tool behavior, not successful model inference, external phone connectivity,
or qualification of an installed native application on every platform. A release
claim for those capabilities needs the corresponding real provider, network and
device run.
