# Execution graph: agent-v2

## Resume checkpoint
- Updated: 2026-09-29
- Current phase: Phase 4 complete (AG2-015 Observation v2, AG2-016 richer computer actions); PR #16 open (ready for review)
- Next ready tasks: PROD/SEC/VRF scope (host conversations, custom agents, worktrees, Activity Center, port/process discovery, runbooks, scopes v2, final verification)
- Active owners: devin-session
- Last verified command: `uv run pytest tests -q` — 288 passed, 10 skipped, ~27s
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
      - "tools/filesystem.py: list_files/read_file(offset+limit+max_chars)/write_file(expected_revision|O_EXCL create)/apply_patch(custom bounded unified-diff: dry_run, delete/rename, CRLF preserve, ±10-line unique match, temp+os.replace rollback, per-file sensitive deny)"
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
      - Evicted clients retire (300s deferred close with a running loop; in-flight turns finish), never eager-close
    evidence:
      - "runtime.py ProviderHttpRuntime: client_for(provider_id) pooled AsyncClient; evict() retires to deferred close; aclose() drains live+retired and cancels pending"
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
      - "uv run pytest tests -q -> 246 passed, 10 skipped, 25.34s (includes Devin Review rounds 1-3 regression coverage)"
      - "uv run python scripts/agent_bench.py --out plans/agent-v2/benchmarks/phase1.json --label phase1 -> structured scenario now completes (was unsupported-provider-tool failure), tool.batch emitted, process.* streaming events present"
      - "old-schema DB migration verified: AgentStore on a tasks table without metrics column -> ALTER applied, get_task returns metrics:{} , update persists"
      - "scripts/agent_smoke.py against OpenRouter (openrouter/free): task completed via list_files+read_file only; metrics persisted; no secrets in events"
    notes: cancellation/takeover covered by existing tests/test_agent.py (33 pass); redaction verified — shell public() redacts, process.output chunks redact, run_check uses redacted output
  # ---- Later phases (ledgered, not in this delivery) ----
  - id: AG2-008
    title: Context budgeting and compaction checkpoints
    status: done
    depends_on: [AG2-007]
    owner: devin
    write_scope:
      - src/termx/agent/store.py (checkpoints table + CRUD)
      - src/termx/agent/context/engine.py (plan_compaction, estimate_tokens)
      - src/termx/agent/manager.py (compaction wiring in _drive)
    acceptance:
      - "compaction triggers at >=75% of max_context_estimate_tokens, keeps head user item + retain tail, never splits call->output pairs"
      - "summary shape {summary, completed_steps, important_files, decisions, pending_work, git_state, approvals, artifacts, last_safe_execution_checkpoint, created_at} persisted as checkpoints(kind='context')"
      - "events context.compaction.started/completed + context.checkpoint emitted"
    evidence:
      - "uv run pytest tests -q -> 262 passed, 10 skipped (bb4b032)"
      - "test_agent_tools.py: test_plan_compaction_below_threshold, test_plan_compaction_preserves_pairs_and_bounds, test_plan_compaction_records_unresolved_calls, test_context_checkpoint_store_roundtrip"
    notes: Phase 2 — deterministic summarizer (no model call); unresolved calls described in pending_work, not re-injected
  - id: AG2-010
    title: Provider streaming and bounded retry/backoff
    status: done
    depends_on: [AG2-009]
    owner: devin
    write_scope:
      - src/termx/agent/providers.py (supports_streaming, stream_turn SSE, ProviderError status/retry_after/network)
      - src/termx/agent/retry.py (new: classify + retrying, bounded backoff+jitter, cancel-aware)
      - src/termx/agent/metrics.py (provider_first_token_ms/stream_ms/output_tokens/retries/rate_limited)
      - src/termx/agent/manager.py (streaming turn + retry wrap in _drive)
    acceptance:
      - "capability-based stream_turn with turn() fallback; SSE deltas -> assistant.delta / provider.tool_call.delta; provider.stream.started/completed events"
      - "bounded 3 attempts, exp backoff+jitter, Retry-After honored (429), cancel-aware, non-retryable 4xx fails fast, no retry after tool dispatch"
      - "no secrets/Authorization in events or persisted state"
    evidence:
      - "uv run pytest tests -q -> 262 passed, 10 skipped (d4d9044)"
      - "test_agent_tools.py: test_stream_turn_parses_sse, test_stream_turn_error_status_raises, test_retry_classification, test_retrying_retries_and_emits, test_retrying_no_retry_on_401, test_retrying_cancel_during_backoff"
    notes: Phase 2
  - id: AG2-011
    title: Abstract continuation strategy and recovery state
    status: done
    depends_on: [AG2-008, AG2-010]
    owner: devin
    write_scope:
      - src/termx/agent/recovery.py (new: recovery_decision, public_checkpoint)
      - src/termx/agent/store.py (ACTIVE_STATUSES += recovering, recovery_confirmation_required)
      - src/termx/agent/manager.py (restart classification, recover_task, _resume_execution)
      - src/termx/app.py (POST /api/agent/tasks/{id}/recover + lifespan auto-resume)
    acceptance:
      - "checkpoint payload mirrors approval-payload shape {call, remaining_calls, history, step, started_at} so resume reuses the _drive/_run_calls pattern"
      - "new statuses/events additive; ambiguous side effects never auto-replayed; resumable tasks resume via lifespan + explicit endpoint"
    evidence:
      - "uv run pytest tests -q -> 262 passed, 10 skipped (1771901)"
      - "test_agent.py: test_recovery_* (7 tests) + test_restart_fails_task_without_checkpoint + test_execution_checkpoint_events_during_normal_run"
    notes: Phase 2
  - id: AG2-012
    title: Convert subagents to asynchronous child handles
    status: done
    depends_on: [AG2-006]
    owner: devin
    write_scope:
      - src/termx/agent/manager.py (_SubagentHandle, _watch_child, _subagent_handles, _spawn_subagent async)
      - src/termx/agent/tools/subagents.py (await_subagents / subagent_status / cancel_subagent specs)
      - src/termx/agent/store.py (tasks.parent_id column + children())
    acceptance:
      - "spawn_subagent returns {ok,status:running,task_id} immediately; child runs concurrently"
      - "await_subagents waits all/named children w/ timeout -> per-child status+result; subagent_status read-only; cancel_subagent stops named children"
      - "child escalation approvals stay resolvable after parent reaches a terminal state"
      - "handles rebuild from durable parent_id linkage after restart"
    evidence:
      - "uv run pytest tests -q -> 276 passed, 10 skipped (e410e97)"
      - "test_agent.py: test_spawn_subagent_returns_async_handle, test_subagent_fanout_and_await_collects_results (2 children started before either finished), test_subagent_status_and_cancel, test_await_subagents_timeout_reports_partial, test_subagent_handles_rebuilt_from_store, escalation test proves post-terminal resolvability"
      - "old-DB migration check: parent_id column added additively; children() query verified"
    notes: Phase 3 — per-child watcher relays events/escalations independent of the parent's drive; subagent.started/event/finished/awaited/cancelled events on the parent stream
  - id: AG2-013
    title: Concurrent fan-out/fan-in subagent orchestration
    status: done
    depends_on: [AG2-012]
    owner: devin
    write_scope:
      - src/termx/agent/manager.py (_limits + parallel/total spawn guards)
      - src/termx/agent/tools/legacy.py (spawn description teaches fan-out vocabulary)
    acceptance:
      - "max_parallel_subagents=3 (clamp 1-8) and max_subagents_total=8 (clamp 1-32) in task limits; spawn refuses with actionable error when exhausted"
      - "multiple children run truly concurrently; await fans results back in"
    evidence:
      - "test_agent.py: test_subagent_parallel_limit_blocks_spawn (running>=1 -> second spawn ok:False 'parallel limit'), test_subagent_total_limit_blocks_spawn (total>=1 -> ok:False 'total limit'), fanout test asserts both subagent.started precede the first subagent.finished"
      - "uv run pytest tests -q -> 276 passed, 10 skipped (e410e97)"
    notes: Phase 3 — children inherit cwd/provider/model/limits; consequential child actions still escalate to the parent/user (no scope widening)
  - id: AG2-015
    title: Implement Computer Observation v2
    status: done
    depends_on: [AG2-002]
    owner: devin
    write_scope:
      - src/termx/agent/observation.py (new: Observation schema, ObservationTracker, frame identity)
      - src/termx/agent/manager.py (_execute_computer: batch_id, computer.action.*, computer.observation/no_change/control.changed events, dedup)
    acceptance:
      - "observation {id, display_id, width, height, dpr, captured_at, artifact_id, frame_hash, changed, control_owner, capture_backend} emitted per batch; frame pixels via JPEG SOF, dpr=px/logical width"
      - "unchanged frames emit computer.no_change and produce text-only model follow-up on the use_computer path (no duplicate input_image); every frame still persisted as a screenshot artifact for replay"
      - "computer.action.started/finished share a stable batch_id; finished reports ok/error incl. cancelled; control.changed fires on takeover->user and next-batch->agent"
      - "capture/input backends unchanged — observation layer is post-capture and backend-agnostic; Pillow-gated paths (aHash, downscale, crop) fall back honestly (sha256, full frame) on hosts without it"
    evidence:
      - "uv run pytest tests -q -> 288 passed, 10 skipped"
      - "test_agent.py: test_computer_observation_schema_and_batch_events, test_computer_no_change_suppresses_duplicate_frame (2 obs, changed=[True,False], no input_image after call computer-2, 2 artifacts), test_computer_control_changed_on_takeover, test_frame_identity_dedupes_identical_frames, test_jpeg_size_parses_sof_marker"
    notes: Phase 4 — model-bound frame downscaled (TERMX_MODEL_IMAGE_MAX_PX, default 1568) only where Pillow can decode; native computer_call_output keeps the screenshot per API contract and gets the no-change signal via an adjacent user message
  - id: AG2-016
    title: Add efficient paste and richer computer actions
    status: done
    depends_on: [AG2-015]
    owner: devin
    write_scope:
      - src/termx/agent/computer.py (paste_text via clipboard_set+verify+paste chord with per-char fallback; mouse_down/up; key_down/up; release_all action; set_display)
      - src/termx/agent/providers.py (use_computer schema: new action kinds, key/display_id/region props, description teaches paste-first)
      - src/termx/agent/policy.py (paste_text text joins the secret scan alongside type)
      - src/termx/machine.py (capabilities: computer_observation_v2, agent_subagents)
    acceptance:
      - "paste_text writes the clipboard, verifies via read-back, and sends the native paste chord (meta+v macOS, control+v elsewhere); falls back to per-character typing when the clipboard bridge is unavailable"
      - "mouse_down/mouse_up emit pointer down/up with normalized coords+button; key_down/key_up emit key down/up; release_all reachable as an action; set_display switches target display"
      - "machine capabilities advertise computer_observation_v2: true; paste_text gets the same credential/secret approval scan as type"
    evidence:
      - "test_agent.py: test_paste_text_uses_clipboard_chord, test_paste_text_falls_back_to_typing, test_mouse_and_key_hold_actions, test_paste_text_secret_scanned_like_type, test_computer_region_screenshot_records_region, test_machine_snapshot_reports_observation_v2"
      - "uv run pytest tests -q -> 288 passed, 10 skipped"
    notes: Phase 4 — screenshot actions accept region {x,y,width,height}; crop is post-capture and Pillow-gated (full frame served with region_cropped=false when undecodable)
  - id: AG2-017
    title: Resumable task checkpoints without replaying side effects
    status: done
    depends_on: [AG2-011]
    owner: devin
    write_scope:
      - src/termx/agent/manager.py (prepared->running->completed_uncommitted->committed wrapper in _run_calls)
      - src/termx/agent/store.py (checkpoints table, kind='execution', side_effect_state)
    acceptance:
      - "side-effecting serial calls checkpointed at all 4 boundaries; crash-safe resume: prepared->re-execute once, committed->continue, completed_uncommitted->reuse stored result (+tool.finished dedup), running/unknown->confirm_required"
      - "no duplicate side effects on resume; resumable=0/not_resumable -> legacy fail"
    evidence:
      - "uv run pytest tests -q -> 262 passed, 10 skipped (1771901)"
      - "test_agent.py crash-boundary tests: prepared(1 write), running(confirm gate, 0 writes until confirmed), completed_uncommitted(0 writes, 1 tool.finished), committed(0 writes), events order prepared>running>completed_uncommitted>committed"
    notes: Phase 2/6 prerequisite
  - id: PROD-001
    title: Persist conversations on host
    status: done
    depends_on: []
    owner: devin
    write_scope:
      - src/termx/agent/store.py (conversations + conversation_turns + conversation_context_refs tables, CRUD + add_conversation_turn)
      - src/termx/app.py (GET/POST/PATCH/DELETE /api/conversations, POST /{id}/turns, AgentTaskBody.conversation_id auto-links a turn)
      - src/termx/machine.py (capabilities.conversations)
    acceptance:
      - "conversations carry id/title/project_id/cwd/pinned/archived/draft/mode/custom_agent_id/provider_id/model + timestamps; list defaults to non-archived (archived=1|all filters)"
      - "turns carry sequence/task_id/prompt/mode/provider/model + context_refs & attachment_refs rows; cascade delete with conversation"
      - "POST /api/agent/tasks with conversation_id validates existence first (404) then appends a turn with task_id/prompt/attachments"
    evidence:
      - "test_agent.py: test_conversations_crud_and_turns (401 gate, CRUD, refs split, archived filter, cascade delete), test_conversation_turn_links_task_creation (404 bad id, auto-turn on create)"
      - "uv run pytest tests -q -> 291 passed, 10 skipped"
    notes: scopes use current model — agent-view reads / agent-control writes; SEC-002 retargets to scopes v2
  - id: PROD-002
    title: Persist reusable custom agents on host
    status: done
    depends_on: [PROD-001]
    owner: devin
    write_scope:
      - src/termx/agent/store.py (custom_agents table + CRUD)
      - src/termx/app.py (GET/POST/PATCH/DELETE /api/custom-agents)
      - src/termx/machine.py (capabilities.custom_agents)
    acceptance:
      - "custom agents carry name/description/instructions/provider_id/model/tools[]/limits{} + timestamps; name required"
      - "full CRUD via REST, 404 on missing id, auth required"
    evidence:
      - "test_agent.py: test_custom_agents_crud (401 gate, create/list/patch/delete, tools+limits round-trip, blank name rejected)"
      - "uv run pytest tests -q -> 291 passed, 10 skipped"
    notes: ""
  - id: PROD-003..006 / SEC-001..002 / VRF-001
    title: Worktrees, activity, ports, runbooks, scopes v2, final verification
    status: pending
    depends_on: [AG2-017, PROD-001]
    owner: unassigned
    write_scope: []
    acceptance: []
    evidence: []
    notes: see handoff section 23 for full dep graph
```
