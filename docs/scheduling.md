# Scheduled tasks and cron jobs

The host owns the scheduler and persists definitions, delegated authority, run
budgets and history in the workspace ledger. Desktop/browser is the control
plane; the Expo companion monitors and controls the same host schedules. The
host process must remain running and awake. Closing a browser or mobile app does
not stop schedules. No operating-system crontab is installed or modified.

## Create work

In desktop **Automations → New scheduled task**, choose a conversation and either:

- **Agent task:** a prompt and success criteria. Uses that conversation's project,
  engine, model, agent preset and execution target, including an attached runner.
  Normal tool approval requirements still apply.
- **Shell cron job:** a task label, command and success criteria. Runs directly
  through the saved-command automation runner in the conversation's folder, as
  the host OS user, without a model call. Requires live terminal execution
  permission and a local conversation. The command is saved as an automation.

Choose Once, Daily, Every interval, or Cron expression, preview the occurrences,
then explicitly authorize a run count, per-run time limit and expiring grant
(1–90 days). The first scheduled occurrence must precede grant expiry. Changing
an execution target or saved command steps requires a new authorized schedule;
existing grants cannot silently authorize the new target or commands.

The mobile **Automations → Schedule a task** flow creates cron schedules from an
existing managed conversation. Choose an agent task or a saved command automation
in the same project. Mobile uses skip policies by default; use the desktop editor
for advanced timing and missed/overlap policies. Managed host sign-in with task
permissions is required; a legacy passcode alone does not grant managed schedule
access.

## Timing

Cron has five numeric fields: `minute hour day-of-month month weekday`. Supports
`*`, comma lists, inclusive ranges and steps such as `*/15`. Weekday 0 or 7 is
Sunday. If both day-of-month and weekday are restricted, either may match, as in
traditional cron. Named weekdays/months, seconds fields and macros are not
supported. Impossible expressions are rejected with a bounded eight-year search.

Examples:

| Expression | Meaning |
| --- | --- |
| `0 9 * * 1-5` | Weekdays at 09:00 |
| `*/15 * * * *` | Every 15 minutes |
| `30 2 1 * *` | First of each month at 02:30 |

Daily and cron schedules use the entered IANA timezone. Nonexistent daylight
saving times are skipped; repeated local times run once. Preview timestamps are
shown in the viewing device's timezone. One-time dates are entered in that
device's timezone and stored as an absolute instant. Intervals use elapsed
seconds, with a one-minute minimum.

Missed occurrences may be skipped or caught up once. Overlaps may be skipped or
queued as one pending occurrence, never an unbounded backlog. A queued occurrence
is not later discarded merely because the prior task took longer than a minute.

## Operate and inspect

Pause stops future automatic dispatches. Run now consumes the same delegated
budget, works while paused, and does not shift the next recurring occurrence.
Concurrent turns in the same conversation are rejected. Schedule revisions
prevent a repeated Run now request from dispatching twice. Creation uses a
stable request ID so retrying an unchanged request cannot create duplicate jobs.

Edit timing, task prompt/label and overlap/missed policies. History shows manual,
scheduled, skipped, completed, failed and approval-waiting work. Direct command
results include captured step output. Confirm gated command steps or stop a run
from history; agent approvals remain in the conversation. Archive stops future
runs while retaining history and allowing already active work to finish. Revoke
and stop runs cancels active work and removes unattended authority.

Command steps retain the existing 15-minute runner ceiling and a delegated
per-step limit (the UI grants 120 seconds). The total time budget and live
execution authority are also checked by the five-second scheduler loop, including
while a command is waiting for confirmation. Host restart reconciles known runs;
ambiguous dispatches are marked for attention instead of replayed.

## Validation

- `tests/test_scheduled_tasks.py`: cron semantics, DST, invalid inputs, one-time
  execution, queueing, creation idempotency, revision conflicts, permissions,
  direct shell execution/output, changed-command rejection, step confirmation,
  cancellation and time limits.
- Existing durable-workspace tests cover restart, budgets, live authority and
  agent approval waits. Existing runbook tests cover the shared command runner.
- `tests/workspace_schedules_e2e.py`: built standalone desktop UI against a real
  disposable host. Exercises cron preview/create, pause/manual execution, reload,
  editing to Once, daemon execution after the browser leaves, archive/history,
  and a real harmless shell command. Agent responses are deterministic fixtures.
- `termx-app/e2e/scheduled-tasks.spec.ts`: compact mobile browser flow against a
  protocol fixture for create, pause, Run now and history. Native device behavior
  is not established by that browser check.
