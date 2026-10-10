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
