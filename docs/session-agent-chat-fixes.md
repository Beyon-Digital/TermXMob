# Session recovery, agent lifecycle, and chat redesign

This change addresses password sessions unexpectedly returning to sign-in, native agent turns becoming disconnected or appearing stuck, and the dense configuration controls in the conversation composer. It targets the shared TermXMob browser/Tauri workspace, based on `ecd0193a51083c9322fa9aa75c0ecfc6adfa0c68`. It does not change the separate Expo client repository.

## Password sessions and GraphQL

The access cookie is short lived. Once the browser removed it, a protected GraphQL request could reach field resolution without credentials and return HTTP 200 with an `UNAUTHENTICATED` field error, including failures on `WorkspaceTurnNode`. The client refreshed on transport challenges but did not handle that GraphQL result. Native refresh also treated recoverable server/keyring failures as reasons to discard credentials.

The managed session guard now challenges a missing GraphQL credential before execution. The client retains GraphQL error codes and supports one refresh/retry for a read operation that reports an authentication error. It does not replay a partially executed mutation. Refresh requests use the normal bounded transport and shared refresh coordination.

Only a confirmed authentication rejection clears the client session. Forbidden requests, throttling, temporary server failures, network timeouts, and an unavailable OS credential store remain recoverable errors. Native error classification recognizes the actual session errors instead of matching any message containing “expired” or “revoked.” A stale cursor or engine ticket therefore cannot sign out the workspace. Locked sessions remain a distinct HTTP 423 state.

SQLite-backed Relay turn/node projections run through the existing asynchronous resolver wrapper. They recheck the live principal and resource authorization after reading, so moving those reads off the event loop does not weaken access control.

## Agent lifecycle

| Confirmed cause | Changed behavior |
| --- | --- |
| ACP stderr was piped without a consumer. A verbose child could fill that pipe and stop making progress. | Drain stderr throughout the child lifetime, retaining a bounded local diagnostic tail. Raw stderr is not published in chat events. |
| ACP/Codex liveness checked the OS process even after its protocol reader closed. | Reader termination makes the connection unavailable. ACP preserves the lost binding, settles the turn, cancels callbacks, and promptly cleans up owned processes/terminals. |
| A terminal turn or late approval expiry could overwrite lost/terminal state. | Terminal tasks stay terminal, old approval expiry cannot release a newer approval, and lost native sessions stay eligible for explicit native-session reattachment. |
| Claude's initially persisted session ID was a `pending_*` placeholder. | Persist the actual initialization ID in the session ledger and task. A failure before initialization does not resume a nonexistent placeholder. |
| Claude EOF without a terminal result looked like success; partial/final message handling could duplicate output. | Require a terminal result, stream supported text deltas, deduplicate the subsequent complete message, and retain partial output on failure. |
| Early internal-provider SSE EOF was not classified as a retryable transport interruption. | Use the existing bounded retry only before visible output. Never automatically replay a prompt after output or possible effects. |
| Provider client eviction closed a connection after 300 seconds even if it was still streaming. | Active requests and response streams hold a lease. Retirement waits for the grace period and stream completion; shutdown still closes clients after workers stop. |
| Whole-payload truncation removed ACP tool IDs/status and Codex item IDs/types. Large completion-only Codex messages also lost their text. | Keep the routing/completion envelope, redact and bound rich fields independently, and split long assistant text into complete ordered chunks. Deduplicate final snapshots by item ID. |

These changes do not automatically resubmit failed native prompts. A subsequent explicit user turn can load the durable native session when supported. Existing task recovery that can repeat an unfinished action retains its explicit review step.

The suspected ACP 64 KiB frame limit was investigated and disproved: the pinned SDK already assembles chunked NDJSON frames. There is no speculative line-limit change.

## Chat interaction

The conversation uses a floating composer with image attachment, an Ask/Agent mode picker, a model picker, and a round send/stop control. The agent/engine selector and session options are in the conversation header. Enter sends a new turn or queues a follow-up while a task is active; Shift+Enter inserts a newline. Draft and queued-request identities are preserved across refreshes and retries.

Scroll autosave and immediate selector changes previously could read the same session revision and conflict with each other. A shared per-conversation write queue serializes those local revision reads and mutations, including drafts, effort changes, and dispatch. Different conversations remain independent. Conflicts from other windows or rejected writes are still surfaced instead of automatically replaying mutations. Authentication fences cancel stale queued/prepared work across signout or locking.

Reasoning effort is a discrete slider built from the selected engine/model's advertised levels. “Default” remains an explicit choice. Changes fetch the current session revision and are fenced to the initiating selection; moving between models or sessions cannot apply an old capability response to the new selection. Unsupported engines do not receive invented effort settings. Provider account selection uses the actual credential reference.

Session options group Agent, Tools, Runtime, and Voice controls into a dialog/sheet. Common execution choices use named profiles:

| Profile | Tool-step ceiling | Execution seconds | Shell timeout | Parallel agents | Total agents |
| --- | ---: | ---: | ---: | ---: | ---: |
| Quick | 64 | 1,800 | 120 | 1 | 3 |
| Balanced | 256 | 14,400 | 120 | 3 | 8 |
| Extended | 1,024 | 28,800 | 180 | 4 | 16 |

Balanced matches the existing backend defaults. Existing custom ceilings remain intact until the user selects a profile. Execution settings remain fixed during a run. Tools, execution location, and voice settings remain available in the session dialog instead of stacked disclosure rows above the composer.

## Messages, tools, and delegated agents

Assistant messages use Streamdown for GFM headings, lists, tables, and code blocks, with contained scrolling and copy actions. Code scroll regions and tables are keyboard reachable. User text remains literal. The response loader reflects actual queued, running, approval, recovery, failure, and cancellation states; reduced-motion preferences disable decorative animation.

One activity disclosure summarizes tools and delegated workers. Tool rows correlate real call IDs across internal, ACP, Claude, and Codex events. Parallel output stays with its originating call. A finished parent task does not imply that every tool succeeded; missing results display “No result reported.” Native child text is isolated from the parent answer. Internal child task IDs open their actual task supervision, while native-only ancestry links open the parent task instead of inventing a task ID.

Completed Relay projections intentionally omit their event history. Expanding a saved activity now opts into `GET /api/workspace/tasks/{id}?activity=true`. The ordinary task-tree response stays lightweight. The activity response contains the latest 300 events per authorized task with a 1,000-event tree budget; `activity_truncated` discloses omitted history. Existing 100-node and depth-eight bounds remain. The endpoint checks the current session and every returned task again before returning prompts or tool output.

The UI loads this history only on expansion, cancels an in-flight read when hidden or switched, and invalidates saved history if recovery resumes the same task ID. It labels truncated histories and allows locally collapsed tool lists to reveal earlier actions. Authoritative child snapshots take precedence over older forwarded events. Virtualized conversations retain a bounded number of mounted message rows.

## Design references

The following ChatGPT screens were inspected through Mobbin MCP:

- [Compact mobile composer, model, and mode controls](https://mobbin.com/screens/7c70cf97-e978-4684-9299-0e4f2fc72eae)
- [Alternate mobile composer state](https://mobbin.com/screens/bcd32d5b-87a4-46ff-9936-aa7489b675ab)
- [In-message progress and activity details](https://mobbin.com/screens/b045e2cd-4f54-424a-97f6-4c5954f0c1e1)
- [Assistant response and activity presentation](https://mobbin.com/screens/73833b79-1dd5-4354-8fc4-a2e99c33a75e)
- [Readable response formatting](https://mobbin.com/screens/0729631b-f0f3-423c-8eff-5903dc2d0ca9)

These references informed hierarchy, spacing, progressive disclosure, and composer placement. The retrieved screens did not expose a complete nested native-agent session example. TermX's nested-agent UI is built from its actual event and task relationships, rather than claiming an exact implementation of an unseen ChatGPT screen.

## Verification

Regression tests cover authentication challenges, bounded refresh and mutation non-replay, transient native credential-store failures, live authorization on Relay/task reads, ACP subprocess failure, native session persistence, Claude stream/tool events, stale approvals, internal pre-output retry, and pooled active-stream retirement.

`tests/workspace_chat_recovery_e2e.py` starts an isolated real daemon with temporary password identity, stores, projects, and conversations. It seeds representative saved/live task states and an explicitly offline capability catalogue. Login, GraphQL reads, task history, session updates, and access-cookie refresh use the real handlers. The fixture blocks external requests and does not dispatch inference. It captures desktop/mobile, light/dark, short-viewport, Markdown, activity, settings, effort, and loader states, and runs bounded axe checks.

The exact results and retained screenshots are recorded in `docs/evidence/session-agent-chat/verification.md`. Browser and synthetic adapter tests do not qualify an installed Tauri build, live vendor service, or physical device. The deployment/installed-app boundary is recorded there explicitly.
