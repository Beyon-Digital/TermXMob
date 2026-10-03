# Engine extensions — technical specs (contracts, v1)

Frozen during P0. Everything else integrates behind these types. Adapt names to repo conventions (snake_case Python, JSON snake_case on the wire).

## 1. Engine contracts (`src/termx/engines/`)

```python
ENGINE_SCHEMA_VERSION = 1

class EngineKind(str, Enum):
    INTERNAL = "internal"; CODEX = "codex"; DEVIN = "devin"; GROK = "grok"; CLAUDE = "claude"

@dataclass EngineDescriptor:
    id: str                    # "codex", "devin", ...
    label: str
    installed: bool
    executable: str | None     # absolute resolved path
    version: str | None
    auth_state: str            # "unknown" | "authenticated" | "unauthenticated" | "expired" | "not_required"
    auth_detail: str           # safe, non-secret description
    location: str              # "local-process" | "sdk-bundled"
    transport: str             # "in-process" | "stdio-jsonl" | "acp-stdio" | "sdk"
    protocol: dict             # e.g. {"name":"codex-app-server","version": ...} / {"name":"acp","versions":[1]}
    error: str | None          # probe failure, human-readable

@dataclass EngineCapabilities:   # value = enforcement status, never optimistic bool
    tools_filter: str          # native|gateway|os|advisory|unsupported|unverified
    approvals: str
    streaming: bool
    resume: str                # supported|unsupported|unverified
    steer: str
    fork: str
    subagents: str
    skills_native: str         # engine loads skills itself
    mcp_native: str            # per-session MCP config support
    models: list[str]          # engine-visible model ids ([] = engine-managed)
    notes: dict[str, str]

@dataclass EngineSessionBinding:
    binding_id: str            # termx task/conversation correlation
    engine: str
    native_session_id: str     # codex threadId / acp sessionId / claude session_id / termx task_id
    native_turn_id: str | None
    conversation_id: str | None
    project_id: str | None
    cwd: str
    profile_revision: str      # content hash of applied agent profile
    extensions_snapshot: dict  # activated skill/agent/connection ids+hashes
    account_scope: str         # engine account identity tag (not secret)
    status: str                # active|idle|closed|lost
    replay_cursor: int         # termx event sequence
    created_at / updated_at: float

class EngineAdapter(Protocol):
    descriptor() -> EngineDescriptor
    async probe() -> EngineDescriptor                    # detect+auth, bounded, no mutation
    capabilities() -> EngineCapabilities
    async create_session(cfg: EffectiveRunConfiguration) -> EngineSessionBinding
    async attach(binding_id) -> EngineSessionBinding     # after host restart
    async send(binding, prompt, attachments) -> None     # starts a native turn
    def events(binding_id) -> AsyncIterator[EngineEvent]
    async cancel(binding) -> None
    async respond_approval(binding, request_id, decision, remember) -> None
    async close(binding) -> None                          # detach ≠ cancel ≠ delete
    async steer(binding, text) -> bool                   # False = unsupported
    async list_sessions() -> list[dict]                  # engine-side history (capability-gated)
```

`EffectiveRunConfiguration` — `{engine, model, agent_profile_revision, tools: ResolvedToolSet, skills:[refs], workflow, mcp_bindings:[{connection_id, tools}], sandbox_profile, approval_mode}` — immutable per session create.

## 2. Event envelope

Existing `events` table gains engine coverage; WS contract unchanged:

```json
{"id","task_id","sequence","type","payload","created_at",
 "engine": "codex", "native": {"session_id":"thr_..","turn_id":"turn_..","item_id":"..."},
 "schema": 1}
```

New `type` values are additive (`engine.*`, `mcp.*`, `skill.*`, `workflow.*`, `approval.elicitation`, `provider.stream.*` kept). Unknown upstream types retained as `engine.raw` with bounded redacted payload (≤ 8 KB). Approval requests from engines map to existing `approvals` rows (`kind` ∈ plan|tool|capability|elicitation). No fabricated usage/reasoning: absent data stays absent.

## 3. Discovery (`src/termx/discovery/`)

Roots (host machine only):
- `~/.agents/skills/<id>/SKILL.md` — Agent Skills spec (name ≤64 lowercase-hyphen, description ≤1024).
- `~/.agents/agents/<id>.agent.md` — TermX profiles (see §4).
- `~/.agents/workflows/<id>.md` — TermX workflow docs (instructional or `x-termx-steps` structured).
- `~/.agents/toolsets/<id>.json`, `~/.agents/mcp/<id>.json`, `~/.agents/acp/<id>.json`.
- Trusted project `<root>/.agents/**` (same layout) — trust = project already authorized by user.
- Compat readers (metadata only, flagged `compat`): `AGENTS.md`, `.github/agents/*.agent.md` (Copilot fields), `.claude/skills/*`, `.devin/` config/skills, `.codex` config, `~/.claude/skills`, `~/.config/devin/skills`.
- Explicit extra roots via host config `extensions.extra_roots` (validated dirs only).

Index: `~/.config/termx/discovery-cache.json` — per machine/user/project scope; entry = `{qualified_id, source, path, kind, name, description, hash, revision, enabled, trust, health{status,error}, mtime}`. Bounds: depth ≤4, ≤512 entries/root, ≤256 KB/file, YAML lenient-safe parse only (no custom objects), 250 ms/root budget, symlink canonicalization w/ cycle detection, out-of-root targets rejected unless under `extensions.trusted_symlinks`.

States (independent): `discovered`, `trusted` (root trust), `enabled` (user/admin toggle), `authorized` (consent/credentials satisfied), `compatible` (for chosen engine), `active` (applied to a run). Discovery performs zero side effects.

Precedence: explicit qualified selection > project-scoped > user > bundled; `deny` rules always win; collisions reported, never silently merged.

## 4. Custom-agent file format (`*.agent.md`)

Canonical: `~/.agents/agents/<id>.agent.md`. YAML frontmatter (safe loader), body = instructions. Unknown vendor keys preserved verbatim for round-trip, never executed; reported in `compat` diagnostics.

```yaml
name: str (≤200)          # display; filename slug is the id
description: str (≤2000)  # Copilot-required → required by TermX import-compat
tools: [str] | str | absent   # absent = engine default (imported intent "all") — see below
target: str | absent           # copilot compat (vscode|github-copilot) — diagnostic only
mcp-servers: obj | absent      # copilot: parsed → mapped to connection proposals, NOT auto-active
disable-model-invocation: bool # → x-termx.auto_use=false
user-invocable: bool           # → x-termx.enabled default
model: str | absent
x-termx:
  schema-version: 1
  id: agent.<slug>
  engine: internal|codex|devin|grok|claude|inherit
  model: <id>|inherit
  enabled: bool
  auto_use: bool               # implicit selection allowed
  tools_mode: explicit|all     # NEW profiles require explicit; "all" = reviewed import intent
  toolsets: [toolset.<id>]
  deny_tools: [str]
  skills: {mode: auto|manual|off, include:[], exclude:[]}
  workflows: [workflow.<id>]
  mcp_connections: [{connection: connection.<id>, tools: [str]|"*"}]
  delegation: {enabled: bool, allowed_agents: [agent.<id>], max_depth: 1, max_children: 4}
  policy_profile: str|null     # reference only — not authority granted by the file
  approval_mode: standard|remember|autonomous
  sandbox_profile: host|workspace|agent
  limits: {max_steps, max_seconds, shell_timeout_s, max_parallel_subagents, max_subagents_total}
```

Tools semantics: `tools:` **omitted** → `tools_mode=all` flagged `review_required`; `tools: []` → no tools; list → explicit set; `server/*` wildcard expands within that server's approved set at resolution time and does NOT auto-grow for restricted profiles (expansion is pinned by content hash at approval time; new server tools land in `unreviewed`).

Resolved tool set = (explicit ∪ toolsets ∪ connection.tools) − deny_tools, then ∩ authorized ∩ policy. Deny always wins. Empty effective set is valid (never falls back to all).

Files: atomic write (`tempfile`+`os.replace`, mode 600), expected `sha256` revision precondition, `.bak` of previous file kept, watcher-suppression via write-token window, create/rename/delete restricted to authorized roots, traversal/symlink-race checks via `os.open`+`O_NOFOLLOW`+dirfd resolution.

`custom_agents` DB table becomes a **projection**: adds `file_path`, `file_revision`, `engine`, `enabled`, `source` (user|project|import|compat|device), `sync_state`. Migration copies rows → files once, preserves `id`, keeps table rows as cache w/ `migrated_at`; rollback restores DB authority flag (`settings.engine_extensions.agents_authority = db|files`) without data loss.

## 5. MCP connections + OAuth

`~/.agents/mcp/<id>.json`:
```json
{"schema":1,"id":"connection.<slug>","label":"…","transport":"stdio|http",
 "command":["…"],"url":"https://…","enabled":true,"owner":"user|project:<id>",
 "auth":{"method":"none|env|oauth","env_names":["…"],"scopes":["…"]},
 "secret_refs":["cred-ref"],"approved_tools":["*"],"trust":"trusted|untrusted",
 "created_at":0,"updated_at":0}
```
No secrets in the file — `secret_refs` point at `CredentialStore` keys (`mcp.<conn>.<name>`), env names resolved at spawn. `trust=trusted` required before any process spawn/connect (URL field is not consent).

OAuth (spec 2026-07-28): protected-resource metadata (`.well-known/oauth-protected-resource`) → AS metadata (`.well-known/oauth-authorization-server` / OIDC) → registration selection order: existing issuer-bound registration → CIMD (`client_id_metadata_document_supported`, requires real hosted HTTPS doc — unconfigured by default, template + docs provided) → DCR (`registration_endpoint`, `application_type:"native"`) → manual client-info entry. PKCE S256, CSRF `state` bound to {user,device,host,connection,issuer,redirect}, exact registered loopback callback (`http://127.0.0.1:<port>/oauth/callback` on the **host**; remote clients get "complete sign-in on host"). Issuer-bound registrations persisted separately from resource tokens in `CredentialStore`. SSRF: reject link-local/metadata IPs, private ranges unless connection opts in (`lan: true`), revalidate DNS at connect.

Gateway (only where needed — Claude in-process `mcp_servers`, engines without per-session config): engine → session-scoped local endpoint (unix socket / loopback+token) → upstream pooled MCP client. Session-scoped capability token; tokens never leave host broker.

## 6. Enforcement honesty

Per engine record: `tools_filter` ∈ native|gateway|os|advisory|unsupported|unverified. Internal: native (registry + policy engine). Codex: advisory+os (app-server approvals + sandbox flags; tool-name filtering is advisory → disclosed). ACP engines: approvals native (`session/request_permission` mediated by policy engine); tool-name filtering advisory. Claude: gateway-capable (`can_use_tool`/`PreToolUse` hook = programmatic gate) + native permissions. Strict profiles that can't be enforced are blocked from launching with that label; loose profiles display actual authority.

## 7. API surface (all behind existing scopes)

`GET /api/engines` · `GET /api/engines/{id}` (descriptor+capabilities) · `POST /api/engines/{id}/probe` · `GET /api/engines/{id}/models` · `GET /api/engines/diagnostics` (redacted env comparison).
`GET /api/extensions` (catalog w/ filters) · `POST /api/extensions/rescan` · `POST /api/extensions/{qid}/enable|disable|trust` · `GET /api/extensions/{qid}` (body on demand).
`GET/POST/PATCH/DELETE /api/custom-agents` extended: `engine, model, tools, toolsets, deny_tools, skills, workflows, mcp_connections, delegation, enabled, revision, source`; `?source=file` raw markdown endpoint; `POST /api/custom-agents/{id}/duplicate`, `/import`, `/export`.
`GET/POST/PATCH/DELETE /api/mcp/connections` · `POST /api/mcp/connections/{id}/connect|disconnect|test` · `GET /api/mcp/connections/{id}/tools` · `POST /api/mcp/oauth/begin` → `{authorize_url}`/`{complete_on_host:true}` · `GET /api/mcp/oauth/status` · `POST /api/mcp/oauth/revoke` · `GET /api/mcp/oauth/callback` (loopback only).
Task create: `engine`, `profile_revision`. Conversation: `engine`. Approvals: `elicitation` kind.
New scopes reuse existing set (`agent-*`, `ai-settings`, `host-admin`); no scope additions needed.

## 8. Env/launch diagnostics

`engines/env.py`: resolve engine executable via ordered strategy {explicit config override → PATH → known dirs (~/.local/bin, /usr/local/bin, brew prefixes, platform equivalents)}; capture binary path/version/cwd/HOME presence (names only) — fix `hostenv` gap by adding `~/.local/bin` + `~/.local/share/mise/shims`-style user dirs to GUI candidates for engine resolution only (not wholesale env import). Reproducer test asserts `devin` resolves under stripped GUI env before/after.
