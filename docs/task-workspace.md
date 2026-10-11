# Task workspace support

## Product boundary

`desktop/workspace` is the standalone control plane used by both the native Tauri
app and browser access to the host. `termx-app` is the Expo remote controller.
They share backend authority and task semantics, not screen layouts or frontend
bundles. An Expo web export is not the desktop/browser control plane.

| Desktop and browser control plane | Expo remote controller |
| --- | --- |
| Configure projects, agents, models, runners, schedules and access | Connect to a host and monitor its work |
| Supervise work across accessible projects | Receive alerts and open the exact task |
| Inspect and intervene through full execution workspaces | Approve, steer, stop and perform focused review |
| Dock editors, terminals, browser, computer and artifacts | Use touch-oriented controls and native transitions |
| Review changes, manage checkouts, validate and deliver | Review results and confirm supported delivery actions remotely |

## Desktop entry and supervision

The root opens the control-plane overview. Existing session and detached-window
links still open execution workspaces directly. Overview, Projects and Runs are
persistent navigation destinations. Run counts describe the latest run per
unarchived conversation; full history and child-agent supervision remain in the
execution workspace. Archived sidebar filters do not change the control-plane
catalog.

The overview provides an attention queue, project portfolio and links into the
existing operational managers. Runs has project/status/search filters and a
desktop inspection panel with exact-session links to approvals, files/terminal,
changes/delivery and browser/preview. Project cards open the correct workbench
or preselect the project for a new task.

Switching to the control plane keeps execution panels mounted and makes them
inert, preserving drafts and editor state while preventing hidden controls from
receiving focus. Control-plane reads pause while locked or the tab is hidden;
failed reads explicitly retain and label stale data rather than displaying a
healthy empty queue.

## Shared host support

The Expo companion uses two additional read APIs: `agent_task_page` for keyset
pagination/search and `agent_task_tree` for bounded child-task supervision and
shared budgets. Every returned task is checked against current resource authority.
Pagination cursors are encrypted, expire after one hour, and bind to the request
credential and filters. Restarting the host invalidates cursors; refresh the task
list to restart pagination.

Opt-in push registrations and pending delivery jobs live in private
`push.sqlite3` beside host configuration. Managed registrations bind to a live
session; legacy registrations require the administrator passcode. Authority is
checked when enqueuing and sending. The worker sends generic task alerts through
Expo's HTTPS push endpoint, retries transient failures with bounded backoff,
checks receipts, removes invalid tokens, and deduplicates replayed events for one
day. No prompt, result, or authentication secret is included in a push payload.

The client must supply an Expo token from a configured native build. Unit tests
use a simulated transport; live APNs/FCM delivery requires device validation.

Source builds are separate: build `desktop/workspace` for the bundled desktop UI;
build the companion Expo repository for mobile and its web export. Do not replace
the standalone desktop bundle with an Expo export.

## Viewer, pairing and runner audit

Computer capture closes its socket, releases input and discards frames when the
panel hides, the window loses focus, the workspace locks, or the component
unmounts. Returning requires an explicit Watch action. Browser observation also
closes its socket while inactive. Foreground browser and runner polling is
serialized; execution polling pauses under the control plane and managers.

Pair device · QR opens the managed pairing form. It renders a local QR image for
a scoped, five-minute, one-use HTTPS invitation. A fresh browser removes the
ticket from its URL before boot and exchanges it only after an explicit Connect
action. Browser credentials use HttpOnly cookies; the existing native bearer
exchange remains available. Pairing does not replace an already signed-in browser
session. Remote HTTPS must be configured; loopback is the development exception.

Global runner enrollment includes project selection. Session options load the
host's runner array directly, making eligible runners available for attachment.
Persistent SSH machines and EC2/Lightsail lifecycle controls are documented in
[Runner machines](runner-machines.md). Their configured roots and SSH permissions
are shown separately from leased, isolated containers.
Refresh and request errors are visible; requests have a 30-second deadline and
mutation timeout messages warn that the operation may already have completed.

Validation performed:

- Desktop: 255 unit tests, TypeScript and production build pass; four creation
  tests rerun after the final stale-response guard.
- Host: 12 managed-pairing tests, two capture-authority tests, two real Docker
  runner tests and 16 agent-runner tests pass.
- `tests/workspace_managed_pairing_e2e.py` exercises the rendered desktop UI and
  real host over pinned TLS: QR generation, fresh-browser cookie sign-in, bearer
  exchange, scope enforcement, refresh rotation, replay rejection and revocation.
- `tests/workspace_runner_e2e.py` exercises enrollment, upload, execution,
  artifact download, conversation attachment, an agent turn, reload, detach and
  teardown against real Docker. It also verifies that actual host viewer
  registrations disappear when the desktop panel hides or window blurs.

Both browser scripts create isolated host state. Build the desktop workspace
before running them; the runner script also requires the
`termx-runner:workspace` image. Set `TERMX_CHROMIUM_EXECUTABLE` when using a system
Chromium. The runner Dockerfile accepts an optional BuildKit `proxy_ca` secret
for package installation behind a trusted corporate proxy; it is not copied
into the resulting image.

The agent provider is a deterministic fixture and physical screen capture is
stubbed at the operating-system boundary. These checks do not establish native
Tauri bridge behavior, physical-device capture, installed mobile secure storage,
or live provider billing behavior.

Scheduled agent tasks and direct shell cron jobs are implemented in the host and
standalone control plane, with remote controls in the companion. See
[scheduling.md](scheduling.md) for supported expressions, authorization, command
limits, lifecycle behavior and validation.
