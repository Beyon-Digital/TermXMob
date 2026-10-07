# TermX desktop workspace: approved delivery plan

**Status: implementation resumed after the user's approval and switch to Code mode.** Three agents own identity/audit, execution budgeting, and policy/browser validation. Existing work is preserved. Backend stabilization precedes frontend integration; routine milestones do not require repeated approval.

The [approved v0.3 HTML](plan-v0.3.html) remains the product specification. This document turns it into an engineering and UX delivery plan. It preserves all ten parity families and the locked decisions. The user approved this plan and requested implementation. Historical design-only stage text in the HTML is superseded by that direct instruction; product requirements remain unchanged.

## 1. Product scope and experience

TermX gets one independent React DOM desktop/web workspace. Tauri and external browsers load that same workspace. Expo remains the native mobile presentation.

- The default is Chat with project-grouped conversations, pinned projects, search and Needs attention.
- Workbench rearranges the same conversation alongside editor, files, diffs, debug, preview and terminals.
- Browser, Computer and Artifacts are dockable surfaces. Review and Focus are layouts.
- Machine → Project → Session owns execution context. Moving a sidebar group or panel does not move files, change permissions or create a replacement session.
- One engine/model chip belongs to each AI composer. Managers configure installed engines, accounts and presets; they do not select a global active engine.
- Built-in browser tabs only: selected context, task-scoped handoff, visible ownership, takeover, private login and explicit resume.
- Local password, OIDC and custom identity adapters share principal/session/authorization services. Enterprise groups and organizations are explicit mappings.
- Host rules and remembered consent precede the smaller reviewer. Every action is validated against current authority before execution.
- Dedicated runners are user-owned containers/VMs. No managed TermX cloud fleet or new paid account is included.

## 2. Current baseline: reuse it, verify it, keep unfinished work separate

The published green checkpoint is `02f987be049075298ebd17da5a0e9d035b22d39a`, on [draft PR28](https://github.com/Beyon-Digital/TermXMob/pull/28). [CI evidence](verification/ci-02f987b.json):245 frontend tests,1035 Linux backend tests and1067 macOS backend tests passed. Focused Windows contracts, native platform compilation and hosted macOS GUI feasibility passed. This includes the final parent/child budget relay and compact approval dialogs; Czw/BIq rendered evidence retains its immutable asset identities. This is source qualification. The [exact-source artifact run](https://github.com/Beyon-Digital/TermXMob/actions/runs/37677911211) then found eight full-Windows-suite failures, stopping Windows packaging. Their [separate failure record](verification/windows-installer-02f987b-failures.json) and scoped repairs do not retroactively promote Windows installation. The PR macOS installed workflow exercised fixtures only.

The companion mobile changes are on [draft PR26](https://github.com/Beyon-Digital/termx-app/pull/26), with green CI. Three unrelated untracked mobile probes remain untouched.

There is substantial implementation and rendered evidence already available. It should be reviewed against the specification, not rewritten merely because it exists. The table distinguishes published implementation, rendered proofs and the remaining installed/live gates:

| Area | Current evidence boundary | Work to review before resuming |
| --- | --- | --- |
| Managed sessions, browser, coding, artifacts and layouts | Published source; multiple actual host/rendered proofs | Confirm full HTML requirements, migration and declared adapter limits |
| Queue, session controls, pins and scoped MCP | Published source; exact rendered queue/MCP flows | Preserve reading anchor, preset refresh and current-policy invalidation fixes |
| Lock/reload continuity | Actual Buw flow restored the same PTY/task and dirty file/chat/artifact drafts | Keep source/hash distinction; review newer fresh-inventory gate |
| Generic remembered approvals | Published source; actual Cfb approve→inspect→edit→revoke proof and owner isolation tests | Review alternate API routes, deny precedence and immutable scope together |
| Model favorites | Published source; mounted session/second-window and four-theme/zoom surface checks | Review preference ownership and manager placement |
| Groups/organizations, audit retention and device lifetime policy | Published backend/components; targeted transport/revocation/retention tests and actual CJW group/audit proof | Review semantics, migration, UI and full integration |
| Shared parent/child execution budget | Published coordinator integration;24 targeted coordinator/regression tests and4 component tests pass | One durable pool counts reserved tool calls and summed active-worker execution seconds. Human approval waits pause active time; crash-orphaned leases require explicit versioned renewal. Token usage and billed cost are separate and are not claimed as measured. |
| Figma | Original11 screens/66 components preserved;95 advanced states/234 verified edges | Editable contract/state coverage verified; final runtime and native gates remain separately scoped |
| Installed desktop and live providers | Unqualified | Exact-build installer tests and explicitly configured real-account validation |

The old38 acceptance rows are necessary but insufficient. [The source-table index](planning-requirement-index.json) preserves20 tables/171 rows, including decisions, contracts, journeys and tests. It does not pretend every source row is a feature. Behavioral requirements in prose must also be mapped. The older implementation ledger contains historical snapshots and must be reconciled before it becomes the final delivery record.

## 3. Complete work-package map

Every package requires a backend contract, UI states, a working declared adapter and an evidence boundary. A feature is not complete merely because a component exists or an unsupported badge is displayed.

| Package | Required scope | Backend responsibility | UX and evidence needed |
| --- | --- | --- | --- |
| WP01 — Access and continuity | Password/SSO/custom auth, principals/groups/orgs, machine/project roles, device sessions, refresh, lock/logout/recovery, migration | Verified identity mapping; current-policy authorization across API/socket/file/preview/control routes; protected secrets and audit retention | Setup/login/failure, access/group editors, revoke/expiry/lock/unlock, recovery; real transport and same-session continuity |
| WP02 — Chat and sessions | Grouped/pinned/searchable sessions, streaming/context/skills/media, session-only controls, forks, queue/interrupt/steer/history | Canonical session/native IDs; immutable run configuration; durable turn receipts/queue/context/drafts; capability-aware changes | Compact controls, context-transfer preview, retained scroll/draft, running/approval/error/reconnect states; no duplicate effects |
| WP03 — Instructions and memory | Project instructions, compatible AGENTS/skills, provenance/scope/exclusions/retention/export, hooks | Versioned memory and sources; bounded permission-checked lifecycle hooks | Inspect/edit/delete/export, revision conflicts, effective source and timeout/failure history |
| WP04 — Execution and supervision | Plans/goals/checkpoints, schedules, subagents, isolation, intervention and shared budgets | One existing execution coordinator; durable schedule/delegation leases; atomic tree budgeting and cancellation | Next-run preview, task tree, location/permission/budget source, bounded extension, cancel/steer/retry/outcomes |
| WP05 — Coding | Editor buffers/splits, terminals, problems/output/tasks, debugging and language tooling | Scoped files/PTY/LSP/debug transports; versioned saves and runtime adapters | Real breakpoints/stepping/navigation/formatting; retained dirty buffers/PTY; keyboard escape and conflict recovery |
| WP06 — Review and delivery | Worktrees, hunks/diffs, conflicts, stage/commit/push, PR/comments/checks/cleanup | Exact target and publication operations; current policy; mutation receipts and dirty protections | Reviewed operation/target, actionable checks/conflicts, cleanup that preserves unsaved work; actual Git workflow |
| WP07 — Browser and Computer | Tabs/profiles/history/downloads/devtools, annotation/compare, handoff/takeover/private mode, capture/record-to-skill | Isolated workers/profiles; network policy; scoped view/control leases; epoch/revision checks; capture exclusion | Interactive in-app surface, reachable Stop/Take over, private barrier, capture indicator, redact/parameterize/test/enable; declared platform input/permission tests |
| WP08 — Managers and extensibility | Models/accounts/favorites, presets, registries/plugins/skills, MCP/ACP distinctions, memory/hooks/access/Safety | Versioned compatible sources, permissions/provenance, install/update/rollback/removal; write-only credentials and project-scoped MCP | Search→detail/editor→permissions→activity; distinct installed/connected/authorized/enabled states; real preset-to-tool path |
| WP09 — Desktop ergonomics | Dock/resize/tab groups, layouts/focus/undo/reset, detach/re-dock, multiple windows/monitors, commands/keybindings/themes/accessibility | Window identity, durable independent drafts/buffer versions and acknowledgement-based transfer; native geometry adapter | Mouse and keyboard equivalents, off-screen recovery, 200% zoom, both themes, reduced motion; actual installed window/monitor lifecycle |
| WP10 — Creation and runners | Dictation/voice, image generation/editing, richer inputs, document/table/chart/deck/code artifacts, dedicated execution | Explicit provider capability/account contracts; versioned operations/artifacts; runner enrollment, scoped secrets/logs/results/budget/teardown | Reviewed transcript/context/billing, revise/export/recovery, active runner supervision; real supported provider and isolated runner qualification |

Cross-cutting requirements from sections3–4 and7–10 apply to every package: versioned host/UI handshake; mobile compatibility; current authority; data ownership; durable cursors; idempotency; honest capability/billing disclosure; accessible failure/recovery states.

## 4. Agreed architecture boundaries

Keep the existing Tauri/Python host, execution loop and viable CodeMirror/xterm/Relay behavior. The shared React DOM UI is a client of those contracts.

| Boundary | Decision to freeze | Review output |
| --- | --- | --- |
| Identity and authorization | Authentication → canonical principal/org/group → device session → current resource policy. Adapters cannot elevate host limits. No email-only identity merging. | Claims, mappings, membership expiry/revocation, scopes, cookie/native credential storage, migration and endpoint matrix |
| Execution admission | A run freezes account/model/preset/context/location/limits. One coordinator handles turns, queue, cancellation and children. | State machines, idempotency keys, queue invalidation/renewal, parent/child cancellation and restart behavior |
| Shared budget | A step reserves one tool call before its effect; exact task/call positions are idempotent. The time pool sums active-worker seconds across parent and children, pauses during human waits, and requires explicit renewal after crash-orphaned leases. Separate individual task ceilings remain visible. | Atomic tree admission, durable reservations, versioned renewal, cancellation and restart tests; no invented token or billed cost |
| Approval broker | Hard policy/sandbox → matching deny → scoped consent → eligible reviewer → final current-authority validation → exact effect. | One scope vocabulary, immutable target/arguments/binding, expiry/CAS/revoke, native interception matrix and audit redaction |
| Browser/Computer | Human view, agent observation, control and capture are distinct grants. Private mode suppresses observations before and after awaits. | Tab/profile isolation, network rules, leases/epochs, private/takeover/resume states and renderer/platform proof |
| Workspace continuity | Canonical execution lives on the host; per-window drafts and layout state are separate. Transfer acknowledges durable receipt before source closure. | Buffer/draft versions, conflict copies, terminal selection versus permission, detach/restore/monitor rules |
| Extensions and providers | Trusted versioned registry/manifest adapters; per-project effective sources and MCP tools; configured account/model capabilities. | Support matrix with versions, scope, permissions, credential ownership, actual adapter tests and explicit linked handoffs |
| Media/runners | Versioned uncertain operations never replay automatically. Workers receive scoped authority and bounded secret references. | Provider/runner ports, outcome reconciliation, operation/export formats, budgets and teardown |

Two limits remain explicit: shared host roles do not isolate unrestricted programs executing as the same OS user; an adapter is supported only where its interception/enforcement actually works. At least one real supported adapter is required for each capability. Unsupported native paths must refuse or offer a reviewed handoff, rather than silently ignore configuration.

## 5. UX plan and design review

Review complete journeys, including failure and recovery, instead of isolated attractive screens:

1. Password/SSO → project/session → chat → same conversation in Workbench.
2. Browser open → selected context or handoff → private login → explicit resume → action → takeover → verified result.
3. Code → debug/checks → isolated parallel worktrees → reviewed Git operation → PR/checks/conflict → cleanup.
4. Goal → schedule/target/budget preview → grant → closed-UI run → subagent supervision → intervention/outcome.
5. Record tab/window → visible capture → redact/parameterize → dry-run → enable exact skill version.
6. Dictation → review transcript/context → voice/image/artifact revision → real export.
7. Dock/detach → independent dirty edits → lock/reload/reconnect → retained PTY/task → conflict-safe re-dock/off-screen recovery.
8. Manager edit → permission/provenance diff → test/connect/enable → activity/failure → rollback/revoke.

Each journey needs its real data fields, permission source, loading/empty/disabled/error/stale/approval/recovery states, keyboard path and compact-browser fallback. Use semantic light/dark/System tokens, Geist typography and owned shadcn components. Reuse the approved original Figma design; fill actual missing states rather than multiplying generic screens.

The consolidated review should include this scope/architecture plan, the source-mapped Figma journeys and the declared adapter/platform matrix. Later routine implementation milestones are governed by agreed exit checks; they do not need repetitive step-by-step permission questions.

## 6. Approved delivery order

| Stage | Concrete output | Exit condition |
| --- | --- | --- |
| P0 — Reconcile scope and baseline | Map all source contracts/prose/journeys to WP01–10; inventory published versus candidate work; list omissions and real external prerequisites | One agreed scope ledger; no hidden expansion and no discarded mandatory family |
| P1 — Freeze contracts and UX | Resolve the architecture table; complete source-mapped Figma states/prototypes and support matrix | Consolidated plan/design review before implementation resumes |
| P2 — Backend first | Review/stabilize candidate auth groups, retention, policy and tree budget work; complete any other mapped backend omission; preserve mobile contracts/migrations | Actual route/transport/effect tests plus one green coherent source checkpoint; no unproved configuration silently accepted |
| P3 — Frontend completion | Wire approved contracts into shared Chat/Workbench/Browser/Computer/Managers/Artifacts; finish recovery and visual polish | Complete journeys against actual host; semantic keyboard checks, both themes, 200% zoom and narrow fallback; no stranded drafts or panels |
| P4 — Adapter and platform qualification | Frozen real-account suite, exact-build signed installers, native window/storage/input/permission checks and runner qualification | Every advertised adapter/platform has named evidence; unsupported paths are honest and a working adapter fulfills each capability |
| P5 — Release candidate | Reconciled evidence ledger, migration/rollback notes, exact artifact/source identity and draft PRs | Full specification/acceptance coverage with no mandatory gate presented as complete while unqualified |

No production release, updater publication, new account or paid validation is implied by this planning document. Actual provider qualification requires a configured entitled account and a bounded approved evaluation budget; test preparation can proceed without those effects.

## 7. Parallel work and verification discipline

- Assign independent identity, execution/policy, browser/native, and frontend/design packages only after their shared contracts are frozen.
- Give shared files such as AppState, AgentManager, workspace routes, GraphQL integration, Chat and Managers one integration owner. Other agents provide isolated modules/patches; concurrent mutations of those files are avoided.
- Use an immutable host/UI snapshot for each rendered proof and record its source/asset hashes. Tests must not accidentally consume another agent's half-integrated source.
- Preserve unrelated changes and the green checkpoint. Review candidate work before adopting it; do not roll back valid work automatically.
- Run targeted meaningful checks after a boundary changes. Full integration/CI runs occur at coherent checkpoints; repeat a passing proof when the relevant source changed or a new concern warrants it.
- Separate implementation, local fixture validation, CI source validation, installed-native validation and live-account validation. Each is a distinct evidence level.
- Build Rust delivery binaries in CI only. A compile test or fixture does not prove the installed package's behavior.

## 8. Definition of done

Every mandatory HTML requirement has a mapped implementation, reachable UX state and named validation. All ten parity families are delivered through real supported integrations. Reconnect, duplicate windows, stale consent and restart do not duplicate effects or lose unsaved work. Authentication/authorization/private-mode boundaries hold across alternate transports. Native packages and live providers are qualified for their advertised support.

A package with missing installed/live evidence remains pending. An unsupported badge, mock provider, structural Figma check, source compile or old green CI cannot stand in for that missing evidence.

**Current action: complete exact-source installer and installed-app qualification, then close entitled live-provider gates. The user selected each session’s current chosen provider; inspected saved sessions have no selected provider/model or numeric evaluation cap, so the clarification remains pending. Live checks must preserve that selection without billing or model fallback.**

[Rollout and recovery notes](rollout-notes.md) cover stopped-host snapshots, managed-auth migration, owner recovery prerequisites and explicit downgrade limits. They are operator instructions, not a performed restoration test.
