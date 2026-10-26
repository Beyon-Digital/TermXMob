# Abrupt in-flight host restart proof

Baseline commit `89fb066` retains the completed graceful host-restart browser
proof. This follow-up is a separate process-level scenario at
`tests/workspace_process_crash_e2e.py`. **Run `process-crash-07` passed all 5
checks in 18.30s**, including real SIGKILL, same-state restart, exactly one write
attempt, zero replay and owned-process cleanup. Evidence is preserved at
`output/playwright/out/sessions/2026-10-25/process-crash-07/report.json`.

## Fixture boundaries

The controller starts an actual Python `AppState`/Uvicorn process on a private
loopback port and signs in over real HTTP with a synthetic password identity.
Provider plan/turn responses are deterministic no-network fixtures. Both initial
and replacement processes use the same dedicated configuration/SQLite directory.
No real provider key, native/ACP vendor engine, browser or Rust command is used.

Four real child tasks/conversations are created from the main agent's actual
delegation tools and approval gates. The first child is explicitly cancelled
before admitting the remaining three, preserving the ordinary parallel-worker
limit. The other children represent a user-controlled running reader, an
ambiguous running write, and a pending private tool approval.

The ambiguous operation uses the **production `write_file` executor** to create
a synthetic file inside its actual managed scratch checkout. A fixture wrapper
fsyncs an observation journal and suspends the executor's return, leaving the
manager's real execution checkpoint at `running` and the actual DecisionBroker
receipt at `executing`, with its single-use permit already consumed. There is no long-lived shell
or native subprocess to orphan when the backend is killed. The pending private
operation is an explicit fixture-only tool; it never runs without its real
approval boundary.

A fixture-only authenticated telemetry route verifies the worker's random nonce,
PID, configuration root and process phase before the controller calls `kill()`
on **that exact `Popen` object**. It also distinguishes a live runtime worker
from a persisted status. No process-name matching, port-owner kill, process-group
kill or broad process scan is used.

Initial attempt `process-crash-01` could not bind its sandboxed loopback port.
Escalated attempt `process-crash-02` stopped before task setup because the SPA
catch-all intercepted the appended fixture inspection route (HTML 200 instead
of JSON). The startup log and failed-run report retain the evidence; started
owned processes were cleaned up. The
harness now explicitly registers inspection ahead of the catch-all, retaining
the controller secret and nonce/PID/root checks. A fresh run is still required.
Attempt `process-crash-03` reached real plan approval but its harness expected
approvals on the REST supervision-tree response. Inspection now uses the real
authorized `agent_task` GraphQL detail query, including approvals on terminal
tasks. Its failed report and successful owned cleanup remain preserved.
Attempt `process-crash-04` reached the production write and detected that the
managed DecisionBroker branch had no AgentManager execution checkpoint. It
stopped before SIGKILL. The broker had already persisted permit consumption;
the real managed execution path received the source repair before the passing run.

The follow-up contract is stronger than the original harness plan. A generic
ambiguous local checkpoint can historically request confirmation before replay.
A broker-bound action has consumed an exact, single-use permit: confirmation is
not new consent for a second action. After a crash, the same receipt must become
`invalidated` with unknown-outcome guidance, and neither ordinary nor confirmed
recovery may re-enter its executor. The proof now requires **one total executor
attempt**, an unchanged file, no new permit or writer worker, and a terminal
failed task with an inspect-then-fresh-instruction diagnostic. It no longer expects a second attempt
to reach create-exclusive file protection.

## Assertions prepared

- The first process actually exits by SIGKILL while parent/child work is active;
  its graceful shutdown path does not run.
- The new process on the same port restores authentication, the main binding,
  original child IDs, ownership and cancelled terminal state.
- The checkpoint points to the actual executing broker receipt before SIGKILL.
  The same receipt is invalidated after restart, without an executable permit.
- Startup, unconfirmed recovery and confirmed recovery do not re-enter the
  ambiguous broker-bound write. The original task becomes terminal-failed with
  the actual checkpoint/review IDs in its fresh-instruction recovery marker.
  Both recovery forms return that same failed task without starting work;
  a fresh reviewed instruction is required for a new action.
  The scratch file and one total executor attempt remain unchanged throughout.
- A private tool approval lost with the process retains its durable identity,
  is safely denied with actionable interruption feedback, and cannot be executed
  from its public/redacted payload.
- Repeating the original parent request and a fresh direct-child request returns
  their original dispatch receipts without duplicate tasks or delegates.
- Persisted `running` states always correspond to an actual live worker; final
  cancellation leaves only terminal tasks and unique report/activity IDs.

## Bounded execution

After explicit grant, run from the isolated TermX checkout with the existing
Python environment and a **new** evidence directory:

```sh
PYTHONPATH=src /Users/jainamshah/Documents/GitHub/termxmob/.venv/bin/python \
  tests/workspace_process_crash_e2e.py \
  output/playwright/out/sessions/2026-10-25/process-crash-05
```

The scenario has a 180-second deadline. Cleanup targets only its currently owned
backend, allowing seven seconds for termination and three seconds for a final
exact-process kill if necessary. Evidence includes `report.json`, per-process
backend logs, a fsynced effect journal and the preserved synthetic SQLite state.
The output directory is never reused or cleared.

This fixture does not establish live provider recovery, native vendor/ACP
resumption, native keychain behavior, guest-browser recovery or real external
side effects. Those boundaries require separate evidence. The passing graceful
restart result remains valid independently of this now-passing abrupt-crash proof.
