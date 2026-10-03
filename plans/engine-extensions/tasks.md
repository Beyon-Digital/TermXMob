# Task ledger — engine extensions

Status: pending / in_progress / done / blocked(reason). Evidence column carries test/verification proof.

| ID | Task | Deps | Scope (files) | Acceptance | Status | Evidence |
|---|---|---|---|---|---|---|
| EX-000 | Baseline + contracts + plan docs | — | plans/engine-extensions/* | 10 docs, baseline recorded | done | pytest 476p/2f(pre-existing); this file |
| EX-001 | Engine types + event envelope | EX-000 | src/termx/engines/types.py, base.py, events.py | dataclasses per tech-specs §1–2; unit tests | pending | |
| EX-002 | Engine env + probe | EX-001 | engines/env.py | resolves engines under stripped GUI env; `~/.local/bin` found; redacted | pending | |
| EX-003 | JSONL/JSON-RPC stdio transport | EX-001 | engines/jsonrpc.py | req/resp/notify, bounded queues, correlation, cancel cleanup | pending | |
| EX-004 | Store migration: engine cols + engine_sessions + extension tables | EX-001 | agent/store.py | additive ALTERs, rollback-safe | pending | |
| EX-005 | Internal adapter | EX-001,004 | engines/internal.py | AgentManager behind EngineAdapter; regression suite green | pending | |
| EX-006 | EngineGateway + app wiring | EX-005 | engines/gateway.py, app.py | session map, replay, turn serialization | pending | |
| EX-007 | Codex app-server adapter | EX-003,006 | engines/codex.py + fixtures | fixture: init/thread/turn/approval/interrupt; live smoke if authed | pending | |
| EX-008 | ACP shared client | EX-003 | engines/acp.py | initialize/auth/new/load/prompt/cancel + fs/terminal/elicitation callbacks | pending | |
| EX-009 | Devin adapter | EX-008 | engines/devin.py | launch `devin acp`, auth state, agent-types | pending | |
| EX-010 | Grok adapter | EX-008 | engines/grok.py | `grok agent stdio`, xai.api_key/cached_token | pending | |
| EX-011 | Claude SDK adapter | EX-006 | engines/claude.py | ClaudeSDKClient, can_use_tool→policy, session resume, sdk mcp hook | pending | |
| EX-012 | Discovery index | EX-004 | discovery/{roots,index,safety}.py | bounded scan, trust states, collisions, malformed entries | pending | |
| EX-013 | Compat readers | EX-012 | discovery/compat.py | AGENTS.md, .github/agents, .claude/skills, .devin, .codex | pending | |
| EX-014 | Agent file format | EX-012 | agents/files.py | parse/serialize/validate, x-termx ns, copilot fields preserved | pending | |
| EX-015 | Agent registry + DB projection + migration | EX-004,014 | agents/registry.py | id-preserving import, rollback flag, revision precondition | pending | |
| EX-016 | Effective tool resolution | EX-014 | agents/tools.py | unions/deny/wildcard/no-fallback/pinned expansion | pending | |
| EX-017 | Custom-agent API v2 | EX-015,016 | app.py | CRUD incl. markdown round-trip, import/export, conflict | pending | |
| EX-018 | MCP connection defs + registry | EX-004 | mcp/registry.py | file defs, trust gate, secret refs | pending | |
| EX-019 | MCP client pool | EX-018 | mcp/client.py | mcp SDK stdio+http, catalog+fingerprint, namespacing | pending | |
| EX-020 | OAuth (CIMD→DCR→manual) + SSRF | EX-019 | mcp/oauth.py | discovery, PKCE, issuer-bound, loopback host callback | pending | |
| EX-021 | Session-scoped MCP gateway | EX-019 | mcp/gateway.py | capability-token scoped endpoint per session | pending | |
| EX-022 | Skill/agent auto-activation | EX-012,017 | engines/*/inject + agent catalog tool | relevant-use plumbing per engine, activation records | pending | |
| EX-023 | Workflow engine (structured) | EX-012 | runbooks.py + workflows | agent steps, deps, gates, cancel/resume | pending | |
| EX-024 | Client: types/api/host-sync v2 | EX-017,019 | termx-app src/lib/* | new fields, pending-sync states | pending | |
| EX-025 | Client: engines + connections + skills UIs | EX-024 | termx-app src/app/settings/* | per tech-specs §7 journeys | pending | |
| EX-026 | Client: agent editor + chat integration | EX-024 | agents.tsx, conversation-view, run-view | form+md modes, capability-honest controls | pending | |
| EX-027 | Engine diagnostics endpoint + GUI env fix | EX-002 | engines/env.py, hostenv.py, app.py | redacted compare; reproducer proves ~/.local/bin fix | pending | |
| EX-028 | Fixtures + conformance suite | EX-003,008 | tests/fixtures/*, test_engines_*.py | recorded transcripts replay deterministically | pending | |
| EX-029 | Perf budgets + measurement | EX-006 | tests/bench_*.py | vs agent-v2 baseline; 1k-skill catalog test | pending | |
| EX-030 | Docs, capability matrix, commits | all | plans/*, README bits, git | reviewable commits both repos | pending | |
