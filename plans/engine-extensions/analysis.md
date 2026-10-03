# Engine extensions — analysis

Audit date: 2026-10-03. Branch: `devin/engine-extensions` (from `main` @ 832b2ab).

## Repositories

| Repo | Path | HEAD | Dirty state (preserved) |
|---|---|---|---|
| Beyon-Digital/TermXMob | `~/Documents/GitHub/termxmob` | `main` = `origin/main` | `desktop/SIGNING.md` modified; untracked `TERMX_ENGINE_EXTENSIONS_KICKOFF.md`, `desktop/scripts/sign_notarize_app.sh` |
| Beyon-Digital/termx-app | `~/Documents/GitHub/termx-app` | `main` = `origin/main` (6a7ce47) | 4 modified `public/fonts/*.woff2` |

## Baseline (recorded before implementation)

- `uv run pytest -q`: **476 passed, 2 failed, 70 skipped** (116 s).
  - `tests/test_sandbox_macos.py::test_xcode_shim_runs_but_prefs_stay_closed` — pre-existing macOS env failure.
  - `tests/test_sessions.py::test_send_signal_int_reaches_foreground_job` — pre-existing pty/foreground failure on this host.
- First pytest run died in `cleanup_dead_symlinks` with `EMFILE` — needs `ulimit -n 8192` on this box.
- Benchmarks: `plans/agent-v2/benchmarks/baseline.json` exists (agent-v2 phases 1–2 harness); reuse `tests/bench_agent.py` for deltas.
- Frontend: termx-app `pnpm vitest` + `e2e/mock-host.mjs` playwright harness exist.

## Installed engines (probed 2026-10-03)

| Engine | Path | Version | Machine interface | Auth state |
|---|---|---|---|---|
| Codex | `/usr/local/bin/codex` | codex-cli 0.159.2 | `codex app-server` (JSONL JSON-RPC, stdio default; ws/unix experimental) | `~/.codex/` populated incl. `auth.bdt.json` (value not inspected) |
| Devin | `~/.local/bin/devin` | 3000.11.3 | `devin acp` (JSON-RPC stdio; `--agent-type`, `--model`/`DEVIN_MODEL`) | `~/.config/devin/` has `credentials.toml` (present; not printed) |
| Grok | `~/.local/bin/grok` | 1.0.40 | `grok agent stdio` (ACP); `-p` headless alt | `cached_token`/`xai.api_key` methods advertised per docs |
| Claude | `~/.local/bin/claude` | 2.1.83 | `claude-agent-sdk` (bundles/spawns CLI) | `~/.claude/` populated; API-key auth required for third-party SDK use |

Key env finding: `hostenv.py::_darwin_candidates()` does **not** include `~/.local/bin` → GUI-launched host cannot resolve `devin`/`grok`/`claude`. That is the leading hypothesis for the reported "Devin works in terminal but not TermX" issue; verify with a reproducer before claiming a fix.

## Existing host internals (reused, not rebuilt)

- `AgentManager` — plan approval, tool registry+scheduler, context engine+compaction, recovery checkpoints, subagents, worktrees, steering, event store + bounded subscriber queues (200), WS replay at `/api/agent/tasks/{id}/events?after=seq`.
- `AgentStore` — SQLite; tasks/events/approvals/checkpoints/conversations(+turns,refs)/custom_agents/policy_rules/runbooks(+runs); additive ALTER migration style.
- `CredentialStore` — Keychain/secret-tool/DPAPI + `TERMX_AI_<ID>_API_KEY` env; memory backend for tests.
- `SandboxRunner`/`SpawnSpec` — profiles host|workspace|agent, writable/read-only roots, capability grants, private-HOME, network none|localhost|outbound.
- `RunbookRunner` — shell steps, confirm gate, resume after restart, sandbox profile per run.
- `PolicyEngine` — allow/deny rules with scope_type task|project|custom_agent|host, fingerprinting, remember scopes.
- Client: local `custom-agents.ts` store (device-local) + `host-sync.ts` write-through with host-id adoption + `settings/agents.tsx`; chat via `use-conversation`/`use-agent-task`; capabilities via `/api/health`.
- Devin CLI already implements its own MCP client + OAuth storage at `~/.local/share/devin/mcp/oauth` — native path, separate concern.

## Gaps this project fills

1. No engine concept — `provider_id`+`model` only; tasks bind to the internal Responses-adapter loop.
2. Custom agents: DB rows + device-local island; no files, engine/model per agent, skills/workflows/toolsets/MCP links, delegation config, or revision preconditions.
3. No discovery of `~/.agents/` or compat locations; no trust states.
4. No MCP client, OAuth, connection registry, or tool-name enforcement beyond provider schema lists.
5. No engine launch diagnostics; GUI env resolution gap (above).

## Interface decisions

- Codex → `codex app-server` stdio (JSONL JSON-RPC; `jsonrpc` field omitted on wire). Schema pinned via `codex app-server generate-json-schema`.
- Devin/Grok → shared ACP client (`agent-client-protocol` 0.12.1, Apache-2.0) with per-engine launch/auth adapters.
- Claude → `claude-agent-sdk` (MIT; `ClaudeSDKClient`, `can_use_tool`, hooks, sessions). API-key auth only — no claude.ai subscription resale (vendor ToS).
- Internal → in-process adapter over `AgentManager`.
- MCP → `mcp` 2.3.0 (MIT) client; OAuth per spec 2026-07-28 incl. CIMD-preferred/DCR-fallback/manual.
- Pi/OpenCode/Goose: inspected; no component adopted (internal runtime already covers needs). Recorded in reuse-audit.
