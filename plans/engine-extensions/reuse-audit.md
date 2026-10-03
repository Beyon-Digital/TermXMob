# Reuse audit — engine extensions

Resolved 2026-10-03. Update strategy: `uv` pins in `pyproject.toml`/`uv.lock`; review changelogs on bump; no floating ranges.

## Adopted

| Package | Version | License | Repo | What we use | Why |
|---|---|---|---|---|---|
| `mcp` | 2.2.0 | MIT | github.com/modelcontextprotocol/python-sdk | `Client` (stdio + Streamable HTTP), typed results, pagination, session callbacks; `mcp.server.MCPServer` for fixtures/gateway | Official SDK; tracks negotiated spec (docs show 2026-07-28). OAuth helpers included (`client.auth`) — CIMD support verified in P4; gaps get thin glue, not a rewrite. Pinned 2.2.0 not 2.3.0 (2.3.0 released 2026-10-02 <7d old). |
| `agent-client-protocol` | 0.12.1 | Apache-2.0 | github.com/agentclientprotocol/python-sdk | `ClientSideConnection`, `Client` interface (session_update/request_permission/fs/terminal/elicitation callbacks), generated pydantic schema, `spawn_agent_process` stdio transport | Official ACP SDK; avoids hand-rolling JSON-RPC + schema drift for Devin/Grok. Requires-python `<3.15` — fits 3.14. |
| `claude-agent-sdk` | 0.2.160 | MIT | github.com/anthropics/claude-agent-sdk-python | `ClaudeSDKClient`, `ClaudeAgentOptions`, `can_use_tool`, hooks (`PreToolUseHookInput`), sessions (`SDKSessionInfo`), `McpSdkServerConfig`, `PermissionResultAllow/Deny` | Official SDK; Anthropic ToS → API-key auth path only (no claude.ai login resale). Pinned 0.2.160 (2026-09-25); daily-release cadence → no float. |

## Inspected, not adopted

| Candidate | Repo | Verdict |
|---|---|---|
| Codex SDK | learn.chatgpt.com codex-sdk | TS-only automation SDK; TermX is Python host → app-server protocol instead (open source under openai/codex, Apache-2.0). No Python SDK exists → thin stdio client written, schema pinned via `generate-json-schema`. |
| OpenCode | github.com/anomalyco/opencode | TS codebase; config/permission patterns reviewed for reference only — nothing portable without importing a second architecture. |
| Pi | github.com/earendil-works/pi | Internal engine already has equivalent session/tool primitives; adopting would be a framework rewrite for marginal gain. |
| Goose | github.com/aaif-goose/goose | Rust; extension patterns reviewed; MCP integration approach (Rust-side) not reusable in Python host. |
| Authlib | pypi | Deferred — prefer `mcp` built-in OAuth client; add only if PKCE/DCR gaps appear. |

## Vendored/generated artifacts

- `tests/fixtures/codex-schema/` — output of `codex app-server generate-json-schema` at codex-cli 0.159.2 (Apache-2.0 upstream; generated schema artifacts, no runtime code copied).
- No copied source from any engine. Engine processes are spawned, never decompiled or scraped.

## Telemetry/install notes

- `claude-agent-sdk` bundles the Claude Code CLI; uses `cli_path` option to prefer installed `/Users/jainamshah/.local/bin/claude` 2.1.83.
- `mcp` pulls `httpx2`, `pydantic`, `pyjwt[crypto]`, `jsonschema`, `sse-starlette`, `anyio`, `opentelemetry-api` (API only — no exporter configured → no telemetry leaves the host).
- `agent-client-protocol` pulls pydantic only (no extras).
