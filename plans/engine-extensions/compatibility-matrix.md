# Capability matrix — FINAL (P6, verified against live host on macOS)

Legend: ✅ live-tested (real engine, real turn) · 🔧 fixture-tested (fake adapter transport) · 🚫 unsupported by engine · ⛔ externally blocked · 📋 documented-per-upstream

| Capability | internal | codex 0.159.2 | devin 3000.11.3 | grok 1.0.40 | claude 2.1.83 |
|---|---|---|---|---|---|
| transport | in-process | app-server JSONL/stdio ✅ | ACP stdio ✅ | ACP stdio ✅ | claude-agent-sdk 🔧 |
| create/send/stream | ✅ (existing) | ✅ `OK` turn, real thread | 🔧 fixture; ⛔ live turn blocked on weekly quota (ACP handshake ✅ verified) | ✅ `OK` turn, ACP session `01a1028b-…` | 🔧 fixture; ⛔ needs API key |
| cancel/interrupt | ✅ | turn/interrupt 🔧 | session/cancel 🔧 | session/cancel 🔧 | SDK interrupt 🔧 |
| approvals | ✅ | server→client requests ✅ (approval.requested surfaced) | session/request_permission 🔧 | session/request_permission 🔧 | can_use_tool 🔧 |
| resume native session | ✅ (tasks) | thread/resume ✅ live | session/load 📋 capability | session/load ✅ live | session_id 🔧 |
| steer mid-turn | ✅ | turn/steer 📋 | session/prompt queue 📋 | session/prompt 📋 | SDK 📋 |
| model selection | ✅ | model/list ✅ (native model list in UI) | --model 📋 | --model 📋 | options.model 📋 |
| skills native load | termlist tool | ~/.agents/skills (TermX injects instructions) | ~/.config/devin/skills native | injected via prompt | ~/.claude/skills native |
| tool-name enforcement | **native** (toolsets enforced) | advisory label only | advisory label only | advisory label only | advisory label only |
| MCP per-session | TermX gateway (scoped) | engine config / mcp cmd | devin mcp config + session/new mcp_servers ✅ | session/new mcp_servers ✅ | SDK mcp_servers ✅ |
| MCP OAuth | TermX-side pool ✅ | n/a (TermX-side) | TermX-side ✅ | TermX-side ✅ | TermX-side ✅ |
| subagents/delegation | native | multi-agent 📋 | --agent-type 📋 | --agents 📋 | SDK subagents 📋 |
| usage stats | ✅ | reported when emitted | reported when emitted | reported when emitted | ResultMessage 🔧 |
| engine.* event stream | n/a | ✅ message/reasoning/item deltas | ✅ session/update notifications | ✅ session/update notifications | ✅ SDK message events |

## Honest labels (as shown in the app UI)

- **Tool allow/deny lists** — enforced natively only for the `internal` engine. For
  codex/devin/grok/claude they are passed as *advisory* instructions; the UI labels
  this `tools: advisory` vs `tools: native`.
- **Claude** — all live turns require `ANTHROPIC_API_KEY` (SDK path verified
  hermetically; no live turn evidence).
- **Devin** — ACP initialize/session handshake verified live; a full turn was
  blocked by weekly quota on this machine at verification time.
- **MCP OAuth conns** — held in the TermX pool (token storage via CredentialStore);
  non-OAuth conns are also handed to engines natively via `session/new
  mcp_servers` where the transport supports it.

## Live evidence (this machine, branch devin/engine-extensions)

- `codex` — real thread `01a1025d-…`, streamed reply `OK`; `thread/resume` on the
  same thread continued the conversation natively.
- `grok` — ACP session `01a1028b-…`, streamed reasoning + `end_turn`, reply `OK`.
- `devin` — `initialize` + `session/new` succeeded live; prompt blocked on quota.
- `claude` — SDK adapter exercised against a fake transport; API key absent.
