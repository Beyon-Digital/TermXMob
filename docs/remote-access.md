# Remote access matrix

What a signed-in phone, tablet or second computer can reach on the host, and which
**existing** scope gates each surface. CLI sessions, durable terminals, host health and
the task-graph executors added no scope: the scope list is the same 15 values it was
before, so a device paired earlier keeps exactly the authority it had. Everything below is
asserted by `tests/test_remote_scope_matrix.py` (401 without credentials, 403 for a
credential holding every scope except the required one, success for a credential holding
only the required scope, plus cross-principal checks).

Credentials are the usual `Authorization: Bearer`, `X-Termx-Passcode` or session cookie.
Two kinds exist: a **managed principal** (a signed-in user with a role, project grants and
private records) and a **device token or the compatibility passcode**, which has no user
identity and acts as the single host owner. A managed principal must also hold the scope on
the project for project-scoped surfaces; a device token is checked against its scopes only.

| Surface | Method / operation | Scope | Also checked for a managed principal |
| --- | --- | --- | --- |
| Installed CLIs | `GET /api/cli-agents` | `terminal-view` | none (host fact) |
| List CLI sessions | `GET /api/cli-sessions` | `terminal-view` | only the caller's own sessions whose terminal the caller owns |
| Read one session | `GET /api/cli-sessions/{id}` | `terminal-view` | the record is the caller's (404 otherwise) and so is its terminal |
| Start a session | `POST /api/cli-sessions` | `terminal-control` | `terminal-control` on the project; worktree isolation also `git-read` and `git-write` |
| Type into a session | `POST /api/cli-sessions/{id}/input` | `terminal-control` | own session and terminal |
| Interrupt / stop | `POST /api/cli-sessions/{id}/interrupt`, `/stop` | `terminal-control` | own session and terminal |
| Resume a lost session | `POST /api/cli-sessions/{id}/resume` | `terminal-control` | own session, project grant `terminal-control`; a bypass-mode session also needs a sandbox and a human confirmation |
| CLI hook | `POST /api/cli-sessions/hook/{token}` | none: a per-session secret URL, loopback only | not applicable; it can only move that one session's status |
| Stream the TUI | `WS /api/sessions/{pty_session_id}/pty` (existing) | `terminal-control` | own terminal |
| CLI session events | subscription `cli_session_events` | `terminal-view` (rechecked every poll) | own sessions only |
| Host health | query `host_status`, subscription `host_status_events` | `machine-view` | managed users additionally need `host-admin` (it is an administrator view) |
| Durability settings | mutation `update_host_settings` | `host-admin` | `host-admin` role |
| Task graph board | query `orchestrator_board` (now with `executor`, `blocked_reason`, `session_id`, `resumable`) | `agent-view` | managed sign-in; only the caller's own main agent and nodes |
| Start / retry / reassign / cancel nodes | mutations `start_ready_nodes`, `retry_node`, `reassign_node`, `cancel_node` | `agent-control` (starting also `agent-run`) | managed sign-in; own nodes only (404 otherwise). Starting or resuming a CLI node also needs `terminal-control`, and the project's `git-read`/`git-write` for a worktree |
| Review a node's changes | model tool `review_node_changes` | main agent only; needs `agent-view` and `git-read` on the project | own node; read-only, no apply path |
| Push registration | mutations `register_push_device`, `unregister_push_device` | `agent-view` | a device token belongs to one principal; another user cannot take it over or remove it |

Notes:

* A device token holding `terminal-view` could already read terminals, and the CLI routes use
  the same scope, so it can list sessions with no change. Starting and steering are the
  same `terminal-control` it needed for any terminal. Tokens created with the old `terminal`
  scope were migrated to `terminal-view`/`terminal-control` (and file and Git scopes) earlier.
* Devices hold no `host-admin` unless it was granted explicitly, so a paired phone can read
  `host_status` (`machine-view`) but cannot change durability settings.
* The task graph acts as the signed-in user, so a device token that holds the scope but
  has no user identity is answered `401 Managed sign-in required` (as for the other
  workspace operations).
* Per-user records are CLI sessions (owner is the principal; the host owner is separate),
  task-graph nodes (owned by the main agent's owner), push registrations, and every
  resource the existing ownership table already claims. A principal never sees another's.

## Push notifications

Push is opt-in: only a device that registered a token (with `agent-view`) is ever sent
anything, and the authority is rechecked when each push is queued and again when it is sent.
There is no quiet-hours or per-kind preference setting yet; the controls are registration,
authority, the per-resource interval and a per-device cap.

| Kind | Fires when | Recipients | Title / body (host-written, never prompt text or terminal output) | `data` |
| --- | --- | --- | --- | --- |
| `task.completed`, `task.failed`, `approval.requested` | A task event (unchanged) | Devices that may view the task | `Task completed`, `Task needs attention`, `Approval needed` / `Open TermX to review this task.` | `{host, taskId, eventId}` |
| `cli.session.attention` | A CLI session's `attention` flips true (waiting for input or a permission decision) | The session owner's devices with `terminal-view` on its terminal | `CLI session is waiting for you` or `CLI session needs approval` / `Claude Code is waiting for input.` | `{host, kind, resourceId (terminal id), eventId}` |
| `orchestrator.report` | The main agent's task graph has no unsettled node | The owner's devices with `agent-view` on the main conversation | `Main agent finished its plan` / counts, for example `2 completed, 1 failed.` | `{host, kind, resourceId (main conversation), eventId}` |
| `host.recovered` | Host start re-adopted terminals or CLI sessions, found some lost, or has tasks recovering | Administrator devices (`host-admin`) and the compatibility passcode | `TermX restarted and recovered work` / counts, for example `1 terminal kept; 1 CLI session lost.` | `{host, kind, resourceId: "host", eventId}` |

A CLI session's permission prompt is not a task approval, but the main agent answering it
(`cli_session_send` while the session is `needs_approval`) is an always-ask delegation. It
raises the normal `approval.requested` task event, so the existing `Approval needed` push
covers it. The Expo request is `{to, title, body, sound, channelId, data}` for every kind.

Limits: a resource gets at most one push per kind per interval (60 s for CLI attention, 30 s
for reports, 300 s for recovery), a device at most 20 pushes an hour of the three new kinds,
and a repeated event key never queues twice. Task pushes keep their existing behaviour. A
client that only understands `taskId` must ignore a push whose `data` has `kind`.
