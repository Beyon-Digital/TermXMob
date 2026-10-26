# TermX chat-first implementation and verification

Status: checkpoint `89fb066` implemented and verified. Its frozen production bundle passed the complete rendered main-agent journey and graceful host restart proof. The later native/ACP and abrupt-crash extension has [separate verification](orchestration-extension-2026-10-25.md); this checkpoint's passing results do not establish that extension.

## Source separation

- Actual desktop/browser product: `termxmob/desktop/workspace`; `termx-app` is the Expo companion.
- Original main: `fb8ae4b`. The 101 existing modified/untracked source files are preserved in isolated snapshot `745fc7d`.
- Work branch: `codex/chat-first-orchestrator` in `Documents/Codex/2026-10-25/task-6/termx`.
- The original tracked working diff matches the pre-task binary diff byte for byte. Other worktrees and unrelated services were not edited or stopped.
- This implementation changes no Rust paths relative to `745fc7d`. No Rust command, GitHub CI, push, merge or deployment was used. There is currently no implementation-specific Rust patch requiring cloud validation.

## Interaction and architecture

Main agent is the default home and a persistent owner-scoped conversation. Focused chats remain immediately accessible; recent activity is the default arrangement, with optional project grouping. Code, browser, terminals, changes, computer and artifacts remain contextual surfaces. Drafts and mounted panels survive navigation. New chat exposes optional folder/preset settings progressively.

The coordinator uses the actual existing Internal Agent runtime, bounded task trees, shared budgets and approval gates. Real child conversations are enrolled with their task ownership and isolated workspace identity. Explicit authorized project delegation accepts enrolled project IDs instead of arbitrary folders. Direct user messages/control transfer prevent simultaneous coordinator steering/cancellation. Existing native/ACP chats retain their identity and remain accessible as focused chats.

Children report progress, decisions, results and errors through a durable feed. Active main turns receive these updates; idle main chats receive clearly labeled source reports without inference. Source authority is checked again when resolving reports. See [main-agent architecture](../main-agent.md).

## Findings, fixes and evidence

| Finding | Implemented fix | Verification |
| --- | --- | --- |
| Root opened operations/projects instead of chat | Persistent Main agent home; secondary operations/code; optional grouping | App navigation regressions; production browser proof |
| Chat creation foregrounded folder/preset configuration | Optional disclosure with useful defaults | Rendered focused-chat creation and draft-switching proof |
| Fresh signed-out browser displayed invalid CSRF error | Do not attempt session refresh without its required marker; retain CSRF protection | API/auth tests; fresh first-owner browser setup |
| Missing provider looked send-ready | Connection guidance and direct setup path preserving draft | Composer/model tests; rendered empty-state proof |
| Failed send lost request identity after remount | Persist exact request payload/key across draft cache, window journal and handoff | Retry/remount and recovery tests |
| Pending Stop could dispatch repeatedly | Shared in-flight guard and visible progress | Cross-panel Stop test; actual runtime browser cancellation |
| Queue action could retry a different pending send | Disable queue while unresolved submission identity is present | Chat journey regression |
| Maximum images duplicated beyond handoff cap | Strict duplicate-image wire reference; bounded validation/reconstruction | Four maximum-image roundtrip and malformed-marker regressions; unchanged 16MB cap |
| Provider test silently invokes billable inference | Concrete destination/model/message/cost review before dispatch | Manager tests; browser review canceled with zero test calls |
| New focused chat briefly retained the previous chat composer | Bind the newly returned Session synchronously with its selection | Two actual App/composer dispatch-target regressions, deferred navigation regression; rendered confirmation pending |
| An older autosave acknowledgement could leave a newer edit on a stale baseline | Advance only the acknowledged baseline while preserving the newer unsent draft | Three gated ACK/external-content/revision409 regressions passed |
| Existing primary unnecessarily required control authority to reopen | Reopen under existing view authority; creation still requires control | Real authorization boundary regression |
| Late primary response overrides newer navigation | Single-flight ensure plus owner/navigation fences, root-history/unlock handling | Five deferred App integration tests |
| Child spawn replay could repeat work | Durable call digest/receipt and stable child identity | Actual manager replay/cancelled-child tests |
| Coordinator/legacy cancel could bypass direct takeover | Shared ownership checks at actual command/approval/cancel boundary | Ownership, legacy cancel and automatic-approval regressions |
| Cancel during follow-up preparation could launch work | Recheck parent status before/after hooks and on child creation | Gated actual runtime regression |
| Late child approval resurrected terminal parent | Preserve terminal parent; show child decision/source report | Late approval/report tests |
| Partial enrollment commits did not repair | Deterministic child conversation identity and idempotent repair of links/turns/claims | Simulated interrupted-commit repair regression |
| Revoked project authority trapped queue forever | Block only denied row; retain transient-host semantics and advance independent conversations | Queue 403/404/503 regressions |
| Native/ACP session startup could send after cancellation | Recheck durable state after create/configure and before send; close late binding | Fake adapter gated lifecycle regressions |
| Send failure could replace confirmed cancellation | Preserve terminal cancellation after late transport error, without replay/duplicate cleanup | Fake adapter send gate regression |
| Late native completion could replace a terminal outcome | Preserve completed/failed/cancelled outcome and first result; retain raw event for inspection | Fifteen gateway lifecycle cases including duplicate/late completion |
| Source report could mask a failed main task as completed | Separate execution status from report delivery in transcript, collection and inspector projections | Status projection and visible failure-review regressions |
| Vite compatibility route returned app fallback | Proxy workspace contract endpoint to selected scratch host | Source fix; isolated preview verification recorded separately |

## Completed local checks

These suites overlap. Their counts are reported separately and must not be summed as a unique total.

- Final-source production TypeScript/Vite build: passed, 11.25s. Log `output/playwright/verification/production-build-final-source.log`.
- Broader frontend sweep before the last navigation fixes: **165 passed across 20 files**, one worker, 38.70s. Log `output/playwright/verification/frontend-regressions.log`.
- Final navigation/autosave/real-composer/docking checks: **61 passed across 8 files**, 26.03s. Includes new-chat send targeting with delayed history, stale-response switching, both ACK and true revision conflicts. Log `output/playwright/verification/navigation-autosave-final.log`.
- Final report-state/control-plane checks: **19 passed across 3 files**, 8.25s. Log `output/playwright/verification/report-status-regressions.log`.
- Final orchestration suite: **14 passed**, 6.75s, actual Python runtime/stores and deterministic inference. Log `output/playwright/verification/orchestrator-status-final.log`.
- Final engine lifecycle suite: **15 passed** within the 28-case combined run; fake adapters only, including startup/configure/send cancellation and terminal/duplicate event protection. Log `output/playwright/verification/orchestration-lifecycle-final-recheck.log`.
- Earlier unchanged queue/approval/cancellation/team group: **35 cases passed** (34 plus the repaired additive-event contract case). [Architecture and details](../main-agent.md).
- Whitespace diff checks passed. One dependency Starlette/AnyIO deprecation warning remains; no runtime failure.

Earlier redundant compilers were stopped during severe Mac load. Prior timed-out UI cases passed unchanged after serialization. Environment contention is not counted as a product defect.

## Browser evidence

**Final: passed.** `output/playwright/out/sessions/2026-10-25/final-frozen-01/report.json` records 13 completed checks, 9 axe WCAG audits with 0 violations/overflow, 21 screenshots, 0 page exceptions, 0 external browser requests and exactly one successful Stop POST. Nine deterministic inference fixture turns ran; provider-test requests and live provider calls were both 0.

The actual host/runtime verified first-owner setup, fresh configured-password login without false CSRF feedback, one persistent main conversation, rapid focused-chat creation and separate drafts, two real approval gates before spawning, actual runtime child creation, inspect/takeover/reload/direct follow-up/return/consolidation, late inline child reporting without inference, cancellation, contextual Browser opening with draft preservation, provider-test disclosure cancellation, real cookie refresh 200, and same-port shutdown/recreation preserving authentication, primary binding, two child results/ownership, eight deduplicated activity IDs and draft with 0 new inference calls.

The 21 frames and six-frame `review-sheet.jpg` are preserved beside `report.json` and `evidence-summary.json`. The review sheet is 1560x1548. All 77 production bundle files match the tested snapshot SHA256 manifest. The owned browser was terminated gracefully and the fixture host stopped. Artifact job elapsed 50.7s.

Before: `output/playwright/out/sessions/2026-10-25/baseline-201400`, immutable original bundle, actual synthetic host/auth/session APIs. [Detailed browser audit](chat-first-browser-audit-2026-10-25.md). Earlier incomplete attempts are preserved; their existence is not counted as a pass. They exposed the stale-chat composer fault subsequently fixed and verified.

All session screenshot artifacts total approximately 6.3 MiB. No generated frame was deleted.

## Isolated review preview

The checkpoint bundle was served at `http://127.0.0.1:8797` using the existing Python runtime and task-local `runtime-state`. Its verified workspace contract is 3 and it remains unconfigured: no owner account or model credential was created for this persistent preview. First-run host passcode: `termx-ui-synthetic` (a disposable local fixture value). It has no tunnel and engine startup discovery is disabled. Owned PID 2770 / exec 4484 was stopped cleanly with exit 0 during the extension validation pause; the listener is absent. A matched final frontend/backend preview will be started after extension verification. The earlier Vite 5197 and old scratch daemon were stopped explicitly; unrelated apps/services were not stopped.

## Limits

At this checkpoint automatic delegation to native/ACP adapters is not implemented. These engines remain independently usable; Main agent clearly uses Internal Agent. The later extension connects their existing adapters and has separate tests. Live provider results, native credential stores, installed Tauri behavior, physical-device access and other operating systems are untested. No provider key was reused from LedgerMind/Nyay and no paid call was made. Native/reference UI inspection was unavailable through the current supported CUA inventory; official references inform interaction requirements without a parity claim.


Additional limits: managed guest-browser website navigation was not exercised; the Browser panel/draft boundary was. Host restart was graceful with completed/cancelled workers; an abrupt in-flight process crash is not established by that browser check. The existing generic cancelled-task error caption still calls its metadata reason an error, although the actual Cancelled status and single cancellation action are correct; this low-priority copy issue remains. Current main-agent summaries are bounded to 100 child chats, prioritize active/attention work, and disclose truncation; other conversations remain inspectable in history.
