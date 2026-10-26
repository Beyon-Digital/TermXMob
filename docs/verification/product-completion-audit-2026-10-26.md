# TermX product completion audit — publication paused

Status: corrective source changes implemented; integrated validation pending. Do not merge or treat the October 25 proofs as validation of this combined tree.

## Repository and publication boundary

The original `termxmob` checkout and all 101 recorded local edits are untouched. Their bytes still match snapshot `745fc7d`, and the original tracked binary diff has the same SHA256 (`a3e3280508ed25de17158a35a3ac8ba84486c1b27ff2ef0446659266be8d23fc`).

A separate local publication checkout, `termx-publication`, starts at GitHub main `ced08d0ebf60d0e3f1a0d5b685ef0154eb6b7f01`. Only the task commits were carried across as `73f6fc7` and `cdf38cd`. Conflicts were reconciled in App and provider settings, retaining main's project settings scope, removed General component, editor reveal behavior, hardened change review and platform fixes. Snapshot `745fc7d` is not an ancestor of this publication branch. Neither original task commit was rewritten.

No branch has been pushed, no PR created and no merge attempted. Main had no protection or active rules at inspection. Standard `[skip ci]` markers avoid unnecessary Actions minutes; workflows and protections are unchanged. No release, deployment, Rust command, live provider request or new real credential is authorized or performed.

## Requirements and concrete gaps

| Journey | Evidence before this correction | Confirmed gap and corrective source change | Required next evidence |
| --- | --- | --- | --- |
| One persistent main chat | Earlier real host/browser proved one owner binding, direct child access and restoration using scripted inference | Current main's workspace changes were not in that proof; reconciled task changes on current main | Rebuild and rerun navigation + real browser journeys on this exact tree |
| First owner → usable main | Password/JWT and draft restoration were tested; browser fixture then injected provider/session setup directly | Saving the first account left existing main unconfigured. Added explicit account/model connection in chat, draft and ID preserved; unconfigured Main send/Enter/queue stops before a failed receipt | Updated browser proof now creates account through actual Settings UI and clicks Use for this chat; run it |
| Simple agent/model controls | Core mode/model/effort and request identity tests existed | Account switching chose no explicit default model. It now uses a configured model; missing model/engine has actionable guidance | Component and composer regressions plus rendered narrow layout |
| Source approvals in main | Earlier native browser proof opened child and took control solely to approve | Added bounded source decision cards in Main, outside collapsed work list, reusing actual approval/budget dialogs and exact source IDs; no ownership handoff or model auto-grant | Eleven new regression cases and updated actual host/browser main-only approval path |
| Proactive results/blockers | Late child result after main finished was tested | A terminal result arriving while main was active was never eligible for durable transcript delivery. Added durable deferred delivery, oldest-first bounded cursor, restart repair, exact pending-request checks and actual redacted failure reason | Eight new cases: terminal states, restart/dedup, 125-event backlog, resolved requests and revocation |
| Working native/ACP sign-in controls | Adapters and source approvals were tested with fake protocols, not vendor sign-in | UI requested arbitrary method IDs even for Codex/Claude without sign-in RPC. Now only advertised installed ACP methods are offered, with exact IDs, permission gate, failure handling and CLI guidance | Four new settings cases; actual vendor account sign-in remains external and untested |
| Chat context defaults | Chat creation before delayed history was tested | Generic New chat could retain hidden project/folder choices from a cancelled creation. It now clears those choices; explicit project task entry retains its target | Added navigation/creation regression; integrated multi-project walkthrough still required |
| Multiple projects from main | Source supports authorized project listing and explicit project child spawning; earlier regression tests one granted/denied target | A rendered two-project delegation and consolidated return is not yet proven | Add/run a real-host UI journey with two synthetic projects, current authority, independent children, consolidated outcomes and direct child intervention |
| Cancellation/restart/permissions | Earlier 61 permission, 104 native, 45 recovery groups and SIGKILL one-write/zero-replay proof passed on October 25 tree; counts overlap | Current source changes touch deferred reports and main decisions, so those passes cannot be promoted to current-tree success | Rerun affected source rights, report delivery, approval, cancellation and restart checks |
| Plugins/skills, contextual code/browser | Prior audits and focused tests cover existing managers and contextual browser preservation | Complete integrated walkthrough on current main remains pending; native Tauri/credential stores were never exercised | Verify supported web paths with synthetic data and report platform-only limits separately |

No automatic background inference loop has been added. Immediate source-labelled reports and real decision cards work without a model request; a currently running main can use its existing turn to consolidate. Idle natural-language synthesis must not be claimed tested.

## Current verification state

`git diff --check` passes. Four changed Python files were parsed successfully with Python's AST parser. These are static checks only.

One publication backend rerun was started under a requested low-priority wrapper. macOS printed `nice: setpriority: Operation not permitted` but still launched pytest. It was interrupted after the audit changed its source and the host remained severely overloaded. Log: `output/publication-validation/backend.log`; exit 2, 36 cases passed before KeyboardInterrupt after 379.95 seconds. This is **not** a completed validation batch and is excluded from current completion evidence.

Only exact owned pytest PID 83298 was sent SIGINT after its command and working directory were verified; exit 2 was confirmed. No PartyGames process was stopped, suspended or reprioritized. The user expressly declined stopping those processes. Current heavy checks are paused. Load readings exceeded 800/700/460; observed unrelated work included three PartyGames TypeScript checks and one PartyGames Jest process. Memory was not exhausted. Do not treat timeout/load artifacts as verified product defects.

## Resume gate before publication

1. When the host has headroom, freeze source and run one serialized backend group including `test_workspace_orchestrator`, permission/projection, native lifecycle and recovery suites; preserve new failure output.
2. Run the changed frontend files with one Vitest worker, including connection setup, composer, engine sign-in, source decisions, general/budget approval, main creation/navigation and current main's surface/settings regressions. Resolve actual failures without weakening assertions.
3. Build the production workspace once. Run updated `workspace_chat_first_e2e.py` and `workspace_native_orchestrator_e2e.py` serially; then the multi-project walkthrough and affected crash/restart proof. Inspect new rendered screenshots. Inference remains explicitly no-network scripted; auth/storage/tools/approvals/runtime are real.
4. Recheck original edit preservation and fresh remote main. Only after completion and current-tree evidence, push the task-only publication branch, create/reuse and attach its PR, respect protection/check requirements, merge with expected head SHA and verify remote main. Do not deploy.

## Explicit limits

Live vendor sign-in/inference, subscription entitlement, native credential stores, installed Tauri behavior, physical/other-platform steps and guest website browsing remain untested. ACP unsolicited session-only chunks have no turn ID; captured host generations do not solve cross-turn attribution of that protocol content. Native tokens/cost are unmeasured and uncapped by TermX. No local Rust validation is permitted; this task changes zero Rust paths. No LedgerMind credential may be reused.
