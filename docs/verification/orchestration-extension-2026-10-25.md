# TermX durable orchestration: final extension verification

Status: implemented and verified locally. This extension follows the separately verified chat-first checkpoint `89fb066`. Original user changes remain isolated at `745fc7d`; no push, merge, deployment, GitHub CI or Rust command was run.

## What now works

Main agent remains the single persistent owner-scoped Internal Agent conversation. It can delegate approved work through the existing Codex, Claude and configured ACP adapters, inspect real child status/results, and manage follow-ups and cancellation within current authority. Existing native IDs, conversation history, folders and direct access remain intact.

The new tools list actual engine capabilities, create a bounded native child, and review/enroll an existing conversation. Enrollment requires an exact revision/digest and leaves user ownership in place. Settings also exposes a direct reviewed enrollment flow without a model call. An idle enrolled conversation has no fabricated task. Direct user intervention prevents competing coordinator instructions. Returning control supervises the actual current task, including a later user-created turn, rather than its original parent.

Native dispatch binds durable ancestry before startup, records exact command receipts, rechecks authority after asynchronous preparation, and refuses uncertain replay. Host admission and time supervision are bounded. Native permission requests remain owned by their adapter, are shown as attention states, and require the actual source decision. Native token use and monetary cost are not measured or capped by TermX.

## Findings, fixes and evidence

The final suites below overlap; their counts must not be added as a unique total.

| Verified defect | Implemented fix | Final evidence |
| --- | --- | --- |
| Main could not delegate through existing native/ACP adapters | Reviewed engine tools use WorkspaceService and the actual EngineGateway; persistent native identity and parent binding | Native group 104; rendered native journey |
| Existing independent chats could not join main updates | Exact-target enrollment through tools or Settings; no task dispatch or implicit ownership transfer | Native/UI groups; rendered idle adoption |
| Delayed setup could send after cancellation, revocation or takeover | Live dispatch and owned-cleanup guards before/after adapter preparation | Native group 104 |
| Old task cancellation could affect a later direct native turn | Supervision follows current task ancestry; takeover ends coordinator lease and return restarts it | Native group 104 |
| Native permissions appeared as running, or stale acknowledgments cleared attention | All pending permissions set attention; only current acknowledged final request restores running | Native group 104; rendered source approval |
| Lost approval delivery could leave an unanswerable active request | Persist decision once, expose expired/uncertain delivery, stop only the owned attempt; no retransmission | Native group 104 |
| Late old-turn messages/completion could mutate a newer native task | Durable actual-turn ownership, captured host dispatch generations, state fences and bounded private quarantine until real start/RPC proof | Native group 104 |
| Unknown/overflowing native events could contaminate results or stall forever | Unmatched content omitted from the current ledger; bounded overflow fails safely without replay | Native group 104 |
| Automatic child events/results outlived project access | Current-source projection covers retained events, wrappers, structured runtime outputs, exports and active/replayed subscriptions | Permission/legacy group 61 |
| Legacy status/approval paths could bypass revoked child rights | Current child authority checked before returning cached results or consuming decisions | Permission/legacy group 61 |
| Scheduled main execution could inherit unrelated coordination scope | Reject before context/history/provider dispatch; specific target conversations remain schedulable | Permission/legacy group 61 |
| Restart skipped active tasks older than 500 recent rows | Stable paging of the complete active ledger | Recovery group 45 |
| Cancellation or lost private approvals could resume incorrectly | Cancellation wins; original lost approval IDs are denied with fresh-instruction guidance; exact wrappers settle child-first | Recovery group 45; actual process proof |
| Managed reviewed writes bypassed the execution checkpoint | Link prepared/running/saved-result checkpoint to the real consumed broker receipt; persist no permit | Recovery group 45; actual process proof |
| An interrupted reviewed effect had no usable recovery explanation | Fail actionably and require a fresh reviewed instruction; even confirmation cannot replay the consumed permit | Recovery group 45; one-write process proof |
| Saved-result recovery could bypass or ignore restart budget consent | Reuse the actual saved result once, then honor the real explicit budget gate before further inference | Recovery group 45 |
| Confirmed cancellation used red generic-error presentation | Neutral stopped status, exact reason and partial output; true failures retain alerts | Frontend group 48; desktop/narrow screenshots |

## Final local validation

| Check | Result | Evidence |
| --- | --- | --- |
| Permission, public projection, GraphQL, workspace/legacy coordination and cancellation | **61 passed**, 25.71s | `output/playwright/verification/final-permission-legacy.log` |
| Native lifecycle, turn payloads/generations, orchestration, Claude and attachments | **104 passed**, 13.61s | `output/playwright/verification/final-native-adapters.log` |
| Actual managed broker checkpoints, action review, crash recovery and tree budgets | **45 passed**, 8.89s | `output/playwright/verification/final-managed-recovery-v2.log` |
| Changed caption, enrollment, coordinator and settings components | **48 passed in 5 files**, one worker, 10.21s | `output/playwright/verification/extension-ui.log` |
| Production TypeScript/Vite bundle | Passed; Vite phase 10.41s | `output/playwright/verification/extension-build.log` |
| Actual abrupt process crash | **5 checks passed**, 18.30s; exact owned SIGKILL, same-state restart, one write and zero replay | `output/playwright/out/sessions/2026-10-25/process-crash-07/report.json` |
| Final rendered native delegation, direct control, adoption, Stop and main restoration | **7 checks, 6 clean accessibility audits, 10 screenshots**, 25.32s; zero external requests, live provider calls or page errors | `output/playwright/out/sessions/2026-10-25/native-browser-03/report.json` |

The native suite uses real Gateway/runtime/storage code, normalized fake adapters and actual local fake ACP/Codex protocol fixtures. It makes no vendor inference calls. The runtime group exercises real human approval, the actual broker and production filesystem tools; only provider inference is scripted. A Starlette/AnyIO dependency deprecation and the existing Vite large-chunk warning remain.

The passing native browser journey is recorded separately in [native browser evidence](native-browser-proof-2026-10-25.md). It uses the production React bundle, actual authentication, Internal Agent tool loop, Gateway and ledgers, with a no-network engine fixture. The original `89fb066` rendered Internal Agent proof remains independently passed.

## Actual SIGKILL and recovery

The controller verified the nonce, PID and dedicated state root before killing only its exact owned `Popen` backend. The kill occurred while a real approved `write_file` had created the synthetic file and its actual execution checkpoint and consumed broker receipt were both running. The original process exited by SIGKILL without graceful shutdown.

A replacement process reused the same configuration, SQLite data and port. Authentication, Main binding, four original child links, direct-user ownership and cancellation survived. The consumed permit was invalidated. Startup and repeated recovery requests with both `confirm:false` and `confirm:true` left the exact original task failed with explicit fresh-instruction guidance, no permit and no worker. The write journal records **one attempt and one successful creation**. The file hash remained `368968b921d8080e6acc73807c86fe6087d6eccfa0615ca4fcd97d0e413c3361`.

The original private pending approval retained its ID and became denied. A fresh direct instruction used its own stable receipt; repeating it returned the same task. Final cancellation left no live workers and unique activity IDs. Both owned processes were stopped. Live provider calls were zero.

## Preservation and limits

All 101 original modified/untracked source files were compared with baseline `745fc7d`: zero mismatches. The original tracked binary diff is unchanged. The implementation changes zero Rust paths; Rust validation was not run on the Mac and no implementation-specific Rust patch requires cloud validation.

The main coordinator itself remains Internal Agent. Real vendor sign-in, billable inference, native credential stores, installed Tauri behavior, guest-browser website navigation and physical/other-platform steps remain untested. No LedgerMind credential was reused.

ACP unsolicited session-only streaming chunks contain no protocol turn ID. Captured host generations protect prompt completion/error/cancel and stale driver cleanup; those session-only chunks cannot reliably identify an older turn after reuse. This protocol boundary is not claimed fixed. Adapter filtering and native token/cost limits remain explicitly disclosed.

Failed harness attempts remain preserved. They exposed route-ordering, unsupported approval-read assumptions, an existing-owner fixture guard and a child-plan approval race; none is counted as a pass. One actual production checkpoint gap was fixed before the final proof. No real security approval was bypassed and no uncertain effect was replayed to make a test pass.
