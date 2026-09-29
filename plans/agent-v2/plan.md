# Agent v2 — Implementation plan

Scope of this delivery: **Phase 0 + Phase 1** of the handoff (Agent core).
Later phases are ledgered in `tasks.md` but not implemented here.

## Phase 0 — baseline (AG2-001)

- `tests/test_agent_benchmarks.py`: deterministic fake-provider benchmark
  harness. Scenarios: `ask_simple`, `file_inspection` (10 files, shell vs
  structured variants), `check_command` (long output), `subagents_two`,
  `computer_loop`. Records provider-independent facts: wall-clock, provider
  turn count, tool-call count, event-type histogram, per-tool durations.
- `scripts/agent_bench.py`: same harness as a CLI; writes JSON under
  `plans/agent-v2/benchmarks/` for before/after evidence.
- Baseline captured on the unmodified tree (see `tasks.md` evidence).

## Phase 1 — Agent core (AG2-002 … AG2-009, AG2-014)

Dependency order: registry → fs/search tools → git/project tools → streaming
runner → scheduler → context engine → provider runtime → metrics.

1. **`agent/tools/registry.py`** — `ToolSpec(name, description, parameters,
   mutability, parallel_safe, approval, decide, execute)`. Registry produces
   the provider `tools[]` schema and dispatches `ProviderCall`s. Legacy names
   (`run_shell`, `share_file`, `spawn_subagent`, computer) register as specs
   bound to the existing manager implementations — identical behavior.
2. **`agent/tools/filesystem.py`** — `list_files`, `read_file` (line ranges,
   size guard, binary detection, sha256 revision), `write_file`
   (expected-revision atomic write preserving mode), `apply_patch`
   (`git apply --check` then apply; patch paths must stay in project).
   Boundary enforcement shared via `tools/paths.py`: resolved path must be
   inside task cwd; sensitive names refused.
3. **`agent/tools/search.py`** — `search_project` (filename + content modes,
   same skip set and caps as `ProjectFiles.search`).
4. **`agent/tools/git.py`** — `git_status`, `git_diff`, `git_stage`,
   `git_commit`, `git_branch`, `git_fetch`, `git_pull`, `git_push`
   (push → always approval, matching `evaluate_shell` "External publication").
   `preview_list`, `machine_info` in `tools/project.py`.
5. **`agent/execution.py`** — add `stream_shell(...)` emitting chunks to a
   callback; `tools/shell.py` maps it to `process.started/output/exited|
   cancelled|timed_out` task events (redacted, chunked, rolling cap) plus the
   `run_check` semantic wrapper (kind/status/duration/summary + log artifact
   when output is large).
6. **`agent/scheduler.py`** — partitions a provider call list into contiguous
   groups: a group of all-`parallel_safe` non-approval calls executes via
   `asyncio.gather` (results appended to history in call order); anything else
   runs serially; approval pause still returns mid-batch exactly as today.
7. **`agent/context/engine.py`** — `project_snapshot(root)` (name, git
   branch/dirty, manifests, detected languages, guessed check commands,
   depth-2 tree, recently-modified files; secret files never read) replaces
   the flat manifest as turn input; `ContextEngine` tracks budget estimates
   and slims oversized tool outputs before they re-enter the transcript.
8. **`agent/runtime.py`** — `ProviderHttpRuntime` caches one
   `httpx.AsyncClient` per provider id with connection limits; manager passes
   it into adapters; closed from `AgentManager.close()`.
9. **`agent/metrics.py`** — per-task counters (provider requests/latency,
   per-tool durations, shell durations, screenshot bytes, tokens, approval
   wait) persisted to the additive `tasks.metrics` column on completion.

Non-goals for this delivery (ledgered, later phases): provider streaming +
retry/circuit/fallback (AG2-010/011), compaction checkpoints (AG2-008),
async sub-agent handles (AG2-012/013), computer observation v2 (AG2-015/016),
resume endpoint (AG2-017), conversations/custom agents (PROD-001/002),
worktrees (PROD-003), activity/ports/runbooks (PROD-004/005/006), scopes v2
(SEC-001/002).

## Compatibility contract

- `ProviderAdapter` protocol unchanged (`test/plan/turn` signatures kept —
  existing fake adapters keep working). Tool schemas move to the registry;
  the adapter imports them.
- `manifest` kwarg stays, now carrying the compact snapshot dict.
- Task replay event names keep working; new event types are additive
  (`process.*`, `tool.batch`, `context.snapshot`, `task.metrics`).
- `machine_snapshot` gains capability flags `agent_tools_v2`,
  `agent_streaming:false`, `agent_context_v2` — advertised conservatively.
