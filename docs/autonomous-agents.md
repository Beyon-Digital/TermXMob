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

Computer-capable model connections expose desktop screenshots and input. Agent
mode and the host's screen/input permissions are required. Linux X11, Windows
and macOS use their platform backends; native Wayland input is not supported.
Native engine capabilities differ: choosing Codex does not automatically supply
the internal engine's browser, computer or team tools.

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
