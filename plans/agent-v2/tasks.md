# Execution graph: agent-v2

## Resume checkpoint
- Updated: 2026-09-28
- Current phase: Phase 1 complete; PR #16 open (draft); Devin Review rounds 1+2 addressed (27/27 threads resolved; fixes 2aab7d4, d09eec2)
- Next ready tasks: Phase 2 scope (provider streaming/retry, compaction, async subagents, observation v2)
- Active owners: devin-session
- Last verified command: `uv run pytest tests -q` — 236 passed, 10 skipped, 24.75s
- Last inspected surface: `src/termx/agent/**`, `project_files`, `git_ops`, `app.py` agent routes
- Blockers: none
- Resume instruction: continue the ledger below top-down; keep tests green.

## State rules
`pending -> ready -> in_progress -> blocked | done`
Only tasks whose dependencies are `done` may be `ready`.
Only validated tasks with evidence may be `done`.

## Canonical task ledger
```yaml
schema_version: 1
effort: agent-v2
plan_dir: ./plans/agent-v2
request_summary: Backend/host Agent upgrade — typed tools, scheduler, context engine, provider runtime, streaming shell — per TERMX_BACKEND_AGENT_V2_HANDOFF.md phases 0-1.
created_at: 2026-09-28
updated_at: 2026-09-28
tasks:
  - id: AG2-001
    title: Baseline tests and Agent benchmark harness
    status: done
    depends_on: []
    owner: devin-session
    write_scope: [tests/test_agent_benchmarks.py, scripts/agent_bench.py, plans/agent-v2/]
    acceptance:
      - Existing suite result recorded
      - Harness covers ask / 10-file inspect / check command / two sub-agents / computer loop
      - Baseline JSON committed under plans/agent-v2/benchmarks/
    evidence:
      - "uv run pytest tests -q -> 195 passed, 10 skipped, 28.6s @ a6f341d"
      - "plans/agent-v2/benchmarks/baseline.json committed (scenarios + event histograms)"
    notes: harness runs on scripted FakeAdapter; metrics are provider-independent
  - id: AG2-002
    title: Introduce Agent tool registry and typed tool contracts
    status: done
    depends_on: [AG2-001]
    owner: devin-session
    write_scope: [src/termx/agent/tools/, src/termx/agent/manager.py, src/termx/agent/providers.py, tests/test_agent_tools.py]
    acceptance:
      - ToolSpec carries mutability/parallel_safe/approval
      - Provider tools[] generated from registry; legacy names unchanged on the wire
      - Unknown tool names pause on the preserved "Unknown tool" approval; execution raises a structured failure, never a crash
    evidence:
      - "src/termx/agent/tools/registry.py: ToolSpec/ToolOutcome/ToolContext + decide_never/make_always/unknown_decision"
      - "providers.py turn() sources function tools from default_registry().provider_tools(read_only=...)"
      - "tests/test_agent_tools.py::test_provider_tools_read_only_filtering"
      - "tests/test_agent_tools.py::test_scheduler_groups covers unknown-call decision"
    notes: ProviderCall kept TYPE_CHECKING-only in tools/* to avoid cycle with providers.py
  - id: AG2-003
    title: Add structured filesystem and search tools
    status: done
    depends_on: [AG2-002]
    owner: devin-session
    write_scope: [src/termx/agent/tools/filesystem.py, src/termx/agent/tools/search.py, src/termx/agent/tools/paths.py, tests/test_agent_tools.py]
    acceptance:
      - list/read/search/write/apply_patch work without shell
      - Boundary + sensitive-path refusal covered by tests
      - Revision-guarded write conflicts return conflict result
    evidence:
      - "tools/filesystem.py: list_files/read_file(offset+limit+max_chars)/write_file(expected_revision)/apply_patch(dry_run, unified diff, per-file sensitive deny, all-validate-then-write)"
      - "tools/search.py: search_project over ProjectFiles.search"
      - "tests/test_agent_tools.py: read pagination/missing/sensitive, list, write create+conflict+read_only+sensitive, patch dry/apply/sensitive, search"
      - "uv run pytest tests/test_agent_tools.py -q -> 17 passed"
    notes: all paths resolve via ProjectFiles.resolve (project boundary); sensitive paths refused inline, never approval
  - id: AG2-004
    title: Add structured Git, preview and machine tools
    status: done
    depends_on: [AG2-002]
    owner: devin-session
    write_scope: [src/termx/agent/tools/git.py, src/termx/agent/tools/project.py, tests/test_agent_tools.py]
    acceptance:
      - git_status/diff/stage/commit/branch via git_ops on task cwd
      - git_push requires approval; preview_list/machine_info read-only
    evidence:
      - "tools/git.py: git_status/diff/stage/commit/branch/fetch/pull/push over termx.git_ops on ctx.cwd; push -> approval 'External publication'; mutations hidden in ask mode and refused inline"
      - "tools/project.py: preview_list (ProjectFiles.previews), machine_info (machine_snapshot)"
      - "tests/test_agent_tools.py::test_git_status (real git repo via git init)"
    notes: git_stage surfaces unstage flag; branch switch requires approval (Project mutation)
  - id: AG2-005
    title: Replace buffered Agent shell runner with streaming execution
    status: done
    depends_on: [AG2-001]
    owner: devin-session
    write_scope: [src/termx/agent/execution.py, src/termx/agent/tools/shell.py, tests/test_agent_tools.py, tests/test_agent.py]
    acceptance:
      - process.started/output/exited events arrive before exit
      - Output redacted before persistence; replay volume capped
      - run_check produces structured check result + log artifact for large output
    evidence:
      - "execution.py: stream_shell(on_output) with identical timeout/cancel/kill semantics; run_shell is a thin wrapper"
      - "tools/shell.py: process.started/output(capped 128k, redacted)/exited/cancelled/timed_out; run_check structured result {kind,command,status,duration_ms,summary,output,truncated,output_artifact>64KiB}"
      - "tests/test_agent_tools.py: test_run_check_streaming_events, test_run_check_failed_status, test_run_shell_ask_refusal"
      - "benchmarks: check_command proc events 0 -> 6; file_inspection_shell proc events 0 -> 30"
  - id: AG2-006
    title: Add scheduler with safe parallel read execution
    status: done
    depends_on: [AG2-002, AG2-003, AG2-004]
    owner: devin-session
    write_scope: [src/termx/agent/scheduler.py, src/termx/agent/manager.py, tests/test_agent_tools.py]
    acceptance:
      - Contiguous parallel-safe reads execute concurrently (wall-clock lower)
      - Approval pause/resume identical to sequential path
      - Mutations/computer calls stay serialized
    evidence:
      - "scheduler.py: contiguous parallel-safe+no-approval calls batch into groups; writes/computer/unknown serialize; batches emit tool.batch + gather, ordered results"
      - "benchmarks phase1.json: file_inspection_structured emits tool.batch:1 and completes (baseline failed)"
      - "tests/test_agent_tools.py::test_scheduler_groups asserts group shapes [[r,r],[shell],[r],[computer],[unknown]]"
      - "approval pause/resume path unchanged (same payload keys, approved_first skip on singleton)"
  - id: AG2-007
    title: Add Context Engine v1 and project snapshots
    status: done
    depends_on: [AG2-003, AG2-004]
    owner: devin-session
    write_scope: [src/termx/agent/context/, src/termx/agent/manager.py, tests/test_agent_tools.py]
    acceptance:
      - Compact snapshot replaces manifest in provider input
      - workspace_manifest import path unchanged
      - Oversized tool outputs slimmed before re-entering transcript
    evidence:
      - "context/engine.py: project_snapshot (files+tree+recent+manifests+languages+commands+git), cached per task, invalidated on successful write/computer mutation"
      - "DEFAULT_CONTEXT_LIMITS {context 120k, tool output 50k/turn, history 160 events, 4 images}; slim_history caps function_call_output + trims old images"
      - "providers.py _task_input renders 'Project snapshot' section when manifest.kind == project_snapshot"
      - "manager emits context.snapshot event at task creation; tests/test_agent_tools.py::test_context_engine"
      - "smoke run: context.snapshot event emitted; model used list_files/read_file directly"
  - id: AG2-009
    title: Refactor provider runtime to persistent HTTP clients
    status: done
    depends_on: [AG2-001]
    owner: devin-session
    write_scope: [src/termx/agent/runtime.py, src/termx/agent/providers.py, src/termx/agent/manager.py]
    acceptance:
      - One AsyncClient per provider id, reused across turns
      - Closed on AgentManager.close; deletion evicts
    evidence:
      - "runtime.py ProviderHttpRuntime: client_for(provider_id) pooled AsyncClient; save_provider/delete_provider evict; close() aclose()s all"
      - "providers.py adapter accepts injected client (ephemeral fallback preserved for direct tests)"
      - "tests/test_agent_tools.py::test_http_runtime"
  - id: AG2-014
    title: Add task metrics and performance evidence
    status: done
    depends_on: [AG2-005, AG2-006]
    owner: devin-session
    write_scope: [src/termx/agent/metrics.py, src/termx/agent/store.py, src/termx/agent/manager.py]
    acceptance:
      - tasks.metrics additive column; summary persisted at completion
      - task.metrics event emitted; no secrets in metrics
    evidence:
      - "metrics.py TaskMetrics: provider latency/first/count, tool ms by name, shell ms, screenshots, tokens, subagents, approval wait"
      - "store.py: tasks.metrics additive column + migration; manager persists snapshot on completed/failed/cancelled and emits task.metrics"
      - "tests/test_agent_tools.py::test_metrics; smoke run populated real metrics incl. provider_ms/input_tokens"
    notes: handoff deps also list AG2-010 (Phase 2); v1 metrics do not need it
  - id: VRF-P1
    title: Phase-1 verification — benchmarks, migration, full suite
    status: done
    depends_on: [AG2-003, AG2-004, AG2-005, AG2-006, AG2-007, AG2-009, AG2-014]
    owner: devin-session
    write_scope: [plans/agent-v2/, tests/]
    acceptance:
      - After-benchmarks show structured-tool improvement vs baseline
      - Old-DB migration path verified
      - Full pytest suite green; openrouter smoke if env key present
    evidence:
      - "uv run pytest tests -q -> 218 passed, 10 skipped, 24.4s"
      - "uv run python scripts/agent_bench.py --out plans/agent-v2/benchmarks/phase1.json --label phase1 -> structured scenario now completes (was unsupported-provider-tool failure), tool.batch emitted, process.* streaming events present"
      - "old-schema DB migration verified: AgentStore on a tasks table without metrics column -> ALTER applied, get_task returns metrics:{} , update persists"
      - "scripts/agent_smoke.py against OpenRouter (openrouter/free): task completed via list_files+read_file only; metrics persisted; no secrets in events"
    notes: cancellation/takeover covered by existing tests/test_agent.py (33 pass); redaction verified — shell public() redacts, process.output chunks redact, run_check uses redacted output
  # ---- Later phases (ledgered, not in this delivery) ----
  - id: AG2-008
    title: Context budgeting and compaction checkpoints
    status: pending
    depends_on: [AG2-007]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 2 — durable checkpoint summaries
  - id: AG2-010
    title: Provider streaming and bounded retry/backoff
    status: pending
    depends_on: [AG2-009]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 2
  - id: AG2-011
    title: Abstract continuation strategy and recovery state
    status: pending
    depends_on: [AG2-008, AG2-010]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 2
  - id: AG2-012
    title: Convert subagents to asynchronous child handles
    status: pending
    depends_on: [AG2-006]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 3 — spawn returns immediately; await_subagents/status/cancel
  - id: AG2-013
    title: Concurrent fan-out/fan-in subagent orchestration
    status: pending
    depends_on: [AG2-012]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 3 — max_parallel_subagents 3 / total 8 defaults
  - id: AG2-015
    title: Implement Computer Observation v2
    status: pending
    depends_on: [AG2-002]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 4
  - id: AG2-016
    title: Add efficient paste and richer computer actions
    status: pending
    depends_on: [AG2-015]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 4
  - id: AG2-017
    title: Resumable task checkpoints without replaying side effects
    status: pending
    depends_on: [AG2-011]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: Phase 2/6 prerequisite
  - id: PROD-001..006 / SEC-001..002 / VRF-001
    title: Host conversations, custom agents, worktrees, activity, ports, runbooks, scopes v2, final verification
    status: pending
    depends_on: [AG2-017]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: see handoff section 23 for full dep graph
```
