# TermX desktop runtime design handoff

Inventory `TERMX-DESKTOP-RUNTIME-DESIGN-2026-10-07` maps all seven section 12 design families of the [approved v0.3 HTML](plan-v0.3.html#figma) to actual routes, fields, records, permissions, states and connected journeys.

The [original manifest](design-manifest.json) preserves eleven core screen IDs, sixty-six components and their first-pass review status. The advanced extension is a separately verified external Figma design: **95 editable connected states, four compact layouts, four shared components and 234 stored navigation edges**. All states are reachable from entry; original 63 prototype edges remain intact. [Actual node/prototype verification](verification/advanced-figma.json) establishes design provenance; runtime fixtures and installed native/provider qualification establish their own independent scope.

The [machine-readable coverage](design-runtime-coverage.json) and [schema](design-runtime-coverage-schema.json) map stable `DF-*`, `UI-*` and `J-*` identifiers. This inventory complements [acceptance](acceptance.json) and the [support matrix](support-matrix.md). Credentials remain host/OS owned; no external click analytics are invented.

## DF-01 · Foundations and component map

### UI-TOKENS · Semantic appearance and reusable controls

Coverage: **runtime-connected-with-limits**. Original Figma screens: DX-01, DX-02, DX-07, DX-09

**Entry points:** Authenticated workspace controls and saved preferences in the mapped source; no independent HTTP endpoint.

**Records:** per-principal saved theme preference; system prefers-color-scheme.

**Fields:** Dark/Light/System; semantic color CSS variables; button variants; dialog accessible names; Geist UI font; Bundled Geist Mono Variable code/terminal font.

**Permission contract:** Appearance requires an authenticated workspace; theme does not grant tool authority.

**States:** hover; focus-visible; disabled; reduced motion; OS appearance change; 200% zoom; narrow layout.

**Code:** [styles.css](../../desktop/workspace/src/styles.css), [main.tsx](../../desktop/workspace/src/main.tsx), [button.tsx](../../desktop/workspace/src/components/ui/button.tsx), [input.tsx](../../desktop/workspace/src/components/ui/input.tsx), [dialog.tsx](../../desktop/workspace/src/components/ui/dialog.tsx), [message.tsx](../../desktop/workspace/src/components/ai-elements/message.tsx), [tool.tsx](../../desktop/workspace/src/components/ai-elements/tool.tsx), [prompt-input.tsx](../../desktop/workspace/src/components/ai-elements/prompt-input.tsx), [theme.ts](../../desktop/workspace/src/lib/theme.ts), [AppearancePicker.tsx](../../desktop/workspace/src/components/AppearancePicker.tsx), [TerminalPanel.tsx](../../desktop/workspace/src/features/TerminalPanel.tsx).

**Evidence:** [accessibility.json](verification/accessibility.json), [media-workspace-rendered.json](verification/media-workspace-rendered.json), [theme.test.tsx](../../desktop/workspace/src/lib/theme.test.tsx), [terminal-theme.test.tsx](../../desktop/workspace/src/features/terminal-theme.test.tsx), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-ENTRY-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-77), [J-DETACH-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1265).

**Connected journeys:** J-ENTRY, J-DETACH. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

**Limits:** Bundled Geist Mono Variable is used for editor, preformatted output and terminals; installed-font/theme instance-preservation checks are recorded separately from Figma provenance. Original glyph components may use semantic runtime equivalents, not recreated Figma instances.

### UI-COMMANDS · Searchable commands and keyboard alternatives

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** Authenticated workspace controls and saved preferences in the mapped source; no independent HTTP endpoint.

**Records:** principal-scoped keybinding JSON; layout history.

**Fields:** search terms; command/view/settings matches; shortcuts; conflict and browser-reserved warnings; visible menu equivalents.

**Permission contract:** Commands rearrange UI only; each destination/action retains its own host scope.

**States:** empty search; no match; keyboard selected result; remapped shortcut; protected PTY/remote input; browser-reserved chord.

**Code:** [WorkspaceCommands.tsx](../../desktop/workspace/src/components/WorkspaceCommands.tsx), [Keybindings.tsx](../../desktop/workspace/src/features/Keybindings.tsx), [keybindings.ts](../../desktop/workspace/src/lib/keybindings.ts), [App.tsx](../../desktop/workspace/src/App.tsx).

**Evidence:** [workspace-commands.test.tsx](../../desktop/workspace/src/components/workspace-commands.test.tsx), [keybindings.test.ts](../../desktop/workspace/src/lib/keybindings.test.ts), [quick-file-workspace.json](verification/quick-file-workspace.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-ENTRY-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-77), [J-CODE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-412), [J-DETACH-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1149).

**Connected journeys:** J-CODE, J-DETACH. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

## DF-02 · Entry and identity

### UI-ENTRY · Setup, password and dynamic SSO entry

Coverage: **runtime-connected**. Original Figma screens: DX-07

**Entry points:** GET /auth/methods; POST /auth/setup; POST /auth/login; POST /auth/oidc/{method}/begin; GET /auth/oidc/{method}/callback; POST /auth/refresh; POST /auth/logout; GET/POST /auth/lock-state; POST /auth/lock; POST /auth/unlock; POST /auth/oidc/{method}/unlock-begin; GET /auth/host/lifecycle; POST /auth/host/stop.

**Records:** Principal; managed session; rotating refresh family; identity adapter configuration; UI/host compatibility version.

**Fields:** username; password; bootstrap credential when required; enabled login method; OIDC callback state; actionable version error; Current managed session lock; configured unlock method/evidence; current host ID, shutdown effects and deliberate acknowledgement.

**Permission contract:** Unconfigured protected hosts fail closed; setup requires live bootstrap/local trust; native refresh secret stays in OS storage; browser refresh stays HttpOnly; Locked session requires fresh same-canonical-account verification; Browser/Computer access remains paused until explicit resume; Managed host shutdown requires live host-admin, exact host ID, acknowledged effects and a supported launcher; no host fallback.

**States:** unconfigured setup; password invalid/rate-limited; SSO available; callback rejection/replay; expired/revoked; offline; incompatible bundle; locking/locked; wrong-account or disabled-method unlock refused; preserved drafts and paused observation after reload; unsupported shutdown; cancellation; accepted shutdown/restart.

**Code:** [App.tsx](../../desktop/workspace/src/App.tsx), [api.ts](../../desktop/workspace/src/lib/api.ts), [native.ts](../../desktop/workspace/src/lib/native.ts), [compatibility.ts](../../desktop/workspace/src/lib/compatibility.ts), [LockScreen.tsx](../../desktop/workspace/src/components/LockScreen.tsx), [HostStop.tsx](../../desktop/workspace/src/components/HostStop.tsx), [identity_locks.py](../../src/termx/identity_locks.py).

**Evidence:** [test_identity.py](../../tests/test_identity.py), [test_identity_guard.py](../../tests/test_identity_guard.py), [native-private-storage.json](verification/native-private-storage.json), [advanced-figma.json](verification/advanced-figma.json), [test_session_lifecycle.py](../../tests/test_session_lifecycle.py).

**Editable advanced states:** [J-ENTRY-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-3), [J-ENTRY-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-19), [J-ENTRY-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11660), [J-ENTRY-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11702), [J-ENTRY-13](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-12151), [J-ENTRY-15](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11733).

**Connected journeys:** J-ENTRY. Local execution/security records: [identity.py](../../src/termx/identity.py), [identity_http.py](../../src/termx/identity_http.py) Per-click analytics are not claimed.

### UI-ACCESS · Sessions, roles, adapters and trusted recovery

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET /auth/access; GET /auth/sessions; DELETE /auth/sessions/{session_id}; GET /auth/admin/principals; PUT /auth/admin/principals/{principal_id}/role; PUT /auth/admin/principals/{principal_id}/projects/{project_id}; POST /auth/admin/adapters/configuration/test; PUT /auth/admin/adapters/configuration; POST /auth/admin/recovery-code; POST /auth/recovery; GET/POST /auth/lock-state; POST /auth/lock; POST /auth/unlock; POST /auth/oidc/{method}/unlock-begin; GET /auth/host/lifecycle; POST /auth/host/stop; POST /auth/pair/issue; POST /auth/pair/exchange; POST /auth/pair/revoke; GET /auth/admin/groups; POST /auth/admin/organizations; POST /auth/admin/groups; PUT /auth/admin/groups/{group_id}/members; PUT /auth/admin/groups/{group_id}/mappings; PUT /auth/admin/groups/{group_id}/projects/{project_id}; GET/PUT /auth/admin/audit/retention; POST /auth/admin/audit/prune; GET/PUT /auth/admin/session-policy.

**Records:** Principal scopes; host role; project/resource grants; device/session; adapter health/migration; session lock/authentication generation; sponsor-bound device ticket and immutable granted scopes.

**Fields:** display name; role; trusted execution; grant expiry/scopes; session expiry/device; adapter type/options; recovery code; Current managed session lock; configured unlock method/evidence; current host ID, shutdown effects and deliberate acknowledgement; Device grant scopes, five-minute ticket expiry, verified machine HTTPS address; explicit legacy device association consent; write-only token evidence; Organization/group revision; role, trusted execution, member source/expiry and exact enrolled-project scopes; Pinned verified issuer and allowlisted external group claim; Audit max_days/max_records/include_notable plus deliberate permanent-removal acknowledgement; Session idle600–2592000 seconds; absolute>=idle and <=31536000; exact policy revision; read-only access300 seconds.

**Permission contract:** Own session visibility/revocation; host-admin for principal/adapter administration; live ownership+project scopes before effects; no hostile tenant isolation for trusted shared OS execution; Locked session requires fresh same-canonical-account verification; Browser/Computer access remains paused until explicit resume; Managed host shutdown requires live host-admin, exact host ID, acknowledged effects and a supported launcher; no host fallback; One-use device grant intersects sponsor scopes and live project/resource ownership; no account scope widening; legacy association requires explicit administrator consent; Host-admin only group, audit and session-policy mutations; exact CAS, current SID/account and last-admin constraints remain authoritative; Session lifetime reductions tighten issued sessions immediately; increases affect only new login/pairing; expired sessions never resurrect.

**States:** own/foreign sessions; disabled principal; reduced scopes; adapter test pass/fail; migration overlap/end; recovery denial; locking/locked; wrong-account or disabled-method unlock refused; preserved drafts and paused observation after reload; unsupported shutdown; cancellation; accepted shutdown/restart; issued/exchanged/revoked/expired ticket; current sponsor disabled; Dirty group draft and current revision conflict; Removed manual/provider membership invalidates authority; Audit result actual deleted/remaining counts; late identity response ignored; Session lifetime stale revision preserves draft; explicit reviewed current policy; successful reduction may expire own SID.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [LockScreen.tsx](../../desktop/workspace/src/components/LockScreen.tsx), [HostStop.tsx](../../desktop/workspace/src/components/HostStop.tsx), [identity_locks.py](../../src/termx/identity_locks.py), [ManagedPairingForm.tsx](../../desktop/workspace/src/features/ManagedPairingForm.tsx), [identity_pairing.py](../../src/termx/identity_pairing.py), [IdentityGroupsManager.tsx](../../desktop/workspace/src/features/IdentityGroupsManager.tsx), [AuditRetentionManager.tsx](../../desktop/workspace/src/features/AuditRetentionManager.tsx), [SessionPolicyManager.tsx](../../desktop/workspace/src/features/SessionPolicyManager.tsx), [identity_groups.py](../../src/termx/identity_groups.py), [session_policy.py](../../src/termx/session_policy.py), [audit.py](../../src/termx/audit.py), [authorization_http.py](../../src/termx/authorization_http.py).

**Evidence:** [test_authorization.py](../../tests/test_authorization.py), [test_identity_guard.py](../../tests/test_identity_guard.py), [test_identity.py](../../tests/test_identity.py), [access-contract.md](access-contract.md), [advanced-figma.json](verification/advanced-figma.json), [test_session_lifecycle.py](../../tests/test_session_lifecycle.py), [test_managed_pairing.py](../../tests/test_managed_pairing.py), [managed-mobile-pairing.json](verification/managed-mobile-pairing.json), [test_identity_groups.py](../../tests/test_identity_groups.py), [test_audit_retention.py](../../tests/test_audit_retention.py), [test_session_policy.py](../../tests/test_session_policy.py), [identity-access-managers.test.tsx](../../desktop/workspace/src/features/identity-access-managers.test.tsx), [identity-access-rendered-CJW.json](verification/identity-access-rendered-CJW.json).

**Editable advanced states:** [J-ENTRY-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-19), [J-ENTRY-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-35), [J-ENTRY-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-56), [J-ENTRY-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-147), [J-ENTRY-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11660), [J-ENTRY-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11702), [J-ENTRY-11](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11762), [J-ENTRY-12](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11955), [J-ENTRY-13](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-12151), [J-ENTRY-14](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-12348), [J-ENTRY-15](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=46-11733), [J-DETACH-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1286), [J-ENTRY-24](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-17136), [J-ENTRY-25](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-17332), [J-ENTRY-26](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-17528), [J-ENTRY-27](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-17724), [J-ENTRY-28](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-17920), [J-ENTRY-30](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=68-16270), [J-ENTRY-31](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=68-16466).

**Connected journeys:** J-ENTRY. Local execution/security records: [authorization_http.py](../../src/termx/authorization_http.py) Per-click analytics are not claimed.

**Limits:** CJW actual Access proof qualifies organizations/groups, verified custom-issuer mappings, stale draft and audit prune before the SessionPolicy editor was added. SessionPolicy and shared-tree UI render qualification are independent pending checks; source/coordinator regressions do not claim installed or paid-provider behavior.

## DF-03 · Chats and supervision

### UI-CHAT · Grouped sessions and session-only configuration

Coverage: **runtime-connected**. Original Figma screens: DX-01, DX-09

**Entry points:** POST /api/workspace/sessions; PATCH /api/workspace/sessions/{identifier}; POST /api/workspace/sessions/{identifier}/turns; GraphQL workspace_sessions/workspace_turns cursor connections; GET/POST /api/workspace/sessions/{id}/queue; GET /api/workspace/queue/{id}/review; POST /api/workspace/queue/{id}/renew; DELETE /api/workspace/queue/{id}; GET/PUT /api/workspace/project-pins.

**Records:** canonical Conversation/Task/Turn; native engine session ID; owned workspace metadata; request digest receipt.

**Fields:** title; project/group; pinned/archive; engine/provider/model/mode; browser workflow; runner/credential reference; run limits; draft text/context/images; scroll; custom_agent_id; custom_agent_revision; dictation/voice transcript review; immutable queued prompt/context/image snapshot; origin managed session consent/expiry; model-specific reasoning_config; compact composer model/account chip; pinned project order; canonical message reading anchor/within-row offset; opaque applicable consent/reviewer account revision for queued target review.

**Permission contract:** agent-view/control on owned conversation plus live project grant; agent-run with host-derived execution target; provider credentials remain backend-only.

**States:** empty/loading/error; needs attention/running; streaming; approval/budget wait; cancel/reconnect; edited draft during dispatch; lost-response exact-payload retry; linked session; queue waiting/blocked/dispatching/dispatched/cancelled; changed-target explicit renewal; ambiguous dispatch restart inspection; separate Queue next / Interrupt / Steer; Jump to latest after old-message reading.

**Code:** [App.tsx](../../desktop/workspace/src/App.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [workspace-data.ts](../../desktop/workspace/src/lib/workspace-data.ts), [drafts.ts](../../desktop/workspace/src/lib/drafts.ts), [PresetPicker.tsx](../../desktop/workspace/src/features/PresetPicker.tsx), [VoiceControls.tsx](../../desktop/workspace/src/features/VoiceControls.tsx), [PromptQueue.tsx](../../desktop/workspace/src/features/PromptQueue.tsx), [SessionModelControls.tsx](../../desktop/workspace/src/features/SessionModelControls.tsx), [ReasoningPicker.tsx](../../desktop/workspace/src/features/ReasoningPicker.tsx), [SessionAccess.tsx](../../desktop/workspace/src/features/SessionAccess.tsx), [conversation-position.ts](../../desktop/workspace/src/lib/conversation-position.ts), [prompt_queue.py](../../src/termx/workspace/prompt_queue.py), [project_pins.py](../../src/termx/workspace/project_pins.py), [model-favorites.ts](../../desktop/workspace/src/lib/model-favorites.ts).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [test_workspace_relay.py](../../tests/test_workspace_relay.py), [chat-drafts.test.tsx](../../desktop/workspace/src/features/chat-drafts.test.tsx), [combined-load.json](verification/combined-load.json), [test_workspace_presets.py](../../tests/test_workspace_presets.py), [preset-selection.test.tsx](../../desktop/workspace/src/features/preset-selection.test.tsx), [voice-controls.test.tsx](../../desktop/workspace/src/features/voice-controls.test.tsx), [advanced-figma.json](verification/advanced-figma.json), [voice-workspace-rendered.json](verification/voice-workspace-rendered.json), [session-presets-rendered.json](verification/session-presets-rendered.json), [test_prompt_queue.py](../../tests/test_prompt_queue.py), [workspace_prompt_queue_e2e.py](../../tests/workspace_prompt_queue_e2e.py), [test_reasoning.py](../../tests/test_reasoning.py), [prompt-queue.test.tsx](../../desktop/workspace/src/features/prompt-queue.test.tsx), [conversation-position.test.ts](../../desktop/workspace/src/lib/conversation-position.test.ts), [session-model-controls.test.tsx](../../desktop/workspace/src/features/session-model-controls.test.tsx), [reasoning-picker.test.tsx](../../desktop/workspace/src/features/reasoning-picker.test.tsx), [prompt-queue-rendered.json](verification/prompt-queue-rendered.json).

**Editable advanced states:** [J-ENTRY-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-98), [J-ENTRY-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-126), [J-ENTRY-16](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-14167), [J-ENTRY-17](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-14403), [J-ENTRY-18](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-14635), [J-ENTRY-19](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-14871), [J-ENTRY-20](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-15107), [J-BROWSER-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-168), [J-BROWSER-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-318), [J-CREATE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-967), [J-CREATE-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1014), [J-CREATE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1107), [J-DETACH-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1173), [J-DETACH-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1220).

**Connected journeys:** J-ENTRY, J-BROWSER, J-CODE, J-DETACH. Local execution/security records: [service.py](../../src/termx/workspace/service.py), [store.py](../../src/termx/agent/store.py) Per-click analytics are not claimed.

### UI-TOOLS · Tool progress, approvals, supervised task tree and scoped connections

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET /api/workspace/tasks/{identifier}; POST /api/workspace/tasks/{identifier}/cancel; POST /api/workspace/tasks/{identifier}/steer; POST /api/workspace/tasks/{identifier}/retry; POST /api/workspace/tasks/{identifier}/approvals/{approval_id}/resolve; GraphQL mcp_connections(project_id); GraphQL create_mcp_connection(expected_digest); GraphQL set_mcp_credential(expected_digest); GraphQL connect_mcp_connection/call_mcp_tool(project_id); GraphQL trust_mcp_connection/delete_mcp_connection(expected_digest); GET/PATCH/DELETE /api/workspace/coding-policies/{identifier}; GET /api/workspace/tasks/{identifier}/tree.

**Records:** Task events/call IDs; Approval; parent/child task tree; effective budget/location; Personal coding consent binding: principal/policy/conversation/SID/target, matcher/version/expiry; Atomic root tree pool, member binding, exact tool position reservations, worker leases, approved-resume receipt.

**Fields:** friendly tool title/status; inspectable arguments/results; allow/deny; steering text; branch/cwd; budget steps/time/parallel limits; allowed enrolled execution projects; project-owned versus admin-global connection; explicit approved catalog tool names/schema; write-only env/header/OAuth credential bindings; definition digest/credential revision; preset-selected connection bindings and exact service-tool names; explicit mcp_catalog/mcp_call broker tool selection; Only host-advertised exact action/task/conversation/personal-project/preset remember scopes; Sensitive or bounded-uninspectable effect is human once-only; no model or remembered allow; Shared reserved tool-call count and summed active-worker seconds including planning; root grant version; Explicit versioned renewal; already approved sibling/restart continuation retains grant and reservations.

**Permission contract:** Current owner/project task authority; execution decisions continuously revalidated; cancelling a parent cancels descendants; no unapproved shared-directory parallel writes; Matching capability/execution denies dominate consent; final rule version and current principal/SID validated before effect; Human waits pause the active worker meter; parallel planning and provider work consume one root allowance; no token/cost estimate; Uncertain recovered active leases exhaust time rather than replay; atomic approved-resume receipt requires explicit continuation.

**States:** started/completed/error grouped by call; human approval; budget exhausted; child denied; isolated worktree child; cancelled; retry disclosure; foreign project denied before secrets/effects; stale definition save/trust/delete refused; empty approved tool list denies all; credential edit disconnects old transport; scoped direct native adapter refused; reviewed Internal broker; manual catalog/schema inspection and explicit call; Generic approval with exact inspectable scopes; stale/withdrawn scope falls back once; Personal policy inspect, CAS edit/revoke, expiry/authority change; Concurrent worker budget pause, explicit renewal, idempotent parked-call retry; Planning budget pause resumes planning then still requires separate plan approval; Crash after approved grant requires explicit continuation within same grant.

**Code:** [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [ToolActivities.tsx](../../desktop/workspace/src/features/ToolActivities.tsx), [TaskSupervisor.tsx](../../desktop/workspace/src/features/TaskSupervisor.tsx), [BudgetApproval.tsx](../../desktop/workspace/src/features/BudgetApproval.tsx), [McpManager.tsx](../../desktop/workspace/src/features/McpManager.tsx), [McpConnectionForm.tsx](../../desktop/workspace/src/features/McpConnectionForm.tsx), [McpCredentialForm.tsx](../../desktop/workspace/src/features/McpCredentialForm.tsx), [scope.py](../../src/termx/mcp/scope.py), [task_broker.py](../../src/termx/mcp/task_broker.py), [McpPresetBindings.tsx](../../desktop/workspace/src/features/McpPresetBindings.tsx), [AgentPresetEditor.tsx](../../desktop/workspace/src/features/AgentPresetEditor.tsx), [GeneralApprovalCard.tsx](../../desktop/workspace/src/features/GeneralApprovalCard.tsx), [GeneralPolicyManager.tsx](../../desktop/workspace/src/features/GeneralPolicyManager.tsx), [coding_policies.py](../../src/termx/workspace/coding_policies.py), [tree_budget.py](../../src/termx/agent/tree_budget.py).

**Evidence:** [test_agent.py](../../tests/test_agent.py), [test_durable_workspace.py](../../tests/test_durable_workspace.py), [tool-activity.test.tsx](../../desktop/workspace/src/features/tool-activity.test.tsx), [git-workspace-rendered.json](verification/git-workspace-rendered.json), [advanced-figma.json](verification/advanced-figma.json), [test_mcp_authority.py](../../tests/test_mcp_authority.py), [mcp-manager.test.tsx](../../desktop/workspace/src/features/mcp-manager.test.tsx), [mcp-preset-bindings.test.tsx](../../desktop/workspace/src/features/mcp-preset-bindings.test.tsx), [workspace_mcp_scope_e2e.py](../../tests/workspace_mcp_scope_e2e.py), [test_tree_budget.py](../../tests/test_tree_budget.py), [test_coding_consent.py](../../tests/test_coding_consent.py), [budget-approval.test.tsx](../../desktop/workspace/src/features/budget-approval.test.tsx), [shared-tree-budget.json](verification/shared-tree-budget.json), [generic-consent-candidate-audit.json](verification/generic-consent-candidate-audit.json), [mcp-manager-rendered.json](verification/mcp-manager-rendered.json).

**Editable advanced states:** [J-CODE-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-486), [J-AUTOMATE-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-644), [J-AUTOMATE-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-665), [J-AUTOMATE-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-707), [J-RECORD-12](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-15343), [J-RECORD-13](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-15539), [J-RECORD-14](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=54-15735), [J-ENTRY-21](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-16548), [J-ENTRY-22](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-16744), [J-ENTRY-23](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-16940), [J-AUTOMATE-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-18312), [J-AUTOMATE-11](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-18508), [J-AUTOMATE-12](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-18704), [J-AUTOMATE-13](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-18900).

**Connected journeys:** J-CODE, J-AUTOMATE. Local execution/security records: [manager.py](../../src/termx/agent/manager.py), [service.py](../../src/termx/workspace/service.py) Per-click analytics are not claimed.

**Limits:** Shared tree accounting applies to the coordinated Internal AgentManager: reserved tool calls and summed active-worker seconds include provider planning; human approval/child waits are paused. Native/runner execution retains its separately declared runtime contract, and no pooled token/billed-cost accounting is invented. Actual coordinator and focused UI tests qualify the new budget source; final rendered budget proof is not claimed here.

### UI-FORK · Reviewed cross-engine continuation

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** POST /api/workspace/sessions/{identifier}/fork-preview; POST /api/workspace/fork-previews/{identifier}/commit.

**Records:** digest-bound transfer preview; canonical linked Conversation.

**Fields:** selected messages; reviewed summaries/context; target engine; provider/model; Explicit chosen messages, up to20 relative project files and64KB summary; exact transfer preview before linked fork.

**Permission contract:** Owned source; foreign turns/files refused; preview digest and current context checked before commit.

**States:** preview; confirm; changed preview denied; source preserved; linked fork; unsupported native transfer denied; Edit reviewed transfer retains chosen messages/files/summary; Lock/session epoch invalidates pending transfer creation without discarding original chat draft.

**Code:** [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [ForkContextDialog.tsx](../../desktop/workspace/src/features/ForkContextDialog.tsx).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [test_engines.py](../../tests/test_engines.py), [advanced-figma.json](verification/advanced-figma.json), [fork-context.test.tsx](../../desktop/workspace/src/features/fork-context.test.tsx), [fork-context-rendered.json](verification/fork-context-rendered.json).

**Editable advanced states:** [J-ENTRY-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-126).

**Connected journeys:** J-CODE. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

### UI-MEMORY · Scoped memory and provenance

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET/POST /api/workspace/memory; DELETE /api/workspace/memory/{identifier}; GET /api/workspace/memory/export; POST /api/workspace/memory (identifier + expected revision for edit).

**Records:** principal/project Memory.

**Fields:** content; provenance; retention; consent; excluded flag; export; edit expected revision; deliberate include/exclude choice.

**Permission contract:** Scoped ownership+project grant; context is never system authority; retention/exclusion honored before dispatch.

**States:** empty; included/excluded; expiry; revoked project scope; export.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [MemoryEditor.tsx](../../desktop/workspace/src/features/MemoryEditor.tsx).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [advanced-figma.json](verification/advanced-figma.json), [memory-editor.test.tsx](../../desktop/workspace/src/features/memory-editor.test.tsx), [memory-presets-rendered.json](verification/memory-presets-rendered.json).

**Editable advanced states:** [J-AUTOMATE-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-579), [J-AUTOMATE-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-686), [J-AUTOMATE-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-749).

**Connected journeys:** J-AUTOMATE. Local execution/security records: [service.py](../../src/termx/workspace/service.py) Per-click analytics are not claimed.

### UI-AUTOMATION · Goal, delegated schedule and execution history

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** POST /api/workspace/goals; POST /api/workspace/delegations; DELETE /api/workspace/delegations/{identifier}; POST /api/workspace/schedules/preview; POST /api/workspace/schedules; PATCH /api/workspace/schedules/{identifier}; GET /api/workspace/automations.

**Records:** Goal; Delegation grant; Schedule; durable ScheduleRun; canonical child task.

**Fields:** success criteria; conversation/execution target; grant expiry/max runs; step/time/parallel budget; daily timezone or interval; next run preview; pause/enable/history; One root reserved-call/active-worker budget shared by supervised Internal children.

**Permission contract:** Explicit durable delegation binds principal/project/engine/runner/worktree digest/budget; scope/expiry/target changes stop unattended execution.

**States:** preview; scheduled; running; approval pause; grant expired/revoked; cancelled; restart recovery without duplicate run; Planning shared budget exhausted; separate execution plan approval after renewal; Durable approved-resume receipt and explicit restart continuation.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [TaskSupervisor.tsx](../../desktop/workspace/src/features/TaskSupervisor.tsx), [tree_budget.py](../../src/termx/agent/tree_budget.py), [manager.py](../../src/termx/agent/manager.py).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [test_runner_agents.py](../../tests/test_runner_agents.py), [advanced-figma.json](verification/advanced-figma.json), [test_tree_budget.py](../../tests/test_tree_budget.py), [shared-tree-budget.json](verification/shared-tree-budget.json).

**Editable advanced states:** [J-AUTOMATE-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-579), [J-AUTOMATE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-600), [J-AUTOMATE-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-623), [J-AUTOMATE-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-644), [J-AUTOMATE-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-665), [J-AUTOMATE-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-707).

**Connected journeys:** J-AUTOMATE. Local execution/security records: [automation.py](../../src/termx/workspace/automation.py) Per-click analytics are not claimed.

## DF-04 · Browser and Computer

### UI-BROWSER · Host-managed tabs, handoff, takeover and private mode

Coverage: **runtime-connected**. Original Figma screens: DX-03, DX-04, DX-05, DX-10, DX-11

**Entry points:** GET/POST /api/browser/profiles; GET/POST /api/browser/tabs; POST /api/browser/tabs/{id}/handoff; POST /api/browser/tabs/{id}/takeover; POST /api/browser/tabs/{id}/human; GET /api/browser/tabs/{id}/context; WS /api/browser/tabs/{id}/view.

**Records:** owned BrowserProfile/Tab; task-bound grant; authority/session/policy revision; document revision; stream frame.

**Fields:** profile; URL; task scope/origins/expiry; human vs agent state; manual input; private/resume; context ref.

**Permission contract:** browser view/control/use scopes, owner/project live checks; terminal tasks cannot acquire handoff; private login excludes executor+reviewer; no external tab bridge/cookie imports.

**States:** human-owned; agent-waiting/acting; takeover; private login; explicit resume; revoked/expired; bounded reconnect; crashed worker human-only recovery.

**Code:** [BrowserSurface.tsx](../../desktop/workspace/src/features/BrowserSurface.tsx), [browser.css](../../desktop/workspace/src/features/browser.css), [context.ts](../../desktop/workspace/src/lib/context.ts).

**Evidence:** [test_managed_browser.py](../../tests/test_managed_browser.py), [test_browser_private_race.py](../../tests/test_browser_private_race.py), [test_browser_review_task.py](../../tests/test_browser_review_task.py), [browser-workflow.json](verification/browser-workflow.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-BROWSER-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-168), [J-BROWSER-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-194), [J-BROWSER-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-220), [J-BROWSER-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-244), [J-BROWSER-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-268).

**Connected journeys:** J-BROWSER. Local execution/security records: [service.py](../../src/termx/browser/service.py), [router.py](../../src/termx/browser/router.py) Per-click analytics are not claimed.

### UI-BROWSER-CONTEXT · Annotations, history, approved files and developer diagnostics

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** POST /api/browser/tabs/{id}/annotations; GET /api/browser/tabs/{id}/annotations; GET /api/browser/tabs/{id}/annotation-frames/{ref}; POST /api/browser/tabs/{id}/diagnostics; GET /api/browser/tabs/{id}/diagnostics/{view}; GET /api/browser/history; DELETE /api/browser/profiles/{id}/history; POST /api/browser/tabs/{id}/uploads; GET /api/browser/downloads/{id}.

**Records:** element/region annotation target+masked frame refs; revision fingerprint; approved upload digest; download metadata; consented diagnostic snapshot.

**Fields:** selected element or x/y region; comment; document fingerprint/stale state; masked frame inspection; Attach annotations; inspector view; history URL/back/forward/delete; filename context chips.

**Permission contract:** Browser owner/project/session and exact revision binding; diagnostics require manual consent and deny private/revoked state; approved file bytes/digests are inspectable; stale selector not reused.

**States:** matching/stale annotation; selected tab changed; consent on/off; private refusal; upload confirmation; download ready; empty/deleted history; late request discarded.

**Code:** [BrowserSurface.tsx](../../desktop/workspace/src/features/BrowserSurface.tsx), [BrowserContextControls.tsx](../../desktop/workspace/src/features/BrowserContextControls.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx).

**Evidence:** [test_browser_context_annotations.py](../../tests/test_browser_context_annotations.py), [browser-context.test.tsx](../../desktop/workspace/src/features/browser-context.test.tsx), [workspace_browser_accessibility_e2e.py](../../tests/workspace_browser_accessibility_e2e.py), [browser-context-features.json](verification/browser-context-features.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-BROWSER-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-292), [J-BROWSER-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-318), [J-BROWSER-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=33-10492), [J-RECORD-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-794), [J-RECORD-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-818).

**Connected journeys:** J-BROWSER, J-RECORD. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

**Limits:** Rendered matrix covers eighteen loaded states across both themes and 100/200% zoom; native picker values use explicit form API, other actions use actual keyboard/focus. No new Figma frames are claimed.

### UI-REVIEW · Auto Review policy, evaluated model and exact human confirmation

Coverage: **runtime-connected**. Original Figma screens: DX-04, DX-06

**Entry points:** GET/POST/DELETE /api/browser/reviewer; POST /api/browser/reviewer/evaluate; GET /api/browser/reviews; POST /api/browser/reviews/{id}/decision; GET/POST /api/browser/rules; GET /api/browser/audit.

**Records:** evaluation corpus/report; provider credential handle; exact model snapshot; review decision/permit; remembered rule; local audit.

**Fields:** provider/model; reported actual model ID; evaluation metrics; pricing/budget; allow/needs-user/block; exact action/origin payload; rule expiry/scope.

**Permission contract:** Host hard policy precedes remembered consent/model; reviewer cannot execute/elevate; model and credentials revalidated; exact one-use human decision; no automatic billing fallback.

**States:** unconfigured; evaluation pass/fail; model mismatch; budget unavailable/exhausted; review unavailable; human escalation; private paused; takeover invalidated.

**Code:** [SafetyManager.tsx](../../desktop/workspace/src/features/SafetyManager.tsx), [BrowserSurface.tsx](../../desktop/workspace/src/features/BrowserSurface.tsx).

**Evidence:** [test_auto_review.py](../../tests/test_auto_review.py), [test_reviewer_model_pin.py](../../tests/test_reviewer_model_pin.py), [test_agent_action_review.py](../../tests/test_agent_action_review.py), [reviewer-pricing-binding.json](verification/reviewer-pricing-binding.json), [safety-pricing-accessibility.json](verification/safety-pricing-accessibility.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-BROWSER-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-220), [J-BROWSER-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-244), [J-BROWSER-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-268), [J-BROWSER-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-344), [J-BROWSER-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-367), [J-BROWSER-11](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=33-10685).

**Connected journeys:** J-BROWSER, J-AUTOMATE. Local execution/security records: [auto_review.py](../../src/termx/auto_review.py), [evaluation.py](../../src/termx/browser/evaluation.py) Per-click analytics are not claimed.

### UI-CAPTURE · Computer/window capture and recording-to-skill

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET /api/desktop/recording/windows; POST /api/desktop/recording/captures; PATCH/DELETE /api/desktop/recording/captures/{identifier}; GET /api/desktop/recording/status; POST /api/desktop/recording/captures/{identifier}/draft; POST /api/desktop/recording/drafts/{identifier}/test; POST /api/desktop/recording/drafts/{identifier}/publish; POST /api/browser/tabs/{id}/recording; POST /api/browser/tabs/{id}/skill-draft.

**Records:** capture surface/display/window lease; opt-in recording; redacted parametrized draft; tested skill revision.

**Fields:** app/window choice; view/control; private recording pause; global capture indicator/open/stop; redaction and parameters; test output; enable draft.

**Permission contract:** desktop-view/control and current owner/session; OS consent required; global status never returns page pixels/titles/URLs; test-before-enable; private observations excluded.

**States:** OS permission unavailable; surface lost; recording on/off/private; global stop across layouts; draft/test failed/pass; published skill.

**Code:** [ComputerSurface.tsx](../../desktop/workspace/src/features/ComputerSurface.tsx), [CaptureStatus.tsx](../../desktop/workspace/src/features/CaptureStatus.tsx), [BrowserSurface.tsx](../../desktop/workspace/src/features/BrowserSurface.tsx).

**Evidence:** [test_window_recording.py](../../tests/test_window_recording.py), [test_browser_skills.py](../../tests/test_browser_skills.py), [browser-accessibility-capture.json](verification/browser-accessibility-capture.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-RECORD-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-770), [J-RECORD-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-794).

**Connected journeys:** J-RECORD. Local execution/security records: [recording.py](../../src/termx/desktop/recording.py), [skills.py](../../src/termx/browser/skills.py) Per-click analytics are not claimed.

**Limits:** OS app/window capture support is explicitly platform-qualified in support-matrix.md; unsupported capture adapters refuse rather than impersonating a working desktop.

## DF-05 · Workbench and delivery

### UI-EDITOR · Editor, quick file search, splits and language tooling

Coverage: **runtime-connected-with-limits**. Original Figma screens: DX-02

**Entry points:** GET /api/workspace/sessions/{identifier}/files; PUT /api/workspace/sessions/{identifier}/files; POST /api/workspace/sessions/{identifier}/terminal; WS /api/projects/{project_id}/lsp.

**Records:** owned canonical session execution root/worktree digest; dirty Buffer/root/revision; LSP document/version/diagnostics.

**Fields:** quick filename query; file path/text; split direction/file; save revision; definition/hover/completion/format/problems.

**Permission contract:** files-read/write grant on session+project; worktree enrollment resolves root; traversal/outside definitions and stale target refused; dirty buffer never saved into another checkout.

**States:** loading/read-only/binary/size limit; dirty/save/conflict; current vs recovered copy; target removed; definition outside root; language unavailable/reconnect.

**Code:** [Workbench.tsx](../../desktop/workspace/src/features/Workbench.tsx), [lsp.ts](../../desktop/workspace/src/lib/lsp.ts).

**Evidence:** [test_workspace_quick_files.py](../../tests/test_workspace_quick_files.py), [test_lsp_navigation.py](../../tests/test_lsp_navigation.py), [editor-splits.test.tsx](../../desktop/workspace/src/features/editor-splits.test.tsx), [quick-file-workspace.json](verification/quick-file-workspace.json), [accessibility.json](verification/accessibility.json), [lsp-layout-lifecycle.json](verification/lsp-layout-lifecycle.json), [advanced-figma.json](verification/advanced-figma.json), [workspace-final-accessibility.json](verification/workspace-final-accessibility.json).

**Editable advanced states:** [J-CODE-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-388), [J-CODE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-412), [J-CODE-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-436), [J-CODE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-558), [J-DETACH-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1241).

**Connected journeys:** J-CODE, J-DETACH. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

**Limits:** Quick file open is execution-checkout scoped for local sessions. A selected cloud runner does not silently search/edit a host execution checkout; local source upload remains explicit.

### UI-PTY-DEBUG · Persistent terminal and debugger

Coverage: **runtime-connected-with-limits**. Original Figma screens: DX-02

**Entry points:** POST /api/workspace/sessions/{identifier}/terminal; GET/POST /api/development/projects/{project_id}/debug; GET /api/development/debug/{session_id}/events; POST /api/development/debug/{session_id}/command; DELETE /api/development/debug/{session_id}.

**Records:** PTY canonical session/output cursor; scoped DAP adapter/session; breakpoints/stack/variables.

**Fields:** shell/terminal choice; rows/cols/input; launch or scoped attach; breakpoint line; continue/step/stack/variables; adapter availability.

**Permission contract:** terminal-control and exact canonical cwd/worktree; constrained sandbox refuses unsupported rather than host fallback; agent-run DAP authority; source path boundary; cloud local debug refused.

**States:** connected/reconnect/revoked; process exit; debug starting/stopped/paused; Python/JS/TS mapped breakpoint; attach denial; adapter missing.

**Code:** [TerminalPanel.tsx](../../desktop/workspace/src/features/TerminalPanel.tsx), [Debugger.tsx](../../desktop/workspace/src/features/Debugger.tsx).

**Evidence:** [test_sandbox_workspace.py](../../tests/test_sandbox_workspace.py), [test_debug_workspace.py](../../tests/test_debug_workspace.py), [test_workspace_transports.py](../../tests/test_workspace_transports.py), [git-workspace-rendered.json](verification/git-workspace-rendered.json), [ci-7d9a4b6.json](verification/ci-7d9a4b6.json), [advanced-figma.json](verification/advanced-figma.json), [workspace-final-accessibility.json](verification/workspace-final-accessibility.json).

**Editable advanced states:** [J-CODE-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-436), [J-DETACH-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1173).

**Connected journeys:** J-CODE, J-DETACH. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

**Limits:** Windows restricted ConPTY/native installed qualification remains open; original host PTY proof is not substituted for it.

### UI-DELIVERY · Worktrees, selective hunks and reviewed Git/PR operations

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET /api/development/projects/{project_id}/delivery; GET /api/development/projects/{project_id}/delivery/diff; POST /api/development/projects/{project_id}/delivery/prepare; POST /api/development/projects/{project_id}/delivery/actions/{action_id}/execute; GET /api/development/projects/{project_id}/delivery/pulls/{number}.

**Records:** enrolled Git worktree; diff/hunk digest; prepared action receipt; PR/check state.

**Fields:** checkout/branch/base; file/hunk selection; stage/unstage; commit text; explicit remote/new-branch target; confirmation payload; pull/check result; cleanup guard.

**Permission contract:** git-read/write plus project grant; exact prepared target/diff and single-use receipt; publication is explicit human action; dirty/conflicted/diverged worktrees refuse unsafe operation.

**States:** clean/dirty/staged; hunk change denied; checks passed/failed; diverged pull/conflict; unknown action outcome; PR/check waiting; dirty cleanup refused; removed target reset.

**Code:** [Delivery.tsx](../../desktop/workspace/src/features/Delivery.tsx), [Workbench.tsx](../../desktop/workspace/src/features/Workbench.tsx).

**Evidence:** [test_delivery_workspace.py](../../tests/test_delivery_workspace.py), [workspace_delivery_e2e.py](../../tests/workspace_delivery_e2e.py), [git-workspace-rendered.json](verification/git-workspace-rendered.json), [live-delivery.json](verification/live-delivery.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-CODE-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-388), [J-CODE-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-462), [J-CODE-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-486), [J-CODE-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-510), [J-CODE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-558).

**Connected journeys:** J-CODE. Local execution/security records: [delivery.py](../../src/termx/development/delivery.py) Per-click analytics are not claimed.

### UI-PREVIEW · Scoped host preview verification

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** POST /api/development/projects/{project_id}/previews; GET /api/development/projects/{project_id}/previews/{identifier}/frame; GET /api/development/projects/{project_id}/previews/{identifier}/context; POST /api/development/projects/{project_id}/previews/{identifier}/input; DELETE /api/development/projects/{project_id}/previews/{identifier}.

**Records:** independent preview BrowserProfile/Tab; exact host origin; owner lease.

**Fields:** host loopback URL/port; frame; typed user input; context verification; close.

**Permission contract:** host-admin for host-loopback preview creation, owned project authority, control service port excluded; no viewer localhost or implicit cookies.

**States:** starting; ready; context inspected; bad host origin; private control endpoint refusal; stopped/revoked.

**Code:** [Preview.tsx](../../desktop/workspace/src/features/Preview.tsx).

**Evidence:** [test_host_previews.py](../../tests/test_host_previews.py), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-CODE-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-534).

**Connected journeys:** J-CODE. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

## DF-06 · Creation and runners

### UI-MEDIA · Voice, image capabilities and editable artifact versions

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET/POST /api/media/artifacts; PUT /api/media/artifacts/{identifier}; GET /api/media/artifacts/{identifier}/versions; GET /api/media/artifacts/{identifier}/export; POST /api/media/inputs; POST /api/media/inputs/{identifier}/context; POST /api/media/generate; GET /api/media/operations.

**Records:** owned artifact immutable versions; input upload/conversion; provider operation digest/receipt.

**Fields:** artifact kind/title; document/code text; table cells; chart series; deck slides/notes; version/export; record/stop voice; capability/provider/model/prompt/voice; explicit billing consent; conversation-scoped microphone capture; review and append transcript without sending; explicit read-aloud voice/model.

**Permission contract:** files/agent scopes plus owner/project; media capability account selected explicitly; credentials backend-only; bounded input; provider operations never replay unknown outcomes.

**States:** empty; editing/new version; old immutable version; microphone denied/recording; capability missing; unsupported conversion; paid consent absent; pending/completed/unknown operation; download error.

**Code:** [Media.tsx](../../desktop/workspace/src/features/Media.tsx), [media-intent.ts](../../desktop/workspace/src/lib/media-intent.ts), [transfers.ts](../../desktop/workspace/src/lib/transfers.ts), [VoiceControls.tsx](../../desktop/workspace/src/features/VoiceControls.tsx).

**Evidence:** [test_media_workspace.py](../../tests/test_media_workspace.py), [media-intent.test.ts](../../desktop/workspace/src/lib/media-intent.test.ts), [media-workspace-rendered.json](verification/media-workspace-rendered.json), [voice-controls.test.tsx](../../desktop/workspace/src/features/voice-controls.test.tsx), [workspace_voice_e2e.py](../../tests/workspace_voice_e2e.py), [advanced-figma.json](verification/advanced-figma.json), [voice-workspace-rendered.json](verification/voice-workspace-rendered.json), [session-presets-rendered.json](verification/session-presets-rendered.json).

**Editable advanced states:** [J-CREATE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-967), [J-CREATE-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-993), [J-CREATE-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1014), [J-CREATE-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1040), [J-CREATE-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1063).

**Connected journeys:** J-CREATE. Local execution/security records: [service.py](../../src/termx/media/service.py) Per-click analytics are not claimed.

**Limits:** Actual editable/exported artifact proof covers all five editable kinds. Real paid voice/image account execution has not been authorized or run; provider contract fixtures are not a claim of paid provider success.

### UI-RUNNER · Dedicated container enrollment, jobs and AI sessions

Coverage: **runtime-connected-with-limits**. Original Figma screens: DX-08

**Entry points:** GET/POST /api/runners; POST /api/runners/{identifier}/workspace; GET/POST /api/runners/{identifier}/jobs; POST /api/runners/{identifier}/stop; DELETE /api/runners/{identifier}; GET /api/runner-agents/capabilities; POST /api/runner-agents/{runner_id}/preflight.

**Records:** immutable runner image/protocol capability; uploaded workspace; job receipt/results; canonical runner Task; explicit provider credential ref; delegated schedule target.

**Fields:** name/image/quota/time/network mode; upload; argv/logs/result; teardown; session runner/provider/model/credential ref; effective budgets.

**Permission contract:** host-admin Docker authority; qualified internal worker is nonroot/network-none/read-only/cap-drop; provider keys stay host broker; principal/delegation/session continuously revalidated; no implicit local fallback.

**States:** qualified/unqualified; provisioning; running logs; approval/budget wait; cancel; unknown/restart reconciliation; revoked session/grant; teardown complete.

**Code:** [Runners.tsx](../../desktop/workspace/src/features/Runners.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx).

**Evidence:** [test_dedicated_runners.py](../../tests/test_dedicated_runners.py), [test_runner_agents.py](../../tests/test_runner_agents.py), [ci-2a57243.json](verification/ci-2a57243.json), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-AUTOMATE-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-600), [J-AUTOMATE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-728), [J-CREATE-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1084), [J-CREATE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1107), [J-CREATE-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1128).

**Connected journeys:** J-AUTOMATE, J-CREATE. Local execution/security records: [agent.py](../../src/termx/runners/agent.py), [service.py](../../src/termx/runners/service.py) Per-click analytics are not claimed.

**Limits:** Documented BYO VM/container path is fulfilled by qualified Docker containers. Undeclared SSH/VM adapters are not claimed; installed native engines on remote runners remain unsupported.

## DF-07 · Managers and advanced shell

### UI-MODELS · Engine/provider inventory and credentials

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GraphQL engines/engine_models/agent_providers/acp_registry; GraphQL authenticate_engine/install_acp_runner/save_agent_provider/test_agent_provider.

**Records:** installed engine capability/account state; provider credential handle; model catalog; principal model favorites.

**Fields:** engine version/auth/capabilities; registry package; provider API/key/default model; per-session favorites; explicit image/audio capability declarations; credential rotation preserves saved capabilities; Owner-scoped shared model favorite chips; current catalog matches and unavailable saved entries.

**Permission contract:** host-admin installation/provider mutations; session composer alone selects active engine/model; no workspace global engine; explicit account billing distinction.

**States:** installed/missing/error; catalog refresh; credential absent; auth pending; capability inspection; test connection failure; Favorites inspected across Models and session composer; Unavailable saved model removed without changing active conversation.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [ProviderAccountForm.tsx](../../desktop/workspace/src/features/ProviderAccountForm.tsx), [ModelFavoritesManager.tsx](../../desktop/workspace/src/features/ModelFavoritesManager.tsx), [model-favorites.ts](../../desktop/workspace/src/lib/model-favorites.ts).

**Evidence:** [test_engines.py](../../tests/test_engines.py), [test_engines_acp.py](../../tests/test_engines_acp.py), [test_durable_workspace.py](../../tests/test_durable_workspace.py), [managers.test.tsx](../../desktop/workspace/src/features/managers.test.tsx), [advanced-figma.json](verification/advanced-figma.json), [model-favorites-manager.test.tsx](../../desktop/workspace/src/features/model-favorites-manager.test.tsx), [model-favorites-manager-rendered.json](verification/model-favorites-manager-rendered.json).

**Editable advanced states:** [J-CREATE-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-944), [J-CREATE-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=33-10878), [J-ENTRY-29](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=64-18116).

**Connected journeys:** J-ENTRY, J-CODE. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

### UI-PRESETS · Owned preset management, per-session selection and immutable execution

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GraphQL custom_agents/custom_agent/create_custom_agent/patch_custom_agent/duplicate_custom_agent/import_custom_agent/delete_custom_agent/custom_agent_export; GET /api/workspace/presets; POST /api/workspace/sessions (custom_agent_id); PATCH /api/workspace/sessions/{identifier} (explicit preset refresh); POST /api/workspace/sessions/{identifier}/turns.

**Records:** owned custom agent Markdown/profile; session preset ID + config digest; immutable task_agent_presets snapshot; digest-bound schedule execution target; credential-free runner startup profile.

**Fields:** name/description/instructions; engine/model/provider defaults; permitted tools/run limits; approval/sandbox profile; enabled/sync revision; New conversation agent preset; Session agent preset; Refresh preset binding; named toolsets/explicit inherited tools/denials; expected revision; step/time/parallel budgets.

**Permission contract:** host-admin library mutations with immutable resource owner claims; selected preset owner/project must match conversation; legacy enabled device/bundled shared definitions explicit; execution requires conversation/project agent-run; no preset grants authority.

**States:** empty/unavailable/disabled/foreign; select before first turn; active run selection disabled; edited profile refuses new run until explicit refresh; native session binding immutable after first turn; compatible qualified internal container preset; unsupported runner tools/native formats refused.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [PresetPicker.tsx](../../desktop/workspace/src/features/PresetPicker.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [presets.py](../../src/termx/workspace/presets.py), [service.py](../../src/termx/workspace/service.py), [store.py](../../src/termx/agent/store.py), [manager.py](../../src/termx/agent/manager.py), [agent.py](../../src/termx/runners/agent.py), [worker.py](../../src/termx/runners/worker.py), [AgentPresetEditor.tsx](../../desktop/workspace/src/features/AgentPresetEditor.tsx).

**Evidence:** [test_workspace_presets.py](../../tests/test_workspace_presets.py), [test_custom_agent_authority.py](../../tests/test_custom_agent_authority.py), [preset-selection.test.tsx](../../desktop/workspace/src/features/preset-selection.test.tsx), [session-presets-runner.json](verification/session-presets-runner.json), [advanced-figma.json](verification/advanced-figma.json), [agent-preset-editor.test.tsx](../../desktop/workspace/src/features/agent-preset-editor.test.tsx), [test_workspace_preset_tools_edit.py](../../tests/test_workspace_preset_tools_edit.py), [memory-presets-rendered.json](verification/memory-presets-rendered.json).

**Editable advanced states:** [J-ENTRY-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-98), [J-AUTOMATE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-728), [J-CREATE-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1107).

**Connected journeys:** J-CODE. Local execution/security records: [store.py](../../src/termx/workspace/store.py), [store.py](../../src/termx/agent/store.py) Per-click analytics are not claimed.

**Limits:** Compatible internal runner presets carry frozen instructions/tool subset/limits without credentials. Native engine enforcement follows its actual capability matrix; unsupported strict formats refuse. New paid native/provider invocation is not implied by local contract tests.

### UI-EXTENSIONS · Plugins, skills, registries and tools/connections

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET /api/workspace/extensions; POST /api/workspace/extensions/preview; POST /api/workspace/extension-previews/{identifier}/install; POST /api/workspace/extensions/{identifier}/rollback-preview; GET /api/workspace/extensions/{identifier}/export; POST /api/workspace/registries; GET /api/workspace/registries/{identifier}/packages; GraphQL mcp_connections; GET /api/workspace/skill-sources; GET/PUT /api/workspace/skill-sources/source; POST /api/workspace/skill-sources/validate; GET /api/workspace/skill-sources/versions; GET /api/workspace/skill-sources/versions/{identifier}; POST /api/workspace/skill-sources/publish-preview; GET /api/workspace/skill-sources/compatibility; GET /api/workspace/skill-sources/compatibility/{identifier}; POST /api/workspace/registries/{identifier}/preview; PATCH /api/workspace/registries/{identifier}/credential.

**Records:** normalized extension manifest/hash/versions; trusted registry adapter; runtime use lease; MCP connection approved tool scope; digest-bound source revision and immutable skill_version history; owner-scoped registry revision and backend-only credential reference; raw-download and canonical manifest hashes; exact dependency digests.

**Fields:** source/adapter; capabilities; install/update/rollback preview; active session skills; MCP transport/url/trust/approved tools; export; Global/Project native SKILL.md scope and path; metadata/body validation, compare-and-save digest; effective project override and read-only compatibility source; file import/export and restore-as-draft; public_https/private_https/private_local registry kind and HTTPS index; write-only credential and rotation; provenance, package digest, dependency version and permission difference; Normalized settings search across safe source/compatibility/bundle/registry/package metadata.

**Permission contract:** host-admin lifecycle; current principal/resource execution lease; active dependencies block destructive changes; no arbitrary code lifecycle hook; supported adapter matrix explicit; Global sources require host-admin; project edits require enrolled project and live files-read/write scope; No-follow source traversal refuses links/junctions; secret/oversize/unsafe Markdown refused before export; Verified HTTPS pins public resolved addresses and same-origin redirects; bounded JSON-only downloads; no request-selected credential reference; Credential rotation and changed native source invalidate outstanding reviews; owner/grant revalidated before publication; missing exact dependency blocks installation.

**States:** preview valid/invalid; installed/active lease; update/rollback; dependency denied; private registry trust refusal; MCP disabled/auth pending; unsaved imported/restored draft; no implicit activation; external source edit conflict; draft preserved; source validation failed; unsafe native source withheld; private credential unavailable/rotated; stale review denied; download hash mismatch; protected origin/redirect/size refusal; readonly native format copied without modifying original; No-match inventory retains current source editor, external revision conflict, previews and credential forms.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx), [Chat.tsx](../../desktop/workspace/src/features/Chat.tsx), [ExtensionManager.tsx](../../desktop/workspace/src/features/ExtensionManager.tsx), [skill_sources.py](../../src/termx/workspace/skill_sources.py), [https_registry.py](../../src/termx/workspace/https_registry.py), [extensions.py](../../src/termx/workspace/extensions.py), [http.py](../../src/termx/workspace/http.py).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [test_mcp.py](../../tests/test_mcp.py), [support-matrix.md](support-matrix.md), [advanced-figma.json](verification/advanced-figma.json), [test_skill_sources.py](../../tests/test_skill_sources.py), [test_https_registries.py](../../tests/test_https_registries.py), [extensions.test.tsx](../../desktop/workspace/src/features/extensions.test.tsx), [workspace_extensions_e2e.py](../../tests/workspace_extensions_e2e.py), [extensions-sources-registries.json](verification/extensions-sources-registries.json).

**Editable advanced states:** [J-RECORD-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-818), [J-RECORD-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-839), [J-RECORD-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-860), [J-RECORD-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-881), [J-RECORD-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-902), [J-RECORD-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-923), [J-RECORD-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=41-11133), [J-RECORD-10](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=41-11329), [J-RECORD-11](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=41-11525).

**Connected journeys:** J-RECORD, J-AUTOMATE. Local execution/security records: [extensions.py](../../src/termx/workspace/extensions.py), [skill_sources.py](../../src/termx/workspace/skill_sources.py) Per-click analytics are not claimed.

**Limits:** Registry wire format is bounded termx-registry/v1 JSON with inert supported bundles, SHA-256 digests and same-origin package URLs; authenticated HTTPS uses backend-owned Bearer credentials. Saving/importing source never enables execution. Project-over-global precedence is for explicit portable publication; native engines retain their own discovery precedence. Other registry protocols/authentication mechanisms or executable plugin lifecycle hooks are not claimed.

### UI-HOOKS · Scoped hook configuration and history

Coverage: **runtime-connected**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** GET/POST /api/workspace/hooks; PATCH /api/workspace/hooks/{identifier}; GET /api/workspace/hook-runs.

**Records:** owned project Hook; bounded HookRun.

**Fields:** before_turn/after_turn/task_failed; argv/cwd; declared capabilities; timeout; enabled; run output.

**Permission contract:** agent-control configuration and scoped execution grants; current session root bound; process boundary and timeout enforced; cloud host hooks refused.

**States:** disabled/enabled; succeeded/timeout/error; scope revoked; missing sandbox capability.

**Code:** [Managers.tsx](../../desktop/workspace/src/features/Managers.tsx).

**Evidence:** [test_durable_workspace.py](../../tests/test_durable_workspace.py), [advanced-figma.json](verification/advanced-figma.json).

**Editable advanced states:** [J-AUTOMATE-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-686), [J-AUTOMATE-09](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-749).

**Connected journeys:** J-AUTOMATE. Local execution/security records: [service.py](../../src/termx/workspace/service.py) Per-click analytics are not claimed.

### UI-DOCK · Docking, multiwindow recovery and monitor-aware shell

Coverage: **runtime-connected-with-limits**. Original Figma screens: No original core frame assigned; advanced nodes below are independently verified.

**Entry points:** trusted native detach/redock commands; same-origin nonce-bound browser window handshake; canonical session reads before handoff.

**Records:** principal layout/geometry/history; per-window IDB buffer/draft slots; owner/session/window nonce; accepted return receipt.

**Fields:** main/right/split/hidden region; resize/keyboard reorder; Review/Focus/undo/reset; detach/recover/return; unsaved buffer conflict copies; full draft images/context; monitor/window geometry; Independent Editor/Conversation/Browser/Computer/Artifacts panel identity; named per-account device layout save/search/restore/remove; per-preset customization; owner-scoped unsaved artifact editor draft and version context.

**Permission contract:** Current principal/session verified before detached transfer; source/opener/origin/window nonce matched; persist and acknowledge before source close; invalid owner/path/expiry refused.

**States:** popup blocked; active named window focus; closed slot reopen; draft conflict refused; duplicate/late ack cancelled; unavailable main/source remains open; 200% adaptive bounds; native window monitor restore; legacy layout migration; restored custom preset; independent Computer/Artifacts focus/detach.

**Code:** [App.tsx](../../desktop/workspace/src/App.tsx), [DockWorkspace.tsx](../../desktop/workspace/src/components/DockWorkspace.tsx), [window-state.ts](../../desktop/workspace/src/lib/window-state.ts), [window-journal.ts](../../desktop/workspace/src/lib/window-journal.ts), [browser-windows.ts](../../desktop/workspace/src/lib/browser-windows.ts), [native.ts](../../desktop/workspace/src/lib/native.ts), [redock-state.ts](../../desktop/workspace/src/lib/redock-state.ts), [LayoutPresets.tsx](../../desktop/workspace/src/components/LayoutPresets.tsx), [layout-presets.ts](../../desktop/workspace/src/lib/layout-presets.ts), [artifact-drafts.ts](../../desktop/workspace/src/lib/artifact-drafts.ts).

**Evidence:** [browser-windows.test.ts](../../desktop/workspace/src/lib/browser-windows.test.ts), [native-redock.test.ts](../../desktop/workspace/src/lib/native-redock.test.ts), [browser-window-lifecycle.json](verification/browser-window-lifecycle.json), [accessibility.json](verification/accessibility.json), [advanced-figma.json](verification/advanced-figma.json), [layout-presets.test.ts](../../desktop/workspace/src/lib/layout-presets.test.ts), [artifact-drafts.test.ts](../../desktop/workspace/src/lib/artifact-drafts.test.ts), [computer-independent-docking.json](verification/computer-independent-docking.json).

**Editable advanced states:** [J-DETACH-01](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1149), [J-DETACH-02](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1173), [J-DETACH-03](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1199), [J-DETACH-04](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1220), [J-DETACH-05](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1241), [J-DETACH-06](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1265), [J-DETACH-07](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1286), [J-DETACH-08](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1307).

**Connected journeys:** J-DETACH. Local execution/security records: No independent record is claimed. Per-click analytics are not claimed.

**Limits:** Runtime scope includes independent five-panel placement, named layouts and unsaved artifact drafts. Actual native/browser close/reopen/redock/monitor and retained artifact journeys remain qualified by their own immutable fixture/artifact evidence; design prototypes do not establish runtime effect execution.

## Connected runtime and editable design journeys

### J-ENTRY · Password/SSO to owned session

Dynamic method discovery → Password setup/login or OIDC callback → Managed session and scope projection → Open canonical project/conversation without dashboard → Session composer chooses account → Lock the current device while preserving unsent state → Reauthenticate the same canonical account; explicitly resume protected surfaces → Grant and exchange a bounded one-use mobile pairing ticket or revoke/renew it → Inspect exact current-host effects; confirm shutdown or cancel.

Surfaces: UI-ENTRY, UI-ACCESS, UI-CHAT, UI-TOKENS. Evidence: [test_identity.py](../../tests/test_identity.py), [test_identity_guard.py](../../tests/test_identity_guard.py), [workspace-rendered.json](verification/workspace-rendered.json), [advanced-figma.json](verification/advanced-figma.json), [test_session_lifecycle.py](../../tests/test_session_lifecycle.py), [managed-mobile-pairing.json](verification/managed-mobile-pairing.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-3&starting-point-node-id=32%3A3) · 31 verified state frames.

### J-BROWSER · Session to handoff/private/resume/takeover

Start an authorized live task → Open host-managed tab/profile → Inspect origin and grant bounded task control → Private login pauses agent/reviewer → Explicit resume and evaluated action decision → Take over revokes queued permits → Inspect/attach verified context and annotated frame.

Surfaces: UI-CHAT, UI-BROWSER, UI-REVIEW, UI-BROWSER-CONTEXT. Evidence: [browser-workflow.json](verification/browser-workflow.json), [test_browser_context_annotations.py](../../tests/test_browser_context_annotations.py), [advanced-figma.json](verification/advanced-figma.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-168&starting-point-node-id=32%3A168) · 11 verified state frames.

### J-CODE · Code/debug to parallel worktrees and reviewed delivery

Select enrolled session worktree → Open named file and retain dirty split buffers → Run terminal checks and scoped debugger → Review diffs/stage selected hunks → Explicit commit/push/PR/check operation → Verify exact host preview → Guard dirty/conflicted cleanup.

Surfaces: UI-EDITOR, UI-PTY-DEBUG, UI-DELIVERY, UI-PREVIEW, UI-TOOLS, UI-COMMANDS. Evidence: [git-workspace-rendered.json](verification/git-workspace-rendered.json), [test_lsp_navigation.py](../../tests/test_lsp_navigation.py), [test_debug_workspace.py](../../tests/test_debug_workspace.py), [test_host_previews.py](../../tests/test_host_previews.py), [advanced-figma.json](verification/advanced-figma.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-388&starting-point-node-id=32%3A388) · 8 verified state frames.

### J-AUTOMATE · Scheduled goal to supervised isolated work and outcome

Save success criteria and bounded goal → Preview timezone/interval next runs → Grant exact execution target/expiry/budgets → Dispatch durable schedule without duplicate run → Pause for child approval or budget → Inspect child location and output → Revoke/cancel descendants and reconcile restart.

Surfaces: UI-AUTOMATION, UI-TOOLS, UI-MEMORY, UI-HOOKS, UI-RUNNER. Evidence: [test_durable_workspace.py](../../tests/test_durable_workspace.py), [test_runner_agents.py](../../tests/test_runner_agents.py), [advanced-figma.json](verification/advanced-figma.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-579&starting-point-node-id=32%3A579) · 13 verified state frames.

### J-RECORD · Record/redact/parameterize/test/enable a skill

Choose authorized tab/window and opt in → Persistent global indicator permits open/stop across layouts → Exclude private steps and redact recorded values → Review parameterized draft → Test current draft revision under live control grants → Enable only tested revision in owned extension runtime → Inspect native source scope and effective override → Edit/import/export validated SKILL.md with CAS conflict handling → Restore immutable history as a draft → Browse public/private HTTPS package with write-only credential → Review exact provenance/dependencies/permissions before installing; rotation rejects stale review.

Surfaces: UI-CAPTURE, UI-BROWSER-CONTEXT, UI-EXTENSIONS. Evidence: [test_browser_skills.py](../../tests/test_browser_skills.py), [test_window_recording.py](../../tests/test_window_recording.py), [browser-accessibility-capture.json](verification/browser-accessibility-capture.json), [advanced-figma.json](verification/advanced-figma.json), [extensions-sources-registries.json](verification/extensions-sources-registries.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-770&starting-point-node-id=32%3A770) · 14 verified state frames.

### J-CREATE · Voice/input to versioned artifact and dedicated runner

Capture visible microphone input or upload owned file → Choose configured media account/model and explicit billing consent → Inspect transcript/image or converted context → Edit artifact and save immutable new version → Export selected document/table/deck/chart/code version → Select qualified runner and uploaded workspace explicitly.

Surfaces: UI-MEDIA, UI-CHAT, UI-RUNNER. Evidence: [media-workspace-rendered.json](verification/media-workspace-rendered.json), [test_media_workspace.py](../../tests/test_media_workspace.py), [test_runner_agents.py](../../tests/test_runner_agents.py), [advanced-figma.json](verification/advanced-figma.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-944&starting-point-node-id=32%3A944) · 10 verified state frames.

### J-DETACH · Detach/reconnect/reopen/acknowledged return

Detach actual selected panel/session through trusted entrypoint → Persist each window dirty buffers/full draft independently → Close/reopen named owner slot or reconnect transports → Retain running canonical task and same PTY → Refuse differing unsent main draft → Transfer conflict copies and await durable acknowledgement → Close source only after unchanged accepted snapshot.

Surfaces: UI-DOCK, UI-CHAT, UI-EDITOR, UI-PTY-DEBUG, UI-TOKENS. Evidence: [browser-window-lifecycle.json](verification/browser-window-lifecycle.json), [native-redock.test.ts](../../desktop/workspace/src/lib/native-redock.test.ts), [advanced-figma.json](verification/advanced-figma.json).

[Editable prototype](https://www.figma.com/proto/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-1149&starting-point-node-id=32%3A1149) · 8 verified state frames.

## Provenance and qualification boundaries

[Connected canvas](https://www.figma.com/design/Vo9guk8YNqP0H9DUMPvmCv?node-id=32-2) reuses original semantic tokens, Geist/Geist Mono typography, workspace panels and editable form patterns. No flattened screenshot substitutes for these states. [Exact node metadata](figma/advanced-node-map.json) separates the canonical connected page from preserved references. Prototype fields are illustrative and do not submit credentials or execute tools.

[Inspected renders](figma/advanced-renders/) include entry failure, delivery review, Voice, detached draft conflicts, remembered-rule edits, supervised children, failed skill tests, native-source CAS conflicts, HTTPS provenance review and compact layouts.

- Installed native unlock/re-dock/monitor topology proof is independent from browser popup proof.

- Paid configured voice/image execution is unperformed; contract/artifact fixtures do not prove paid inference.

- The extension rendered fixture qualifies its immutable asset snapshot and verified local TLS transport. External production registry availability and undocumented adapters are not inferred.

- Queue/Jump actual immutable Buw UI and later policy-fingerprint source regressions have separate evidence. MCP actual Cfb Manager→owned typed preset→AgentManager→local stdio tool execution is verified, including ordinary human approval; Figma navigation does not prove execution.

- CJW Access eight-state proof predates configurable SessionPolicy. Shared tree coordinator/planning/restart regressions and four budget UI tests pass; the final session-policy/shared-budget rendered snapshot remains independently qualified by its own forthcoming report.

## Resolved audit findings

- **GAP-PRESET-INVOKE**: Owned explicit session preset selector, digest-bound execution and immutable task-local snapshot added; compatible internal runner profiles integrated. Evidence: [test_workspace_presets.py](../../tests/test_workspace_presets.py), [preset-selection.test.tsx](../../desktop/workspace/src/features/preset-selection.test.tsx).

- **GAP-CODE-FONT**: Bundled pinned Geist Mono Variable applies code/terminal output; theme changes preserve xterm instance and active PTY. Evidence: [terminal-theme.test.tsx](../../desktop/workspace/src/features/terminal-theme.test.tsx).

- **GAP-ADVANCED-FIGMA**: Actual external editable advanced states, shared patterns, compact layouts and connected recovery prototypes authored without modifying original core IDs. Evidence: [advanced-figma.json](verification/advanced-figma.json), [advanced-node-map.json](figma/advanced-node-map.json).

- **GAP-SKILL-SOURCE-MANAGEMENT**: Bounded global/project native discovery, read-only compatibility import, Markdown validation, CAS edits, immutable versions, draft restore/import/export and effective override inspection connected to actual permission-reviewed activation. Evidence: [test_skill_sources.py](../../tests/test_skill_sources.py), [test_https_registries.py](../../tests/test_https_registries.py), [extensions-sources-registries.json](verification/extensions-sources-registries.json).

- **GAP-HTTPS-REGISTRY**: Public HTTPS/private authenticated registry adapters fetch verified bounded same-origin JSON, pin public DNS addresses, preserve backend-only credentials, validate exact raw/canonical digests/dependencies and revalidate live owner plus credential revision before installation. Evidence: [test_skill_sources.py](../../tests/test_skill_sources.py), [test_https_registries.py](../../tests/test_https_registries.py), [extensions-sources-registries.json](verification/extensions-sources-registries.json).
