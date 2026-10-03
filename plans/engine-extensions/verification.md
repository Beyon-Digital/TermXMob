# Verification — engine extensions

## Baseline (P0, 2026-10-03)

`uv run pytest -q` → **476 passed, 2 failed, 70 skipped** (~117 s, ulimit -n 8192).

Pre-existing failures (unrelated, do not regress further):
- `tests/test_sandbox_macos.py::test_xcode_shim_runs_but_prefs_stay_closed`
- `tests/test_sessions.py::test_send_signal_int_reaches_foreground_job` (macOS pty job-control quirk)

## Commands

- Host: `cd termxmob && uv run pytest -q` (subset: `-k engine|acp|mcp|discovery|agent_file|oauth`)
- Client: `cd termx-app && pnpm vitest run` ; e2e: `pnpm playwright test` w/ `e2e/mock-host.mjs`
- Live engine smokes: `tests/live/test_engines_live.py` — skipped unless `TERMX_LIVE_ENGINES=1`; each test reports PASS/SKIP(auth-missing)/BLOCKED.

## Evidence ledger

| Check | Result | Where |
|---|---|---|
| baseline suite (P0) | 476p/2f/70s | this file |
| post-P1..P5 suite | 510p/2f(same env)/70s | `uv run pytest -q` |
| ACP adapter fixtures | ✅ fake-agent transport, incl. `session/load` resume via state file | `tests/` ACP tests |
| MCP pool e2e | ✅ catalog + fingerprint + call_tool + clean shutdown (FastMCP stdio fixture) | `tests/test_mcp.py` |
| Registry migration | ✅ legacy DB row → `*.agent.md` export, id preserved, `migrated_at` set, idempotent rescan | `tests/test_discovery_agents.py::test_sync_migrates_db_rows_to_files` |
| Client vitest | 41 passed (engine-event reducer cases incl.) | `pnpm vitest run` |
| Client tsc | clean | `pnpm exec tsc --noEmit` |
| Client lint | 87 problems, all pre-existing on clean tree (0 new) | `pnpm expo lint` |

## Live runs (real engines, this machine, devin/engine-extensions)

| Engine | Evidence |
|---|---|
| codex 0.159.2 | real thread `01a1025d-…`, streamed reply `OK`; `thread/resume` continued natively |
| grok 1.0.40 | ACP session `01a1028b-…`, reasoning deltas + `end_turn`, reply `OK`; UI E2E: composer `Engine: Grok` → `grok` chip → `Working · via grok` → `Run complete · via grok` |
| devin 3000.11.3 | `initialize` + `session/new` verified live; turn BLOCKED on weekly quota |
| claude 2.1.83 | SDK path hermetic-verified; live turn needs `ANTHROPIC_API_KEY` (absent) |
| internal | unchanged provider path; existing suite green |

## P6 lifecycle review

- Shutdown order (lifespan): forwards → `mcp_pool.shutdown` → loopback →
  `engines.shutdown` → `shutdown_state` (`src/termx/app.py` ~L577).
- JSONL transport close escalates wait → terminate (3 s) → kill (3 s)
  (`src/termx/engines/jsonrpc.py::close`).
- Adapters implement `close(binding)` + `shutdown()`; gateway `shutdown()` fans out.
- Engine dispatch: `body.engine != "internal"` → `state.engines.create_task`;
  `KeyError`→404, `ValueError`→400 (`src/termx/app.py`, task endpoint).
- Migration: file-authority; DB rows w/o files exported once (id-preserving),
  `migrated_at` set, errors collected into report — never silently dropped
  (`src/termx/agents/registry.py::sync`).

## Known limitations (do not regress into claims)

- External-engine tool allow/deny = advisory labels, not enforced permissions.
- Claude live turns need an API key; Devin turns were quota-blocked at verify time.
- Two pre-existing env test failures (macOS Seatbelt shim, pty race) reproduce on clean tree.
