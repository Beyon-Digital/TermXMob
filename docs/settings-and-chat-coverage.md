# Settings and chat coverage checklist

The inventory covers the shared TermXMob desktop/browser workspace, including contextual conversation settings. The separate Expo companion retains its native settings screens and adds explicit LAN trust removal; its transport work carries forward the existing draft managed-auth PR rather than editing that PR or replacing its state architecture.

## Navigation and shared form behavior

- [x] Before: repeated operations/access categories in the main drawer and a second settings rail. After: Home/Overview, searchable folder-grouped conversations, and one sticky Settings entry.
- [x] One category registry supplies the canonical settings rail, compact horizontal chips, legacy route aliases and command destinations. Existing `/manage/*` links open the same content inside the dialog/sheet. Projects/Runs remain Home views; Media remains a workspace surface.
- [x] Settings uses the existing Radix modal focus scope: trap, Escape, keyboard navigation, return focus and a navigation-button fallback when resizing unmounts the opener. Mobile uses dynamic viewport height, safe-area padding and contained scrolling.
- [x] A scope selector keeps account/host settings and project-specific hooks, memory, tools and runners reachable. Scope/category changes, history navigation, refresh/cancel and close protect drafts.
- [x] The shared form shell validates native field constraints before older div-form save handlers run. Destructive settings actions require review before dispatching their original handler. No backend permission or irreversible cloud action is executed merely by opening settings.
- [x] Successful writes clean only the unchanged form that initiated them; failed requests and edits made during a pending save remain protected. Unsaved values, including secrets, are not serialized by this shell.
- [x] Shared compact labels, controls, fieldsets, wrapping and mobile layout use the existing theme tokens. Existing loading, error, conflict, empty and save feedback is retained.

## Category and nested-flow inventory

| Category / sources | Before / retained flows | Changes and coverage |
| --- | --- | --- |
| General | Account identity and sign out | Canonical dialog category; sign out remains available after drawer cleanup. |
| Models & engines; ProviderAccountForm; ModelFavoritesManager | Inspect installed engines, authenticate/refresh/install; provider create/edit/remove/test; capability declarations; favorites | Provider internal keys auto-generate with Advanced override; display-name validation; masked replacement secret preserves current credential on blank; destructive review and pending-write guard. Existing model/favorite tests. |
| CloudAccounts | AWS/Azure/GCP verify/create/update/remove, secure-store availability | Shared validation/review/write lifecycle; GCP key entry is now masked as well as individual secrets. No cloud credentials or live provisioning used in verification. |
| Agents; AgentPresetEditor; McpPresetBindings | Create/edit/remove presets, revision conflicts, engine/model defaults, tools, MCP bindings, budgets, sandbox and approvals | Installed-engine selector; human-readable common-tool choices; exact names/toolsets and model name remain under Advanced; budget validation and deny/inherit semantics retained. Preset tests cover explicit tools, limits and revisions. |
| Plugins & skills; ExtensionManager; BrowserSkillEditor | Skill-source CRUD/history/publish, immutable bundles/import, permission previews, registries, credentials and rotation | Existing review/publish and secret boundaries retained; common draft/write lifecycle and destructive review applied; exact manifests remain disclosed as Advanced. Existing extension/editor tests. |
| Tools & connections; McpManager; McpConnectionForm; McpCredentialForm | Create/edit, project allowlist/ownership, inspect tools, connect/trust, OAuth, credential add/remove, tool calls, delete | Auto-generated connection key with Advanced override; executable/arguments accept one per line while preserving JSON compatibility; write-only masked credentials and empty tool denylist retained; validation and review shell. Existing MCP scope/credential tests. |
| Memory; MemoryEditor | Create/edit/include/exclude/delete/export, provenance, retention, revision conflicts | Required create fields and bounded retention; unchanged-form write cleanup, protected failed/conflicting edits, destructive cancellation. Real-host edit/save and unit conflict checks. |
| Hooks | Project-scoped create/enable/disable/delete and execution history | Executable plus one argument per line; expert JSON still accepted; required folder/command and 1–60 second limit match the host; project scope selector makes these flows reachable from Settings; execution consequence remains visible. |
| Automations; ScheduleManager; ScheduleBrowserPermission | Create/edit/preview/run/pause/resume/archive; goals/delegations; bounded unattended browser scope | Existing explicit unattended consent, DST/timezone preview, revisions and idempotency retained; shared validation/review/write lifecycle; important grant/security fields stay visible. Existing schedule/browser tests. |
| Runners; Runners; MachineRunners; CloudMachines | Container enrollment, machine/cloud configuration, catalogs, explicit provisioning, job execution/results, stop/teardown | Human-readable project names; command accepts one argument per line and expert JSON; common validation and destructive review; cloud cost/network consequence screens retained. No real runner or cloud operation during this task. |
| Access & sessions; ManagedPairingForm | One-use pairing/revoke, device sessions, people/roles/project access, identity bindings, enterprise providers, recovery/migration/audit | Pairing QR can carry the authenticated private CA; host IDs move behind Advanced; existing trusted-execution value is preserved when editing a role; assertion evidence is masked; revocation reviewed; LAN HTTPS setup/lifecycle guidance added. |
| IdentityGroupsManager | Organization/group CRUD, membership, provider mapping, project grants, revision conflicts | Existing named selectors and security consequences retained; shared successful-write lifecycle and destructive review. Existing identity-group tests. |
| AuditRetentionManager | Retention edit/save/prune and explicit irreversible-prune consent | Existing consent and limits retained; shared numeric validation and pending-write cleanup. |
| SessionPolicyManager | Read/edit/refresh, conflict review, idle/absolute lifetimes and session expiry | Existing version/consequence semantics retained; shared draft/write lifecycle and validation. |
| HostServiceSettings | Inspect/install/start/stop/uninstall and explicit tunnel restoration | Existing exact-target confirmation retained; scoped settings surface and common draft protection. Tests do not install a live service. |
| Appearance; AppearanceChoices | Dark/light/system | Same controls are reused inline in canonical Settings, so a second settings dialog is unnecessary. Immediate local persistence remains. |
| Keyboard shortcuts; Keybindings | Edit/reset/conflicts | Canonical category, existing immediate per-account persistence, conflict behavior retained. |
| Safety & Auto Review; SafetyManager; GeneralPolicyManager | Evaluate/activate/disable a reviewer; inspect decisions; remember/edit/revoke browser/coding policy | Named provider selector and billing/security consequences retained; shared validation, destructive review and successful-write lifecycle. No paid evaluation run. |
| Contextual SessionSettings / conversation settings | Session engine/model/preset/extensions, run budget, execution location, reasoning, voice, title/group | Contextual controls remain with chat; unsaved run-budget close is guarded. Existing session controls and voice tests remain. Sidebar grouping preserves the execution project. |

Exact provider trust configurations, registry manifests, diagnostic records and uncommon tool IDs remain accessible for expert users. They are not discarded or converted into guessed model/provider identifiers. Provider-specific external IDs that users must obtain from their provider stay explicitly labeled.

## Chat source integration

See `desktop/workspace/third-party/AI_ELEMENTS_PROVENANCE.md` for the actual copied package/source and license. Message/Markdown/code, tools, prompt input, attachments, approval and clarification components adapt existing TermX state. No AI backend migration or paid provider request was needed.

## Verification and evidence

- Full final workspace suite: 325 tests passed across 77 files. The production TypeScript/Vite build passed. Exact bounded commands and platform limits are recorded in the verification report.
- Real-host rendered proof: `tests/workspace_settings_tls_e2e.py`; widths 1440, 768 and 390, both light and dark; focus trap, chip overflow, manager URLs, all 14 categories, isolated TLS configuration save, memory edit/save, discarded-navigation refusal, Escape and destructive cancellation.
- Axe 4.10.3: zero violations in the tested 1440px and 390px settings states for WCAG 2 A/AA and 2.1 AA tags. This is a bounded scan, not a whole-product accessibility certification.
- Screenshots/report: `docs/evidence/local-tls-settings/`. They use a temporary host account and state. No real credentials or user conversations are included.
- Native Tauri and iOS/Android installed execution, physical touch/keyboard/safe-area behavior and platform-specific root trust remain release gates, as documented in `local-network-tls.md`.
