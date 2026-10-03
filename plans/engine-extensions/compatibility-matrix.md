# Capability matrix — filled during implementation (updated as evidence lands)

Legend: ✅ live-tested · 🔧 fixture-tested · 🚫 unsupported by engine · ⛔ externally blocked · ❓ unverified

| Capability | internal | codex 0.159.2 | devin 3000.11.3 | grok 1.0.40 | claude 2.1.83 |
|---|---|---|---|---|---|
| create/send/stream | ✅(existing) | ❓ | ❓ | ❓ | ❓ |
| cancel/interrupt | ✅ | turn/interrupt ❓ | session/cancel ❓ | session/cancel ❓ | SDK interrupt ❓ |
| approvals | ✅ | server→client requests ❓ | session/request_permission ❓ | same ❓ | can_use_tool ❓ |
| resume native session | ✅(tasks) | thread/resume ❓ | session/load (capability) ❓ | session/load ❓ | session_id ❓ |
| steer mid-turn | ✅ | turn/steer ❓ | session/prompt queue ❓ | ❓ | ❓ |
| model selection | ✅ | model/list ❓ | --model ❓ | --model ❓ | options.model ❓ |
| skills native load | n/a(termlist tool) | ~/.agents/skills? verify | ~/.config/devin/skills | plugins/skills? verify | ~/.claude/skills |
| tool-name enforcement | native | advisory | advisory | advisory | gateway (hooks) |
| MCP per-session | termx gateway | engine config / mcp cmd | devin mcp config | grok config | SDK mcp_servers |
| subagents/delegation | native | multi-agent? | --agent-type | --agents | SDK subagents |
| usage stats | ✅ | reported if emitted | reported if emitted | ❓ | ResultMessage |

**Honesty rule:** ❓ must be verified before claiming; unsupported → control hidden/explained in UI.
