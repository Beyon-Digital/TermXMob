# Engine extensions — implementation plan

Source brief: `TERMX_ENGINE_EXTENSIONS_KICKOFF.md`. Contracts: `tech-specs.md`. Ledger: `tasks.md`.

## Phase order (dependency-respecting)

- **P0 — Contracts & docs.** This directory; baseline recorded; deps pinned (`mcp==2.3.0`, `agent-client-protocol==0.12.1`, `claude-agent-sdk` pinned at install); schema v1 frozen.
- **P1 — Engine core + internal adapter + Codex.**
  1. `engines/types.py`, `base.py`, `events.py` (envelope helpers).
  2. `engines/jsonrpc.py` — bounded async JSONL/JSON-RPC stdio transport shared by codex + acp drivers (codex omits `"jsonrpc"`, acp includes it — parameterize).
  3. `engines/registry.py` — detection/probe/auth-state using `engines/env.py` (bounded exec, known-dirs, redacted env comparison).
  4. `engines/internal.py` — `AgentManager` behind `EngineAdapter` (in-process; existing behavior untouched).
  5. `engines/codex.py` — app-server client: initialize, thread/start·resume·read·list, turn/start·steer·interrupt, model/list, approval requests → `store.create_approval`/`respond_approval`, item events → envelope.
  6. `engines/gateway.py` — session registry, turn serialization, reconnect replay via store cursor, uncertain-state marking, process supervision.
  7. `app.py` engine + task `engine` field plumbing; store migrations (engine columns + `engine_sessions` table).
- **P2 — ACP client + adapters + Claude.**
  1. `engines/acp.py` on `agent-client-protocol`: client callbacks — `session/request_permission` → policy engine → approvals; `fs/read_text_file|write_text_file` → scoped fs (cwd boundary, read-only mode honored); `terminal/*` → sandbox-spawned terminals w/ lifecycle; `elicitation/*` → approval kind.
  2. `engines/devin.py` (`devin acp`, auth via agent-initiated flow → "complete sign-in on host"), `engines/grok.py` (`grok agent stdio`; auth methods `xai.api_key`/`cached_token`).
  3. `engines/claude.py` on `claude-agent-sdk`: `ClaudeSDKClient`, `can_use_tool` → policy engine + approvals, `mcp_servers` injection point for scoped gateway, session resume via SDK ids.
  4. Conformance fixtures (recorded ACP/codex transcripts) + live smokes where auth permits.
- **P3 — Discovery + file-backed agents.**
  1. `discovery/` — roots, index, bounds, states, compat readers, watchers (bounded polling fallback), cache file.
  2. `agents/files.py` — `*.agent.md` parse/serialize/validate, revision hashes, atomic writes, conflict detection; `agents/registry.py` — DB projection + migration (`custom_agents` → files), API v2 fields, import/export (Copilot + TermX), `mcp-servers` → connection proposals.
  3. Effective-tool resolution (`agents/tools.py`): unions, denials, wildcards, revision pinning, `tools_mode` semantics.
- **P4 — MCP + OAuth.**
  1. `mcp/registry.py` (connection defs, trust, secret refs), `mcp/client.py` (pooled `mcp.Client`, catalog w/ fingerprint, pagination, namespacing `mcp__<conn>__<tool>`), `mcp/oauth.py` (discovery, PKCE, CIMD→DCR→manual, issuer-bound persistence, loopback callback on host, SSRF guards), `mcp/gateway.py` (session-scoped endpoint for engines needing it).
  2. Local fixtures: in-process AS + resource + DCR endpoint + MCP server (mcp SDK `MCPServer`) — no third-party creds needed.
- **P5 — Automatic use + UI.**
  1. Catalog projection into engine-appropriate surfaces (internal: scoped `skill` tool + instruction refs; codex: app-server skill/turn params + AGENTS.md precedence honoring; ACP: `_meta`/prompt blocks per negotiated capability; claude: `plugins`/setting sources or scoped tool).
  2. Workflows: extend runbooks with `agent` step type (runs a task on a chosen engine), human gates already exist; instructional markdown workflows surfaced as activated guidance, not executed as shell.
  3. Delegation: internal engine subagent path honored; external engines get delegation only via native subagent support else `unsupported` (never fake).
  4. termx-app UI: engines/settings, agents editor (form+raw markdown, effective tools, conflicts), skills/workflows browsers, connections UI + "complete sign-in on host", chat (engine picker, session identity, approvals/elicitation cards, cancel/steer capability-aware).
- **P6 — Hardening.** security/negative tests, packaged-GUI launch verification, perf budgets, migration rehearsal incl. rollback, capability matrix publication, final commits.

## Parallelization

After P0: codex adapter ∥ acp client ∥ claude adapter ∥ discovery index ∥ client fixtures — disjoint files, shared contracts from `tech-specs.md`. Store/manager/app.py integration serialized through me.
