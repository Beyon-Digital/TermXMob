# TermX — Native Engines, Extension Discovery, and Custom Agents
## Desktop implementation-agent kickoff

Prepared: 3 October 2026. This is an execution brief: inspect the current repositories, produce the implementation plan, then implement and verify it. Do not stop after producing planning documents.

## 1. Mission and authority

You are the primary desktop implementation agent for:

- `Beyon-Digital/TermXMob`: host/backend, runtime, persistence, permissions, process management, and desktop packaging.
- `Beyon-Digital/termx-app`: chat, settings, custom-agent editor, tools/connections, and client integration.

Build TermX into a UI and host gateway that can run the actual Codex, Devin, Grok, and Claude agent engines. Add automatic discovery and relevant use of skills, workflows, custom agents, MCP connections, and ACP engine definitions, centered on the host user's `~/.agents/` and trusted project configuration. Implement Markdown-backed custom-agent CRUD and real tool/toolset enforcement, including an MCP client with OAuth and Dynamic Client Registration compatibility.

Preserve the current TermX internal engine as an available engine. Reuse upstream SDKs and narrowly scoped, license-compatible implementation components instead of rebuilding their agent loops, protocol stacks, or mature utilities.

You may inspect both repositories and relevant local configuration, edit project files, create feature branches and reviewable commits, and run bounded local checks. Follow the repositories' contribution instructions for PR creation. Do not merge, deploy production, overwrite unrelated work, change machine-wide security, import secrets into Git, or enable paid overages. Use temporary HOME/config directories for mutation tests. A user's real login or external consent must remain a user action.

Make low-risk engineering decisions and continue without asking for a second planning approval. When credentials, vendor approval, a compatible license, or unavailable infrastructure blocks one integration, record that exact blocker and continue independent work. Never label a mock-only integration production-ready.

## 2. Locked architectural boundaries

### Engine is not model

Keep these separate in types, settings, storage, and UI:

- **Engine/harness:** owns the agent loop, native tools, planning, context management, native session, and its own supported subagents.
- **Model/provider:** an inference selection available to an engine. A compatible model endpoint is not proof of a compatible engine.
- **Custom agent:** a versioned instruction/capability profile applied through the chosen engine's supported customization surface.
- **Skill/workflow:** reusable task guidance or explicit workflow steps. Neither is an authorization grant.
- **MCP connection:** access to external tools/resources/prompts.
- **ACP connection:** communication with an agent engine. ACP and MCP are not interchangeable protocols.

Default integration targets, subject to local version verification:

| Engine | Preferred interface | Required decision |
|---|---|---|
| Codex | `codex app-server` | Use its structured native protocol, not a model proxy or terminal scraping. [S1] |
| Devin CLI | `devin acp` | Use a shared ACP client plus a small Devin adapter. Verify the installed command and advertised capabilities. [S2] |
| Grok Build | `grok agent stdio` | Use that same ACP client with Grok-specific launch/auth configuration. [S3] |
| Claude | Official Claude Agent SDK | Choose the supported SDK appropriate to the host stack; do not reimplement Claude Code's loop. [S4] |
| TermX internal | Adapter over existing runtime | Retain existing conversations, tools, policies, and recovery. |

Devin Cloud is a separate execution location and API, not an interchangeable local filesystem engine. Do not quietly move laptop work to cloud execution.

Engine authentication and MCP authorization are separate systems. Follow each vendor's current permitted integration/authentication path for TermX's distribution model. Do not extract subscription tokens, impersonate a first-party client, or assume a user's paid plan authorizes a commercial/hosted wrapper. Record any vendor approval or app-registration requirement as a real blocker, while implementing and testing permitted paths. [S1, S4]

One authoritative agent loop per running engine session. TermX may supervise processes, schedule explicit workflow steps, provide scoped tools, and render events; it must not wrap an external engine in another speculative planning/tool loop. Do not add a router-model inference call to every message.

The native engine remains authoritative for its session. TermX stores an index, display projection, engine identifiers, configuration snapshots, and replay cursor. Never edit proprietary/native session files to manufacture resume. Switching engines is an explicit new-session handoff, not transparent state migration.

Keep ordinary human terminal behavior separate from agent execution policies. Diagnose the reported “Devin works in the laptop terminal but not TermX” issue with a reproducer; do not assume its cause or disable sandboxing to hide it.

## 3. Start with fresh repository and environment reconnaissance

Locate both checkouts. Record repository paths, current branch/HEAD, remote default branch/HEAD, worktrees, uncommitted changes, and relevant open branches/PRs. Fetch before making decisions, but do not reset or discard work. Do not mistake an unmerged PR for shipped main.

Read applicable `AGENTS.md`, repository instructions, prior TermX handoffs, and existing design/engineering maps. Reconcile previous agent-v2, sandbox, custom-agent, and redesign work as completed / reusable / partial / missing / blocked. Apply only the relevant audit, implementation-planning, backend, kickoff, frontend, and QA workflow steps; do not force an unrelated full workflow catalog.

Preliminary paths already observed, which you must re-check rather than assume unchanged:

- Backend: `src/termx/agent/manager.py`, `store.py`, `providers`, `runtime`, `tools`, `policies`, `secrets`; `src/termx/app.py`, `sessions.py`, `terminals.py`, `sandbox/`; `desktop/src-tauri/src/backend.rs`.
- Client: `src/lib/custom-agents.ts`, `src/lib/host-sync.ts`, `src/lib/api.ts`, `src/lib/types.ts`, `src/app/settings/agents.tsx`, and `src/components/chat/conversation-view.tsx`.
- Existing planning: `plans/agent-v2/` and any later canonical handoffs.

A preliminary source read found an existing `AgentManager` with tools, scheduling, context, recovery, and policies; custom-agent API fields already include instructions, provider/model, tools, limits, approval mode, and sandbox profile. Client search found agent settings and host-sync code. This is not a clean-slate CRUD build. Inspect behavior, including any device-local/host split, before migrating. [R1–R3]

Inventory installed engine executables, absolute paths, versions, supported commands, authentication status without printing credentials, configuration roots, and documented session/customization APIs. Bound executable probes. Never execute arbitrary binaries merely because an untrusted manifest mentions them.

Run existing tests, type checks, lint, and a representative chat/terminal smoke test. Record existing failures separately. Capture a small baseline for startup, event delivery, memory, and reconnect.

Create one canonical directory, preferably `plans/engine-extensions/`, containing:

`analysis.md`, `plan.md`, `tech-specs.md`, `tasks.md`, `compatibility-matrix.md`, `reuse-audit.md`, `security.md`, `migration.md`, `verification.md`, and `sources.md`.

Each task needs a stable ID, dependencies, owner/write scope, acceptance criteria, status, and evidence. Refresh official documentation and installed SDK schemas before implementation. Record access date and resolved versions. Distinguish stable features from proposals and experimental APIs; in particular, do not assume ACP v2 documentation means the installed agents support it. [S5–S6]

## 4. Mandatory reuse investigation

Prefer, in order: an official maintained SDK or native interface; an existing compatible project dependency; a narrow reusable upstream package; a small audited source adaptation; new code only for the remaining TermX-specific glue.

Evaluate concrete components from these primary-source starting points:

| Candidate | Evaluate for reuse, not assumed superiority |
|---|---|
| OpenAI Codex app-server | Protocol types, client patterns, event/session/approval handling. |
| Official ACP SDKs | Async client, schema validation, transport and bidirectional request handling. Python SDK exists; assess version coverage before choosing it. [S7] |
| Official MCP SDKs | Client transport, lifecycle, tool schemas, and authorization helpers. [S11] |
| OpenCode | Agent/config loading, permission and session/event patterns, and any separable MCP utilities. [S12] |
| Pi | Small agent/runtime and session/extension primitives useful to the **internal** engine. Verify the current upstream location and package boundaries. [S13] |
| Goose | Extension/MCP integration and workflow/session patterns worth extracting or adapting. [S14] |

Do not install every candidate. Inspect source first; benchmark relevant alternatives where a performance claim matters. Repository popularity is not performance evidence.

For every adopted dependency or copied component, record repository URL, pinned version/commit, exact files/component, license and notices, transitive obligations, security/telemetry/install behavior, reason for adoption, changes, tests, and update strategy. Preserve required attribution and add notices/SBOM entries where appropriate. Do not copy code of unknown or incompatible licensing; do not decompile proprietary engines. SDK availability does not make an entire product open source.

Keep external-engine integrations thin. Improve the internal engine only where a measured, reusable component serves this scope; do not turn this task into a total framework rewrite or import a second architecture just to reuse a small utility.

## 5. Engine gateway, sessions, and events

Introduce an engine boundary above—not disguised inside—the existing model-provider layer. Define and freeze contracts before parallel implementation. Adapt names to the repository's conventions.

Required concepts:

- `EngineDescriptor`: identity, executable/SDK version, installation/auth state, execution location, transport, supported protocol versions.
- `EngineCapabilities`: native features and their enforcement/availability status, not optimistic booleans inferred from a brand name.
- `EngineSessionBinding`: TermX conversation, machine/project/worktree, engine account scope, native session/thread and turn IDs, profile revision, extension snapshot, connection identity, lifecycle, and replay cursor.
- `EngineAdapter`: detect/probe, authenticate through supported flows, create/attach/resume, send, stream, cancel, approvals, close, and capability-gated steering/fork/list/configuration.
- `EffectiveRunConfiguration`: selected engine/model, immutable agent revision, selected tools/toolset expansion, skills/workflow references, approved MCP bindings, sandbox and approval envelope.

Define a versioned event envelope with stable IDs, sequence/cursor, session/turn correlation, timestamp, event type, provenance, and a bounded redacted payload. Support text/tool deltas, structured results, status, plans, diffs/artifacts, approvals/elicitation, usage when reported, errors, and completion. Retain unknown upstream event types safely without breaking the client. Do not fabricate hidden reasoning or usage statistics that the engine does not expose.

Implement transport using supported SDKs/framing: stdio protocol traffic on stdout and diagnostics on stderr. No PTY for a machine protocol, no ANSI scraping, no newline-regex parsing of human console output, no OpenAI-compatible proxy masquerading as the engine.

Build bounded queues, backpressure, child lifecycle management, request correlation, timeout/cancellation cleanup, and clear protocol errors. Support events outside an active prompt where the negotiated protocol permits them. Correlate completion using real protocol state—not an arbitrary “no text arrived recently” timer.

Reconnect must replay without duplicate display items or duplicate tool execution. Persist critical transitions and native references. Reconcile native replay against TermX's projection. On an uncertain side-effecting in-flight operation, show an explicit uncertain/reconciliation state rather than automatically resending it. Do not claim distributed exactly-once guarantees.

Serialize competing turns for a session unless supported steering/queueing explicitly permits otherwise. Multiple devices may observe the same session; approvals and mutations must have a single winning decision with actor attribution. Reconnect is not a new task. Closing a view, detaching, cancelling a turn, stopping an engine, and deleting history must be distinct operations.

For ACP, implement negotiated client callbacks actually advertised, including safe filesystem access, permissions, terminal lifecycle, and elicitation where supported. Use existing scoped filesystem/process services. Never advertise terminal or filesystem support while returning placeholder successes. Gate newer methods by negotiated capabilities. [S5]

## 6. Discovery rooted in `~/.agents/`, without inventing a universal format

`~/.agents/skills/<name>/SKILL.md` and project `.agents/skills` are cross-client conventions; Agent Skills defines the skill package, not a universal storage/configuration schema for every extension type. GitHub custom-agent Markdown and native MCP/engine configuration have their own semantics. Support compatibility readers rather than pretending everything is one standard. [S8–S10]

Inspect the user's existing layout first. Proposed **TermX conventions**, not claims about external standards:

```text
~/.agents/
  skills/<skill-id>/SKILL.md          # Agent Skills-compatible packages
  agents/<agent-id>.agent.md         # TermX custom-agent profiles
  workflows/<workflow-id>.md         # TermX versioned workflow definitions
  toolsets/<toolset-id>.json         # Explicit reusable tool selections
  mcp/<connection-id>.json           # Connection metadata and secret references
  acp/<engine-id>.json               # Approved ACP launch descriptors
```

Support analogous trusted project `.agents/` roots and explicit additional roots. Do not silently relocate or overwrite existing files. Keep credentials, OAuth tokens, native sessions, caches, approvals, and audit state out of these shareable definition files.

Add format adapters for documented locations when actually present: project/root `AGENTS.md`, `.github/agents/*.agent.md`, applicable `.claude/skills`/agents, native Codex/Devin/Grok configuration, and supported MCP configuration files. Verify each path/schema in current docs. Do not recursively scrape every dot-directory or dump full home/config directories into model context.

Discovery rules:

1. Resolve roots on the selected **host machine**, not on the phone/browser. Preserve user/project/organization/bundled provenance.
2. Read metadata lazily; bound depth, count, bytes, parsing time, and YAML features. Do not evaluate templates or execute hooks during discovery.
3. Canonicalize paths; detect symlink cycles and out-of-root targets. Allow explicitly trusted shared skill symlinks, not silent sandbox escape.
4. Use stable, scope-qualified IDs and content hashes. Deduplicate the same source reached twice, while keeping distinct same-named resources identifiable. Never merge arbitrary bodies or silently discard native collision semantics.
5. Define deterministic TermX selection precedence: explicit qualified selection first, then trusted project-specific resolution, then user/bundled fallback. Admin/security denials always constrain the result. A name collision must be visible.
6. Cache indexes per machine/user/project. Debounced watchers or bounded polling should invalidate only affected entries. Do not rescan disk per token or rebuild the entire catalog per turn.
7. Report malformed, missing, unsupported, disabled, conflicting, and untrusted entries individually. One bad file must not disable the whole registry.
8. Separate `discovered`, `trusted`, `enabled`, `authorized`, `compatible`, and `active` states. Finding an MCP/ACP definition must never auto-install a package, spawn a process, launch OAuth, or grant privileges.

Native loaders can execute hooks or load additional tools outside our registry. Audit that path too. Use documented configuration isolation/overrides, not cosmetic filters. Do not point an engine at unrestricted user/project configuration while claiming TermX's narrower profile is enforced.

## 7. Automatic use of skills, workflows, agents, and connections

Deliver automatic **relevant use**, not merely a settings list.

For each turn, the selected engine should see a compact, permission-filtered catalog. Prefer its native skill/custom-agent mechanism where compatible. Otherwise provide a small, scoped discovery/activation tool or supported instruction extension. Never add the same skills twice through native loading and TermX injection. [S8–S9]

Explicit user selection takes precedence. Implicit selection should activate the minimum relevant set using descriptions, context, and declared dependencies; honor “manual only,” disabled, excluded, and version-specific invocation flags. Do not activate every workflow simply because it is installed. Prefer the engine's existing selection ability instead of a separate expensive router.

Load full instructions only on activation and resource files only when needed. Resolve relative paths against the actual package root. Preserve a durable activation record containing source ID, revision/hash, scope, reason, dependencies, and activation mode. “Available,” “activated,” and “actually executed” must be distinct observability states.

Pin run inputs. Files changed mid-run affect later runs, not an invisible mutation of the current agent. Show available updates; apply user-requested changes only through supported safe configuration boundaries or a new run. Preserve compact activation/workflow references through reconnect and compaction without rewriting native engine internals.

Workflow support must handle both instructional Markdown and explicitly structured, versioned workflows. For structured workflows, implement validated steps, dependencies, inputs/outputs, human gates, cancellation, resumable checkpoints, and bounded retry rules using existing runbook facilities where suitable. Reject unknown executable workflow formats rather than interpreting arbitrary Markdown as shell commands.

Workflows may coordinate separate agent sessions at explicit boundaries; they must not become a second hidden reasoning loop around each external-engine turn. Side effects require current authorization even when a previous workflow step was approved.

Allow auto-selection/delegation of enabled custom agents where the engine supports it. Bound concurrency, recursion depth, total tasks, runtime, and cost. Keep parent-child links and separate sessions/worktrees. Delegate a scoped objective and relevant context, not all history or credentials. Child authority cannot exceed the parent's effective envelope. Unsupported native subagents should be reported; do not silently substitute another engine or pretend delegation happened.

Previously approved MCP connections can be reused automatically when the current agent/tool policy allows them. Discovery of new servers, authentication, installing an ACP engine, or widening tools/scopes requires the appropriate trust/consent step. A skill's dependency declaration may request a connection; it cannot authorize it.

## 8. Markdown-backed custom-agent CRUD

Extend the current backend/client CRUD, replacing conflicting persistence paths through migration rather than creating another island of settings.

The canonical definition must be an editable Markdown file with safe YAML frontmatter. TermX's database is its searchable projection plus durable run snapshots, not a competing mutable definition. Use the host API for file mutation. Existing local storage may cache views and pending drafts, but cannot silently become a second authority.

Support create, list/search, inspect, edit, duplicate, rename, enable/disable, scoped override, import/export, and delete with dependency checks. Distinguish disabling a profile from deleting it, and deleting a profile from deleting historical sessions. Preserve existing custom-agent IDs and conversation references during migration.

Use an editor form and a raw Markdown editor over one parsed model. Display validation, effective tools, compatibility, source location, revision, and unsaved/conflicting changes. Preserve unknown vendor frontmatter for round-tripping without executing it. Mark incompatible fields honestly during import/export.

Explicitly recognize documented Copilot `mcp-servers` entries rather than treating them as inert unknown fields: validate them, map them to scoped TermX connection definitions, preserve supported tool filters and secret references, and require trust/authentication before starting them. Distinguish GitHub cloud and IDE profile semantics. Detect inline secrets during import; do not echo them into previews or export them into shareable files. Keep unsupported fields with diagnostics, not a false claim that they are active. [S10]

Proposed profile shape below is **TermX's schema**, not something all native engines consume verbatim. Validate and refine it during planning. Keep a useful Copilot-compatible frontmatter subset and namespace TermX-specific metadata:

```yaml
---
name: repository-reviewer
description: Review code changes and report concrete defects.
tools:
  - read
  - search
x-termx:
  schema-version: 1
  id: agent.repository-reviewer
  engine: codex
  model: inherit
  enabled: true
  toolsets:
    - toolset.review
  deny-tools:
    - execute
  skills:
    mode: auto
    include: []
    exclude: []
  workflows: []
  mcp-connections:
    - connection-id: connection.example-review
      tools:
        - review_lookup
  delegation:
    enabled: false
    allowed-agents: []
  policy-profile: workspace-review
  approval-mode: standard
---
Review the requested changes. Cite file locations and explain impact.
Do not edit files or publish results unless the user authorizes that work.
```

The example connection/toolset IDs are placeholders for valid local registry objects, not real server identities or tools. Do not ship it as an enabled profile with unresolved dependencies. `policy-profile` is a reference to an approved policy, not authority encoded by a Markdown author.

Specify import semantics explicitly. In GitHub's format, omitted tools enable all available tools and `tools: []` disables all tools; do not collapse those states. Preserve imported intent while requiring a review of broad/wildcard access before enabling it. For newly created TermX profiles, require deliberate tool selections rather than defaulting to everything. [S10]

Define toolset expansion, unions, explicit denials, unknown names, wildcards, and revision locking. Denials win. Unknown or empty resolved selections must not fall back to all tools. New tools arriving on a server must not silently enlarge a previously approved restricted profile.

Implement safe writes: validation before mutation, expected revision/content-hash preconditions, same-filesystem atomic replacement, backups or recoverable history, file permissions, and watcher feedback-loop suppression. On external edits, show a conflict instead of last-writer-wins data loss. Restrict create/rename/delete destinations to authorized roots and protect against traversal/symlink races.

Import existing DB/device definitions once, preserve original data and IDs, and provide rollback. Make offline edits visibly pending; do not display “saved to host” when it is only cached on a device. Do not write agent exports into another engine's global configuration without explicit user action.

## 9. Effective tools, MCP client, and optional scoped gateway

Build one namespaced registry across TermX tools, engine-native tool metadata, MCP tools, and approved delegation/workflow operations. Stable identities must include source/connection, not just a display name.

Compute effective permissions as requested agent selections intersected with authorized connections, current user/project policy, active session restrictions, and what the engine can actually enforce. Treat shell/network/filesystem authority separately from which tool names are displayed.

A disabled checkbox or instruction saying “don't call this tool” is not enforcement. For each engine record whether control is native-enforced, gateway-enforced, OS-enforced, advisory-only, unsupported, or unverified. A strict profile that cannot be enforced must not launch under a falsely restrictive label. General engine availability can remain usable with accurate authority disclosure.

A shell with unrestricted credentials/network can bypass a hidden MCP tool. Do not claim tool filtering prevents that. Use effective sandbox/credential/egress boundaries where strict capability restriction is required; otherwise disclose the limitation and block the strict profile.

Implement an actual MCP **client** using the maintained SDK: initialization/version negotiation, local stdio and current HTTP transport, pagination, tool discovery and calls, cancellation, errors, structured outputs, and catalog-change handling where supported. Resources/prompts and optional server-initiated capabilities should be supported deliberately or rejected clearly. Do not accidentally enable MCP sampling, elicitation, or arbitrary server requests without policy and budgets. [S11]

Connection definitions contain endpoint/command/args, transport, scope, auth method, credential references, enablement, and approved metadata—not raw secrets. Store owner/project/account bindings. Separate safe connectivity checks from paid or mutating tool execution. Starting an untrusted stdio server is code execution and needs trust; a URL text field is not permission to connect anywhere.

Pool live connections only within a compatible trust/account/auth boundary. Cache tool schemas with version/fingerprint and invalidation. Keep server namespacing and schema validation on both discovery and invocation. Handle timeouts, unhealthy servers, cancellation, and revoked authorization independently.

For the internal engine, use existing tool registration/execution seams. For external engines, prefer supported per-session MCP configuration. Where filtering, shared auth, or audit requires it, build a narrowly scoped TermX MCP gateway using the SDK:

`engine -> session-scoped TermX tool endpoint -> authorized upstream MCP client`

Keep upstream OAuth tokens inside the host credential broker. Give the engine only a local/session-scoped credential or stdio bridge. Bind gateway requests to the correct conversation, user, project, and approved upstream identity. Never expose all user's connections to every session. Native direct connections need the same effective enforcement verification; do not silently create both paths and duplicate tools.

Deduplicate shared connections without sharing another account's credentials. Retry safe discovery operations with backoff, but never blindly retry non-idempotent tool calls after an ambiguous timeout. Preserve the uncertain result for reconciliation.

## 10. OAuth, client registration, and DCR compatibility

Implement the **MCP client role**; this is not a request to create a new general OAuth authorization server.

Re-check the negotiated MCP spec and SDK support. The documentation snapshot used for this brief resolves to MCP `2026-07-28`: it retains DCR for compatibility and deprecates it in favor of Client ID Metadata Documents (CIMD). Therefore ship DCR support, but not a DCR-only client. [S15–S16]

Follow validated discovery and the documented registration selection order: existing issuer-bound registration, supported/configured CIMD, supported DCR fallback, otherwise explicit client-information setup. An advertised feature with missing local prerequisites is a setup state, not permission to fabricate a client ID. DCR support is optional on servers. [S16]

Requirements for the implementation and test fixtures:

- Resolve protected-resource metadata and authorization-server metadata through current supported discovery rules. Do not guess `/authorize`, `/token`, or `/register` endpoints.
- Bind pending authorization to initiating user/device, host, connection, issuer, resource, and redirect. Use the maintained OAuth implementation for code exchange and PKCE; add the missing state/binding controls at the application boundary.
- Use PKCE S256 and CSRF state; verify advertised PKCE support, issuer and response binding, exact registered callbacks, and resource audience according to the negotiated spec. Never use a static desktop-embedded client secret as proof of confidentiality. [S15, S17]
- Select least-privilege scopes and make step-up consent visible. Distinguish authentication from authorization and an authorized server from permission to call every tool.
- Persist issuer-bound client registration separately from resource/account tokens. Secure refresh, rotation, expiry, logout, revocation where supported, and bounded recovery. Never log codes, verifier, access/refresh tokens, authorization headers, or credential-bearing URLs.
- DCR must use the authorization server's advertised registration endpoint and appropriate native/web application metadata for the actual callback architecture. Surface registration policy/redirect failures; do not silently weaken the flow. [S16]
- CIMD requires a real configured HTTPS metadata document. Add configuration, validation, a template, and deployment instructions; do not pretend localhost is a publicly reachable HTTPS client identity. Missing hosting must leave CIMD explicitly unconfigured while other supported registration paths remain usable.
- Keep stdio authentication separate: provide approved, minimal environment/secret injection rather than attempting an HTTP OAuth handshake with a subprocess.
- Keep upstream and gateway tokens separate, audience-bound, and least-privilege. Never forward an engine login token as an MCP access token. [S17]

Remote/mobile authorization needs special care: `localhost` in the phone's browser is not the laptop. Implement a supported desktop-browser completion path and accurate remote-client status. If a safe HTTPS callback/deep-link architecture already exists, use and test it. Otherwise show “Complete sign-in on host” rather than inventing a public callback or leaking tokens through client messages.

Apply SSRF controls to discovery, redirects, token/registration endpoints, and tool-server configuration. Reject metadata/cloud link-local targets and unapproved private-network destinations; permit explicitly trusted local/LAN MCP configurations with scoped exceptions. Revalidate redirect destinations and DNS changes. Do not break legitimate multi-domain authorization-server discovery by assuming everything must share one hostname.

Keep browser consent and credential entry out of model-controlled tools. A request to “connect an MCP” may propose and prepare configuration; it cannot synthesize user consent or approve broader permissions for itself.

## 11. Client UX and API integration

Deliver usable end-to-end UI, not backend endpoints alone or decorative settings:

1. **Engines:** detected/path/version/auth state, connection test, supported capabilities, engine and model selection, location/authority, and actionable failures. Do not promise a model not offered by that engine/account.
2. **Agents:** existing settings extended to create/import/duplicate/edit/delete, form/Markdown modes, source scope, instructions, toolsets/tool search, skills/workflows, MCP connections, delegation, and effective permission preview.
3. **Skills and workflows:** source/trigger/trust/compatibility states, explicit invocation, relevant auto-activation controls, dependencies, active-run provenance, and revision conflicts.
4. **Connections:** stdio/HTTP setup, auth/registration modes, connect/disconnect, sign-in completion, refresh/revocation state, tools browser, and per-agent authorization.
5. **Chat:** native session identity, streamed activity and tool results, approval/elicitation cards, selected profile, active extensions, cancellation, reconnect, and supported steering. Unsupported controls must be hidden or explained, not fake.

Prefer compact progressive disclosure. Reuse the approved TermX design system and existing screens rather than redesigning navigation again. Update any existing canonical JSON screen/component map and API schemas alongside implementation. Cache design references when needed rather than repeatedly refetching or guessing them.

Audit the installed frontend stack first. Preserve existing data/state patterns; do not introduce a parallel ad hoc cache or a new GraphQL/Relay migration for this task. Where Relay already exists, follow its canonical IDs, fragments, connections, and hooks. For Expo, read official documentation for the **installed SDK**; if it is SDK 57, use SDK-57-compatible Expo UI/native wrapper and navigation APIs, not remembered deprecated APIs or SDK-58-only ones.

Expose API validation/compatibility errors clearly. Check each request's auth/device scopes and host ownership. Paginate catalogs, agents, tools, and history. Treat all engine/MCP Markdown, HTML-like text, URLs, and tool output as untrusted display content.

Verify phone/tablet/desktop, keyboard behavior, light/dark mode, and existing terminal flows. A form must demonstrate a persisted profile and a real run using that profile.

## 12. Environment, security, and performance guardrails

Construct launch environments deliberately. Compare engine resolution between a native terminal, CLI-started host, and GUI-started host using redacted diagnostics: binary path/version, cwd, HOME/config resolution, runtime dependencies, and presence—not values—of credential variables. Restore necessary host compatibility using a bounded, trusted shell/environment mechanism where justified; do not inherit every secret into every engine or MCP server.

Do not source untrusted project startup scripts as part of detection. Do not rewrite the user's dotfiles or weaken ordinary terminal/sandbox behavior merely to make a smoke test pass. Handle any engine/sandbox incompatibility explicitly and separately from shell-integration errors.

Keep credentials in the existing secure store after auditing its guarantees. Apply least-privilege filesystem permissions and identity isolation between projects/accounts. Do not expose raw engine transports on a public port; retain the authenticated TermX gateway, safe origins, and encrypted remote transport. Approval persistence must remain scope-bound and invalidatable; broad permissions cannot be created by a skill/agent file edit.

Instrument cold/warm engine start, gateway-added event latency, first-response latency separately from model latency, skill-index/activation cost, tool discovery/invocation, reconnect replay, process count, memory, and catalog/prompt size. No tokens or raw secret-bearing inputs in telemetry. Keep diagnostics local/redacted by default.

Set explicit budgets from the recorded baseline **before** implementation and keep them in verification. Test a synthetic 1,000-skill catalog, large tool catalogs, long histories, event bursts, and repeated connect/cancel/disconnect cycles. The warm path should use cached metadata and lazy bodies, not full rescans or whole-catalog prompt injection. Use persistent bounded clients, safe read concurrency, and serialized conflicting writes. Do not claim improved performance without reproducible measurements.

## 13. Dependency order and parallel work

Execute vertical slices in this order. Share a small progress report and evidence after each phase; do not wait for another approval between ordinary phases.

- **P0 — Audit and contracts:** baseline, fresh docs, reuse decision, trust model, engine/extension/config/event schemas, migration design, acceptance fixtures. One owner controls shared schemas and migrations.
- **P1 — Internal adapter and Codex slice:** preserve internal runtime; implement gateway/session binding and minimal real chat wiring; demonstrate create → stream → approval → disconnect/reconnect → continue native session → cancel.
- **P2 — Shared ACP and Claude:** ACP SDK/client, real callbacks, Devin and Grok thin adapters; Claude SDK adapter. Conformance tests first, then supported live smoke tests. No copied engine loops.
- **P3 — Discovery and file-backed agents:** metadata index, compatibility readers, trusted activation, durable revisions, Markdown CRUD and reversible migration. Integrate internal and Codex first, then each verified external customization surface.
- **P4 — MCP and auth:** direct client, connection lifecycle, effective tool/toolset enforcement, OAuth/CIMD/DCR, scoped gateway where necessary. Local authorization-server/MCP fixtures remove reliance on third-party credentials for correctness tests.
- **P5 — Workflow/delegation integration and complete UI:** automatic relevant use, profile editor, tool selection, connections, approvals, capability-aware chat, and clear blockers.
- **P6 — Hardening and release evidence:** security/negative tests, packaged GUI launch, performance comparison, native-session recovery, migration/rollback rehearsal, docs, reviewable commits/PRs.

After P0 contracts are frozen, independent adapter modules, discovery parsing, OAuth fixtures, and client components can run in parallel with explicit file ownership. Limit parallel agents to the existing configured budget. Use separate worktrees/branches for overlapping repository writes. No two agents may concurrently edit shared schemas, migrations, central store/manager, or the same UI integration file. Integrate behind tested interfaces in dependency order; clients may develop against fixtures but must later pass against the real host.

Give each delegated implementation agent its task IDs, allowed files, interface version, constraints, tests, and completion evidence. Inspect its diff and run integration checks yourself; do not accept “all phases done” as evidence.

## 14. Required acceptance tests

Use deterministic protocol fixtures plus live local smoke tests. Mark tests distinctly as passed, failed, skipped with reason, or externally blocked.

### Engine/session behavior

- Internal engine regression suite remains passing; historical conversations/custom agents still resolve.
- Each implemented engine has launch/probe, authentication-state, create/send/stream/cancel, error, and cleanup coverage. Test resume only where supported and surface unsupported behavior correctly.
- Real Codex session continues after client reconnect; losing the UI does not duplicate a prompt or erase state.
- Two clients reconnect or approve simultaneously without duplicate turns/side effects. Crash during a side-effecting tool call enters reconciliation rather than blind replay.
- ACP handles bidirectional requests, denied permissions, file boundaries, terminal output/cleanup, malformed/oversized frames, unsupported protocol versions, and unavailable executables.
- A native engine's undisclosed tool/configuration authority cannot bypass a supposedly strict TermX profile unnoticed.

### Discovery and activation

- A trusted `~/.agents/skills/<id>/SKILL.md` is discovered and a matching request activates it without manual attachment. An unrelated request does not activate it.
- Manual-only/disabled skills remain respected; explicit selection resolves the correct qualified source.
- Project/user name collisions, shared symlinks, cycles, invalid YAML, oversized files, external edits, and missing dependencies produce bounded, clear behavior.
- Discovery alone executes no script, installs no package, opens no OAuth flow, and grants no new access.
- Native-loader and TermX activation do not double-inject a skill; active revision and workflow progress survive reconnect.
- Untrusted repository instructions cannot enable servers, auto-approve execution, alter security policy, or export credentials.

### Custom agents and tools

- Form create → Markdown file → catalog → real run → edit → duplicate → disable → delete works end to end.
- External file edits appear in UI; concurrent edits cause a visible conflict; failed writes preserve the prior file.
- Migration retains IDs, instructions, policy, references, and usable rollback. Device drafts cannot overwrite newer host definitions silently.
- Omitted tools, explicit empty tools, wildcards, toolset expansion, explicit denial, unknown tools, and new server tools all have tested, documented semantics.
- Disabled tools are blocked at the actual enforcement boundary. Test direct/native execution and shell-mediated bypass where relevant, not just hidden schema entries.
- Import/export preserves supported fields and reports lossy/incompatible mappings; it does not alter another engine's global files automatically.

### MCP and authorization

- Local stdio and HTTP MCP discovery/call/cancel work. Catalog pagination and change invalidation preserve approved tool restrictions.
- Mock OAuth tests cover pre-registration, CIMD, DCR fallback, no registration support, native redirect rejection, and unconfigured CIMD hosting.
- Test state/issuer/PKCE mismatches, malformed metadata, token expiry, refresh rotation, insufficient scope, user cancellation, revocation, wrong audience, wrong account/project, redirects, and SSRF/DNS changes.
- Reconnect reuses a valid issuer-bound registration rather than registering per turn; issuer change cannot reuse another issuer's registration/token.
- Tokens never enter Markdown, Git, browser storage, prompt text, error payloads, or logs. A session-scoped gateway cannot call another session's server/tool.
- Mobile sign-in does not redirect to the wrong machine's loopback address. Missing external hosting/credentials yields an honest setup state.
- Non-idempotent MCP calls are not automatically duplicated after ambiguous failure.

### Workflows, UX, and performance

- A structured workflow validates its dependency graph, performs the intended scoped steps, pauses for required consent, resumes safely, and cancels child work.
- Delegation respects tool/permission subsets, allowed-agent lists, budgets, recursion limits, and isolated write scopes.
- Chat and settings complete actual host-backed journeys on supported layouts, with meaningful loading/error/unauthorized/unsupported/offline states.
- Test CLI and packaged GUI launches; reproduce or conclusively narrow the original Devin terminal discrepancy without claiming an untested fix.
- Repeated sessions and cancellation leak no processes, callbacks, queues, or MCP connections; benchmark results meet predeclared budgets or identify an explicit regression.

## 15. Definition of done and reporting

Completion means working backend and frontend integration with evidence—not a plan, directory scanner, static agent list, fake tools UI, unverified OAuth helper, or success-shaped mock responses.

Produce reviewable changes with migration/rollback instructions, versioned schemas, examples, tests, protocol fixtures, reuse/attribution records, capability matrix, and setup documentation. Update the task ledger with exact file paths and test evidence. Keep generated examples free of credentials and machine-specific private paths.

Use bounded CI jobs, explicit per-job timeouts, and concurrency cancellation. Select focused tests before full suites. Avoid a repeated unrestricted test process that burns CI minutes. Do not run paid live agent tests in CI by default or enable overages.

Final report must state:

- Actual branch/commit/PR links for both repositories and what changed.
- What already existed, what was extended, and what was intentionally preserved.
- Native engine versions/interfaces tested; supported vs unsupported vs externally blocked capabilities.
- Source paths for canonical agent files and how the user creates an agent, connects MCP, selects tools, and runs it from chat.
- Test commands/results, live vs fixture evidence, screenshots where appropriate, performance before/after, and remaining regressions.
- Migration/rollback status, security limitations, reuse/license inventory, and any user-only authentication or hosting prerequisite.
- The exact next action for a blocker, without describing blocked work as complete.

Start by reporting the current repository state, the concrete reuse candidates, and the dependency-ordered plan. Then proceed with P0 and implementation in the same task. Do not ask the user to re-explain the already specified scope.

---

## Source starting points — refresh during P0

These sources establish interfaces and conventions, not universal feature parity, benchmark superiority, permission to reuse subscriptions, or proof that the installed versions support every documented method.

- **S1 — Codex app-server:** https://developers.openai.com/codex/app-server/ (resolved during preparation to https://learn.chatgpt.com/docs/app-server). Authentication rules distinguish local/open-source use from commercial/hosted services; verify the supported Sign in with ChatGPT/API path for the actual distribution. Do not turn subscription sessions into an unauthorized generic inference proxy.
- **S2 — Devin ACP:** https://docs.devin.ai/cli/acp/zed ; follow its linked configuration/auth/permissions documentation and local command help.
- **S3 — Grok machine interfaces:** https://docs.x.ai/build/cli/headless-scripting ; verify identity of the installed `grok` executable, not just its command name.
- **S4 — Claude Agent SDK:** https://code.claude.com/docs/en/agent-sdk/overview ; use permitted SDK authentication. The documentation requires prior approval for third-party products offering claude.ai login/rate limits; do not harvest CLI subscription tokens.
- **S5 — ACP protocol:** https://agentclientprotocol.com/protocol/v1/overview ; index: https://agentclientprotocol.com/llms.txt . Negotiate capabilities and handle optional methods honestly.
- **S6 — ACP v2 maturity:** https://agentclientprotocol.com/announcements/acp-v2-draft . The fetched announcement describes draft status and feature/version gating; check the current status before selecting a production version.
- **S7 — ACP Python SDK:** https://agentclientprotocol.com/libraries/python ; https://github.com/agentclientprotocol/python-sdk . Other official SDKs are linked from the protocol documentation.
- **S8 — Agent Skills format:** https://agentskills.io/specification . Client implementation guidance: https://agentskills.io/client-implementation/adding-skills-support . Definitions and storage conventions are different concerns.
- **S9 — Codex skill discovery/customization:** https://developers.openai.com/codex/skills/ (resolved to https://learn.chatgpt.com/docs/build-skills). Verify native invocation, disabled skills, and collision behavior rather than assuming all clients behave identically.
- **S10 — Copilot custom-agent configuration:** https://docs.github.com/en/copilot/reference/custom-agents-configuration . Product-specific fields and tool aliases differ across GitHub cloud and IDE clients.
- **S11 — MCP SDKs:** https://modelcontextprotocol.io/docs/sdk ; choose a maintained SDK that actually implements the negotiated protocol/auth revision.
- **S12 — OpenCode source candidate:** https://github.com/anomalyco/opencode . Inspect exact files and licenses before reuse; no speed claim is made here.
- **S13 — Pi source candidate:** https://github.com/earendil-works/pi (resolved from the older https://github.com/badlogic/pi-mono URL during preparation). Verify exact package/license scope.
- **S14 — Goose source candidate:** https://github.com/aaif-goose/goose (resolved from https://github.com/block/goose during preparation). Verify exact component/license scope.
- **S15 — MCP authorization:** https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization ; latest alias: https://modelcontextprotocol.io/specification/latest/basic/authorization . Refresh before coding.
- **S16 — Client registration/CIMD/DCR:** https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration . Includes selection order, DCR compatibility, issuer binding, and application-type requirements.
- **S17 — OAuth security:** https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations . Use its linked discovery and security-best-practice material for the full implementation.
- **R1 — Preliminary backend runtime read:** https://github.com/Beyon-Digital/TermXMob/blob/main/src/termx/agent/manager.py . Observed content blob SHA: `d57400932d0ea29f1bb019e03a67d9d80d695864`; this is a file blob, not a repository HEAD.
- **R2 — Preliminary custom-agent request-model read:** https://github.com/Beyon-Digital/TermXMob/blob/main/src/termx/app.py . Observed content blob SHA: `11e8377d4e95a6f87c12bbe6a84f7f7750deda37`; this is a file blob, not a repository HEAD.
- **R3 — Client paths surfaced by repository search:** `src/lib/custom-agents.ts`, `src/lib/host-sync.ts`, `src/app/settings/agents.tsx`, and `src/components/chat/conversation-view.tsx` in `Beyon-Digital/termx-app`. Search pointed to commit `6a7ce47a47f7af366c8f40ece0abf6377772a876`; fetch current remote HEAD and inspect full files rather than treating search snippets as a complete implementation audit.
