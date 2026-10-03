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
| baseline suite | 476p/2f/70s | this file (P0) |

(filled as phases land)
