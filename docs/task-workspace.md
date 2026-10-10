# Task workspace support

The desktop task dashboard groups conversations by live status and links back to
their existing workspaces. Search, status filters, project context, and review
shortcuts reuse the session and delivery systems.

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
