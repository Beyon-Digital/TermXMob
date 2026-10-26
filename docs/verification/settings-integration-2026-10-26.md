# TermX settings integration verification — October 26, 2026

Status: implemented; selected frontend/backend checks and the updated production build have passed, including the browser-discovered cancellation fix. **Both integrated browser proofs passed against identical production assets.**

The isolated branch is `codex/settings-integration-20261026`, based on merged PR46 main `da03536509a9a9e7628372600d7847b33928c344`. Current implementation checkpoint: `051a933d9501e5771f2fdca9cd4c3f52e213fa9c`. Later evidence-only changes must be distinguished from runtime changes when the final result is recorded.

## Integrated behavior

This branch selectively reconciles settings behavior from the preserved local source. It does not copy the old App or Managers wholesale over PR46. Main chat remains the primary work surface; settings is secondary.

| Area | Resulting behavior and relevant boundary |
| --- | --- |
| General and settings navigation | Grouped category icons, a compact mobile category picker, direct General links for access/models, and immediate theme selection. Existing category IDs, aliases, deep links, guarded transitions, focus restoration and visual-viewport handling remain. Hostname lookup is optional, permission failures do not block General, and stale account/host responses are fenced. Mobile control targets are at least 44px. |
| Settings context | Account/host context is explicit. Project-aware categories retain their project selector and capabilities; Hooks and MCP choose execution targets within their own actions. Changing a sidebar group or settings category does not grant execution rights. |
| Host service and LAN HTTPS | Setup and diagnostics use progressive disclosure while existing service controls remain available. Service changes retain exact-action review. LAN HTTPS review identifies the configured address/names and preserves failed drafts; save errors remain readable in the active review. Settings changes do not themselves install a listener or trust root. |
| Plugins and skill sources | Inventory and locations remain inspectable. Editing is offered only for an advertised editable source root. Source scope, expected-digest conflicts, immutable publication review and read-only compatibility discovery remain. Read-only source metadata does not grant write authority. |
| Lifecycle hooks | A bounded inventory loads authorized project targets; creation reviews the exact command, folder, event and timeout, and saves disabled. Enabling requires a separate review. Current account rights, project path and hook revision/signature are rechecked before mutation. Pre-dispatch cancellation releases its pending reservation; a dispatched uncertain write is retained and is not automatically repeated. |
| MCP inspection and execution | Ordinary inspection reads saved schemas/configuration. The existing direct-call capability remains under an explicit advanced action. Each connection uses an accessible allowlisted project or an explicit administrator-only host target. Trust, enabled state, approved tool, bounded arguments and exact target are reviewed before dispatch. |
| Reviewed MCP definition | The UI sends `expected_definition_digest`. The backend checks current authority before comparing it, then repeats the comparison through the pool's final authorization callback before execution. A changed reviewed definition is rejected rather than silently using another configuration. Existing callers may omit this optional field. |
| Unconfirmed MCP actions | A tab-local receipt is written before dispatch, keyed by hashes of the account/host identity and exact reviewed call. Stored receipt fields contain state, time and attempt identity, not plaintext arguments or credentials. A failed/lost response preserves an uncertain receipt, including across panel remount/reload in that tab. Repeating the identical call requires explicit acknowledgment of a new instruction and another exact review. |
| Review cancellation | Canceling a reviewed action closes only that review and keeps the underlying draft. Actual category/editor departure remains guarded. The inline MCP call review carries the same explicit boundary. |
| Draft acknowledgments | Persisted changes acknowledge only the unchanged form snapshot that produced them. Inspection, connection checks and direct tool calls do not clear unrelated settings drafts. Failed saves and edits made during a save remain protected. |

Hook receipts are in-memory guards for the loaded application, including panel remounts; the hook API supplies no idempotency key. They do not establish exactly-once behavior across browser reloads or devices. New hook creation remains disabled until a separate enable review.

The MCP receipt is **not backend exactly-once execution**. It does not coordinate independent tabs/devices, establish whether an external effect happened, cancel an earlier call, or prevent repeated effects after a user authorizes a new instruction. After an uncertain response, the user must inspect the service or its records. Backend authority and reviewed-definition checks remain separate from this browser guard.

## Completed validation

Evidence is retained under `output/settings-validation/`. Test counts below describe this settings branch and must not be added to PR46's earlier totals.

| Check | Confirmed result | Evidence |
| --- | --- | --- |
| Selected frontend regressions | **247 passed across 24 unique files**, using the latest completed passing result for each file. This is a per-file union, not one test invocation or a sum of repeated runs. | `checkpoint.json` → `frontend_latest_passing_file_results`; `ts-integrated-final.log`, `ts-mcp-receipts.log`, `ts-chat-context.log`, `ts-review-cancel.log` |
| Selected Python settings/core boundary regressions | **106 passed**, one completed clean invocation; pytest 90.34s, supervisor 94.24s, exit 0, no timeout. | `python-settings-core-v2.log`, `python-settings-core-v2.result.json` |
| Final production TypeScript/Vite build | **Passed**, exit 0, no timeout, 142.99s, with a 768 MiB Node heap at `051a933`. | `production-build-v6.log`, `production-build-v6.result.json`, `production-build-v6.launch.json` |
| Integrated settings browser | **Passed**, 9 acceptance checks, 24 axe/layout audits with zero violations/overflow, exit 0 in 85.22s; owned browser/host stopped. | `browser-settings-05/report.json`, `browser-settings-05.result.json` |
| Core chat/orchestration browser regression | **Passed**, 15 acceptance checks, 12 axe/layout audits with zero violations/overflow; exit 0 in 170.64s, owned browser and host stopped. | `browser-core-02/report.json`, `browser-core-02.result.json` |

The frontend selection includes General/category guards, mobile picker transitions, host/TLS reviews, skill inventories, hook authority and uncertain-write handling, reviewed MCP calls/receipts, settings context, composer/draft/queue behavior, Main navigation, source decisions and budget/status controls. The Python batch includes the affected settings/API authority boundaries and selected core regressions. A passing selected batch is not a claim that the entire repository suite ran.

The final build retains the existing large-chunk warning. The Python batch reports the existing Starlette/AnyIO deprecation warning. Neither is counted as a product failure.

### Excluded attempts

- Core browser attempt 01 passed 12 checks including actual two-project work, then raced cached draft rendering against asynchronous refresh response recording. The harness now explicitly waits for HTTP 200 refresh and checks that the access cookie was actually removed. Attempt01 is partial, not a completed proof.
- Build v2 timed out under host load during bundle rendering. It is not a passing build.
- Build v3 exited 134 after exhausting its heap. It is not a passing build.
- Build v4 emitted completion output, but its executor/supervisor transport was lost and a final exit status was not confirmed. It is excluded from the passing ledger.
- Earlier failed or partial frontend fixture runs are excluded; only their completed passing replacements contribute to the per-file union.
- Settings browser attempt 01 stopped after three checks because the harness used a nested `asyncio.run`. The harness now uses HTTP login and explicitly exercises a non-default MCP project target. Attempt 01 is retained as diagnostic evidence and is not a passing browser proof.
- Settings browser attempt 04 and two focused hook diagnostics exposed a harness-only exact-label issue after a controlled textarea acquired child text. DOM ancestry had no hidden/inert state; the accessible snapshot retained the correctly named textbox and exact draft. The assertion now uses that exact textbox role/name; attempt 05 passed the full journey. Diagnostic runs are not acceptance passes.
- Settings browser attempt 02 failed before reaching the app because its 15-second CDP connection timeout elapsed under host load. Its owned browser and host were stopped. The test-only connection timeout was increased to 60 seconds with startup logging; attempt 03 connected successfully. Attempt 02 is not an acceptance pass.

- Settings browser attempt 03 passed five acceptance checks, then exposed a real shared guard defect: Cancel review dispatched the outer draft-leave prompt and could leave the action review open. Fixed at `051a933`; the harness now asserts review dismissal, retained hook/MCP draft, and zero mutation. Attempt03 is partial, not an acceptance pass.
- The affected three-file frontend rerun hit its 180-second bound after SettingsSurface 20 and MCP 32 completed passing. The Hook file did not complete in that invocation; its earlier unchanged 14-case passing result remains in the per-file ledger. The owned orphan worker was stopped and its process group verified absent. This attempt is not represented as a clean full-batch pass.

## Browser proof boundaries and record

The settings harness uses production React, actual password/JWT, HTTP/GraphQL, SQLite, skill-source APIs and a local synthetic MCP stdio service. Its inference adapter raises if called. OS service mutation requests are blocked and fail the proof if attempted. TLS configuration writes are confined to a private fixture profile; no listener, CA or service is installed.

The browser evidence records:

- Settings journey result, exact code/bundle checkpoint, acceptance checks, screenshots, accessibility/overflow results and owned-process cleanup: **Passed at runtime checkpoint `051a933` using the v6 production asset snapshot**. The real browser journey covered configured-owner password/JWT login, account form and explicit Main binding without inference, guarded memory drafts, actual skill validate/save and read-only member denial, progressive host/TLS controls with zero OS mutations, explicit disabled-hook review/cancel/save, read-only MCP inspection and one confirmed actual local stdio `add(7,5)=12` on the second allowed project, foreign-project denial, both themes at 1440/390px, modal focus and reload of the same Main/draft. All 9 checks and 24 axe/layout audits passed; no page error, external request, inference call or forbidden service mutation occurred. Both owned processes stopped.
- Core Main/chat journey after settings, draft/selection preservation, child coordination, decisions and return navigation, with explicit inference-fixture boundaries: **Passed at the same v6 asset snapshot**. Actual owner setup/configured-password login, separate chat drafts, first account binding, real approvals and child runtime, direct child takeover/reload/return, consolidated and late source results, single Stop, contextual browser, provider-test consent cancellation, two enrolled project roots with two real read-only child file reads, access-cookie loss with successful HTTP 200 refresh, and host shutdown/recreation all passed. The restart retained auth, Main, child ownership/results, all 14 unique activity IDs and the unsent draft, with no new adapter calls. Inference itself is deterministic and local; this does not qualify a live provider. The 15 acceptance checks and 12 axe/layout audits cover 1440/1024/390px in both themes.
- Final screenshot review and any remaining browser failures: **Reviewed the current settings sheet and source frames**: grouped desktop rail, ordinary General controls, explicit disabled-hook target, advanced MCP result and narrow light General remain readable without clipping in the sampled renders. Sheet: `out/sessions/settings-integration-20261026/TermX-settings-review-sheet.png` (1568×1184), private Library `libfile_722675bbdcf48191b353cf5c2ba18d07`. Frames contain synthetic account/project data and read-only host metadata, with no visible API key, token or real project content. One existing minor refinement remains: a successful settings notice can persist across categories until the next action.

`final-verification-manifest.json` records the report hashes and identical UI asset-manifest digest. Only completed passing browser runs contribute to the acceptance counts. The earlier diagnostic attempts remain separate.

## Preservation, exclusions and publication

The 101 pre-existing modified/untracked source files remain preserved in the original checkout. This integration uses a separate worktree and does not reset that checkout or publish its snapshot commit `745fc7d`. Only selected settings behavior was reconciled onto PR46; generated Tauri permission files, obsolete chat/workbench copies, diagnostic probe scripts, apparent partial-clone deletions and the already-squashed macOS migration are excluded.

The PR46 chat component, orchestration runtime, engine adapters, engine-authentication handling and billable provider-test review remain unchanged by this settings branch. Its relevant backend changes are skill-root editability metadata and reviewed MCP-definition enforcement. PR46's broader product evidence remains separately recorded in [the product verification report](product-completion-audit-2026-10-26.md); those earlier totals are not substituted for this branch's integrated proof.

No Rust or Tauri build/check/test command ran on the Mac. No vendor login/inference, real credentials, OS service installation/start/stop, certificate trust installation, real external MCP service or installed native-platform behavior is qualified by these checks. Live vendor/native testing limits from PR46 remain. No deployment or release is included.

Publication readiness: **verified for a normal settings-only PR and merge**. The exact published head and resulting merge commit are reported separately after GitHub confirms them. No protection bypass or force push is used.
