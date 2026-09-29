# Agent v2 — Technical specs

## 1. Tool registry (`src/termx/agent/tools/registry.py`)

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                          # wire name ("read_file")
    description: str
    parameters: dict[str, Any]         # JSON schema for function tools
    mutability: str                    # "read" | "write" | "computer" | "external"
    parallel_safe: bool
    approval: str                      # "never" | "policy" | "always"
    decide: Callable[[ProviderCall, ToolContext], PolicyDecision]
    execute: Callable[[ProviderCall, ToolContext], Awaitable[ToolOutcome]]
```

- `ToolContext`: `task_id`, `cwd`, `task` (store row), `cancel`,
  `read_only`, plus manager services (store, computer, subagent spawner,
  emit).
- `ToolOutcome`: `{result: dict, items: list[dict]}` — `result` is the
  event-persisted payload (already redacted), `items` are transcript entries
  appended to history (`function_call_output` etc.).
- `registry.provider_tools(read_only, allow_computer, native_computer)`
  yields the `tools[]` array for the provider; computer stays a separate
  (non-registry) special case as today.
- `decide` returns the same `PolicyDecision` shape; `approval="always"`
  short-circuits to approval-required, `"never"` → no approval, `"policy"`
  defers to the spec's `decide`.
- Ask mode: registry filters to `mutability == "read"` tools + `share_file`
  + `run_shell` (read-only prompt wording), matching today's surface plus
  the new read tools. `write`/`computer`/`spawn_subagent` are excluded;
  if a model still names them it gets an inline refusal (same as today).

## 2. Filesystem tools (`tools/paths.py`, `tools/filesystem.py`)

- `resolve_in_project(root, raw) -> Path`: absolute or relative input;
  `..` components, absolute paths outside root, and symlinks resolving
  outside root are refused; missing files error per tool.
- Sensitive names: `is_sensitive_path` + `context.SECRET_NAMES` → refused
  inline (`{"ok": False, "refused": True, ...}`), never an approval prompt
  whose payload would expose the path's purpose to the model again.
- `list_files {path, depth<=4, include_hidden}` → entries `{path, dir,
  size, mtime}`; honors `SKIP_DIRS` + `.gitignore` patterns like
  `workspace_manifest`.
- `read_file {path, start_line, end_line, max_bytes<=256k}` → `{path,
  total_lines, content, revision, truncated, encoding}`; binary/oversize
  → `reason` instead of content (same rules as `ProjectFiles.read`).
- `write_file {path, content, expected_revision}` → 409-style conflict
  result `{ok: False, conflict: True, current_revision}` when the revision
  mismatches; atomic temp+replace preserving mode.
- `apply_patch {patch, dry_run?}` → custom unified-diff parser (NOT
  `git apply`): each `---/+++` section resolves inside the project with
  sensitive paths refused inline; hunks match within a bounded ±10-line
  window and must be unique (stale/ambiguous → error). Supports
  `+++ /dev/null` deletes and `a/`→`b/` renames, `\ No newline at end of
  file`, and preserves the source's CRLF/LF convention. All sections
  validate before any write; commits are temp-file + `os.replace` with
  rollback on failure. Returns `{ok, dry_run, changed_paths, changes:[
  {path, added, removed, hunks, created, deleted, renamed_from}]}`. Works
  without git installed (pure working-tree apply).

## 3. Search (`tools/search.py`)

- `search_project {query, path=".", mode="content|filename",
  case_sensitive}` — same walk rules/caps as `ProjectFiles.search`
  (20k visited, 5s deadline, 500 results); content mode returns
  `{path, line, text, revision}`.

## 4. Git / project tools (`tools/git.py`, `tools/project.py`)

- `git_status {}`, `git_diff {path?, staged?, range?}`, `git_stage
  {paths, unstage?}`, `git_commit {message}`, `git_branch {name?, create?,
  switch?}`, `git_fetch {}`, `git_pull {}`, `git_push {}`.
- Read ops (`status`, `diff`, `branches`) run via `git_ops` in a thread;
  mutating ops run through `_run_mutating` (`create_subprocess_exec`,
  own process group, `ctx.cancel`-aware kill, timeout) since
  `asyncio.to_thread` cannot interrupt a git subprocess.
  Status payloads are sanitized (`_sanitize_status` drops sensitive
  filenames); `git_stage` expands directory args via porcelain status and
  refuses if any affected path is sensitive.
  Mutating ops are `mutability="write"`, `parallel_safe=False`.
- Approvals: `git_push` → `approval="always"` (external publication);
  fetch/pull are `"policy"` and evaluate to no-approval (matches today's
  shell policy which does not gate them).
- `preview_list {}` → registered previews for the task's project
  (project id derived from cwd exactly like `ProjectFiles.register`; no
  implicit registration). `machine_info {}` → hostname/os/arch/shells/
  git-availability — no secrets, no env dump.
- `process_list`/`port_list` are deferred to PROD-005 (Phase 7) — the
  scoped port-discovery service does not exist yet on this host.

## 5. Streaming runner (`agent/execution.py` + `tools/shell.py`)

- `stream_shell(command, cwd, *, timeout_s, cancel, on_output) ->
  ShellResult`: same spawn/env-scrub/process-group semantics as
  `run_shell`; reads `stdout` in 4 KiB chunks, invokes
  `on_output(bytes)` per chunk (callback re-raises → cancel).
- `run_shell` gains the same streaming path internally with a null sink —
  one implementation, two surfaces.
- Events (emitted by the shell tool):
  `process.started {call_id, command(redacted), cwd, pid}`,
  `process.output {call_id, chunk(redacted, ≤8KiB), stream:"combined"}`,
  `process.exited {call_id, exit_code, duration_ms, truncated}`,
  `process.cancelled`, `process.timed_out`.
- Replay volume is capped: at most `OUTPUT_LIMIT` bytes of `process.output`
  payload per call; further output increments a dropped-bytes counter in
  the final event. Full tail still lands in `tool.finished.result` like
  today.
- `run_check {kind: test|lint|typecheck|build|other, command, timeout_s}`
  runs the same runner; result `{kind, command, status: passed|failed|
  error, duration_ms, summary, output, truncated}`; when output exceeds
  ~64 KiB the tail is stored as a `log` artifact and `output_artifact`
  references it.

## 6. Scheduler (`agent/scheduler.py`)

- Input: ordered `ProviderCall`s + their `PolicyDecision`s.
- Greedy partition: iterate; while calls are function-calls whose spec is
  `parallel_safe` and not approval-required, accumulate into a batch; a
  non-conforming call ends the batch and runs serially. `computer` calls
  and approvals always break the batch.
- Batch executes `asyncio.gather` (bounded by the batch size; reads are
  local so no extra semaphore in v1); outcomes append to history in the
  original call order; one `tool.batch {call_ids}` event per batch >1.
- An approval-required call pauses after its predecessors — identical to
  the current sequential pause, including the private-payload flow for
  `_resume_approved`.
- Ask mode: same path; read tools are already `parallel_safe`, writes are
  refused inline inside `execute` (not via scheduler).

## 7. Context Engine v1 (`agent/context/`)

- `manifest.py` = today's `workspace_manifest` moved verbatim
  (`workspace_manifest` re-exported for compatibility).
- `engine.project_snapshot(root)` →
  `{name, root, git: {repo, branch, dirty_files, ahead, behind},
  manifests: [{path, kind, name?, scripts?}], languages: [...],
  commands: {test?, lint?, typecheck?, build?}, tree: [top-level entries
  depth<=2], recent: [rel paths by mtime]}` — bounded (~4 KB), secret-name
  files excluded, `.gitignore`/SKIP_DIRS honored.
- `ContextEngine.build(task)` → snapshot dict stored on
  `runtime["snapshot"]`; refreshed lazily (TTL ~30 s or after write tools)
  so edits don't go stale the way the old manifest did.
- `ContextEngine.slim_history(items)` → enforces budgets
  (`max_tool_output_chars_per_turn` default 50 000): oversized
  `function_call_output` payloads are truncated with a
  `…[truncated N chars]` marker; image payloads count toward
  `max_images_in_context` (4) — oldest dropped (artifacts stay in replay).
- `_task_input` renders the snapshot (not 500 filenames) when it receives
  the new dict shape — `files` key absent ⇒ snapshot renderer; keeps the
  old manifest renderer for compatibility.

## 8. Provider runtime (`agent/runtime.py`)

```python
class ProviderHttpRuntime:
    def client_for(self, key: str, *, timeout_s: float,
                   headers: dict[str,str]) -> httpx.AsyncClient
    def evict(self, key: str) -> None   # deferred close, see below
    async def aclose(self) -> None
```

- One client per provider id (timeout + auth header bound at creation).
- `OpenAIResponsesAdapter(client=None)` — injected client ⇒ reuse, never
  close it; `None` ⇒ current per-call client (keeps unit tests valid).
- Manager `_default_adapter` passes the pooled client; `close()` calls
  `runtime.aclose()`.
- Provider save/delete calls `evict(provider_id)`: the client moves to a
  `_retired` set and is closed after a grace period (`_EVICT_GRACE_S`,
  300 s) when an event loop is running — an in-flight turn finishes on the
  old client rather than erroring mid-request. With no loop (synchronous
  save) the retired client is held for `aclose()`. Pending close tasks are
  tracked and cancelled on shutdown.

## 9. Metrics (`agent/metrics.py`)

- `TaskMetrics.record_*`: `provider_request(ms)`, `tool(name, ms)`,
  `shell(ms)`, `screenshot(bytes)`, `usage(dict)`, `approval_wait(ms)`.
- Persisted on `tasks.metrics` (new nullable JSON column via additive
  `ALTER TABLE`; `TASK_FIELDS += "metrics"`) at completion/failure and
  flushed periodically on long runs.
- Emit `task.metrics` event with the final summary.

## 10. Capability flags (`machine_snapshot`)

`agent_tools_v2`, `agent_context_v2`, `agent_parallel_tools`,
`agent_streaming` (Phase 2), `agent_recovery` (Phase 2) — all `true`.

# Phase 2 — execution/recovery layer (AG2-008/010/011/017)

## 11. Checkpoints (`agent/recovery.py` + `store.checkpoints` table)

One durable table carries both checkpoint kinds — `kind` column is
`"execution"` (AG2-011/017) or `"context"` (AG2-008):

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- execution | context
  history_cursor INTEGER NOT NULL DEFAULT 0,
  plan_step INTEGER NOT NULL DEFAULT 0,
  pending_call_id TEXT,
  side_effect_state TEXT NOT NULL DEFAULT 'none',
                                    -- none|prepared|running|completed_uncommitted|committed
  payload TEXT NOT NULL DEFAULT '{}', -- resume payload or context summary
  result TEXT,                        -- stored tool result (completed_uncommitted)
  resumable INTEGER NOT NULL DEFAULT 1,
  reason TEXT,
  provider_turn_id TEXT,
  created_at REAL NOT NULL
)
```

Add additive migration mirroring the `tasks.metrics` precedent (try/except
`ALTER`— CREATE TABLE IF NOT EXISTS is itself additive). Store methods:
`create_checkpoint(task_id, kind, **fields)`, `update_checkpoint(id, **changes)`,
`latest_checkpoint(task_id, kind=None)`, `checkpoints(task_id, kind=None)`.

## 12. Safe resume state machine (AG2-011/017)

Execution checkpoints are written only in the **serial** path of
`_run_calls` and only for calls whose spec `mutability != "read"`
(parallel batches admit only `parallel_safe` reads, so side effects are
always serial). The checkpoint's `payload` mirrors the approval
private-payload shape — `{call, remaining_calls, history, step,
started_at, task_id}` — so recovery reuses the `_resume_approved`
driving pattern:

```
prepared ──invoke begins──> running ──outcome lands──> completed_uncommitted
(result row written, tool.finished event emitted) ──> committed
(history items appended + event ledger durable)
```

- `prepared` at dispatch; `running` set immediately before `execute`;
- `completed_uncommitted` + `result={finished, output_items}` written
  **after the side effect completes but before** the result is fed to
  history; `committed` after emit + history extend. `committed` does NOT
  rewrite `payload["history"]` — resume reconstructs post-call history
  from the pre-call snapshot plus the stored `result.output_items`
  (call-id dedupe), so each call writes one history blob.
- `execution.checkpoint` events emit only at the `prepared` and
  `committed` boundaries (2 events per call); intermediate transitions
  remain in the durable row for crash classification.
- Startup recovery: `__init__` scans ACTIVE tasks (today: all → failed).
  With a resumable latest execution checkpoint the task goes to
  `recovering`/`recovery_confirmation_required` and is not failed:
  - `prepared` → resume safely (side effect never started) → status
    `recovering` then drive from payload; emit `task.recovery.started`,
    `task.recovery.resumed`, `execution.checkpoint`.
  - `committed` → resume from the checkpoint `payload` history plus the
    stored `result.output_items` (history is not rewritten at commit) →
    drive `remaining_calls`.
  - `completed_uncommitted` + stored `result` → resume **without
    re-executing**: append stored `output_items` to payload history,
    re-emit `tool.finished` only if it is not already in the event log
    (a crash can land between `tool.finished` and `committed`), mark
    checkpoint `committed`, drive `remaining_calls`.
  - `running` / `unknown` → `recovery_confirmation_required`; emit
    `task.recovery.blocked`. Confirming calls `recover_task(confirm=True)`
    which re-drives from `prepared` semantics (documented as accepting
    possible double-effect) — never automatic.
  - `resumable=0` → failed with `reason`.
- New statuses (additive): `recovering`,
  `recovery_confirmation_required` — both in `ACTIVE_STATUSES` so
  cancel/takeover still apply; `resumed` is transient (task returns to
  `running` once driving) and is emitted as `task.recovery.resumed`.
- `AgentManager.recover_task(task_id, confirm=False)` + additive route
  `POST /api/agent/tasks/{task_id}/recover` (`{"confirm": bool}`).
  Events: `execution.checkpoint`, `task.recovery.started`,
  `task.recovery.resumed`, `task.recovery.blocked`.

## 13. Provider streaming + bounded retry (AG2-010)

- Optional adapter capability: `supports_streaming=True` +
  `stream_turn(**same kwargs, on_delta) -> ProviderTurn`. Manager calls
  it when present; otherwise falls back to `turn()` — fakes and the
  non-streaming providers unchanged.
- `OpenAIResponsesAdapter.stream_turn` posts the same payload with
  `stream: true` via `client.stream()`, parses SSE `data:` lines
  (`response.output_text.delta` → progressive text;
  `response.completed`/`response.incomplete` → final body parsed by the
  existing `turn()` output pipeline). `function_call` argument deltas
  need no incremental assembly on this API — complete call items arrive
  in the terminal event; still emits `provider.tool_call.delta`
  placeholders only if the API supplies call deltas (kept additive).
- Manager emits per logical turn a `stream_id` + per-request `attempt`:
  `provider.stream.started {model, stream_id, attempt}` (emitted per
  attempt), `assistant.delta {text, stream_id, attempt}`,
  `provider.tool_call.delta {stream_id, attempt, ...}`,
  `provider.stream.aborted {stream_id, attempt, reason_class}` on every
  abandoned attempt, `provider.stream.completed {response_id, stream_id,
  attempts, first_token_ms, stream_ms}`; final `assistant.message` still
  emitted (unchanged contract).
- Retry is suppressed once a delta is visible (`may_retry=not
  first_token`): restarting a visible stream would duplicate text in
  replay, so the attempt fails instead (`provider.failed
  {retry_suppressed: true}`). Pre-delta failures still retry normally.
- `response.incomplete` (and non-streaming `status:"incomplete"`) is a
  structured failure — `_turn_from_body` raises `ProviderError` naming
  `incomplete_details.reason`; partial output is never treated as a
  completed turn.
  Cancellation: the stream task sits inside `_race_cancel` — a cancel
  closes the in-flight response.
- Retry (`agent/retry.py`): `classify(exc) ->
  {kind: retryable|rate_limited|non_retryable, status_code?, retry_after_s?}`
  (network errors, 408, 5xx → retryable; 429 + 5xx-with-Retry-After →
  rate_limited honors Retry-After capped at 30s; 400/401/403/404/422
  → non_retryable). `ProviderError` gains `status_code`. `retrying(fn,
  emit, cancel)` → ≤3 attempts, exponential backoff 0.5s→8s +20% jitter,
  cancellation-aware sleeping. Events: `provider.retry {attempt,
  max_attempts, reason_class, status_code, delay_ms}`,
  `provider.rate_limited`, `provider.failed`. Never retries after a
  dispatched tool side effect (retry wraps only the provider call).
- `ProviderHttpRuntime` is untouched; no secrets/Authorization headers
  enter events — payloads carry classes + status codes only.
- Metrics: `record_provider_stream(first_token_ms, stream_ms)` and
  `provider_output_tokens` surfaced in `tasks.metrics`.

## 14. Context compaction checkpoints (AG2-008)

`ContextEngine.plan_compaction(items, *, git_state=None, keep_recent=40) ->
{history, payload, history_cursor, dropped}` — called by the manager
before each provider turn after `slim_history`; the manager emits the
events, fills in approvals/artifacts/`last_safe_execution_checkpoint`,
and persists the `kind="context"` checkpoint row:

- Trigger: `estimate_tokens(history) >= 0.75 * max_context_estimate_tokens`.
- Split point: find the newest index `i` where `history[i:]` contains no
  orphaned call → `*_call_output` needs never severed; everything `< i`
  is compacted into a deterministic structured summary —
  `{summary, completed_steps, important_files, decisions, pending_work,
  git_state, approvals, artifacts, last_safe_execution_checkpoint,
  created_at}` — built from the transcript itself (tool.finished error
  lines, file paths in call args, last user instruction, current plan
  step) plus the live `project_snapshot().git_state`; no model call in
  v1 (deterministic + cheap; a provider-assisted summarizer can slot
  behind the same function later).
- The summary is injected after the original user prompt item as a
  plain user message — `{"role": "user", "content": <json>}` where the
  JSON body carries `{type: "context_checkpoint", checkpoint_id,
  summary, compacted_items}`. A custom item `type` would be rejected by
  strict Responses-API endpoints; `checkpoint_message()`/
  `checkpoint_message_payload()` serialize/parse it.
- Ordering: `plan_compaction` runs BEFORE `slim_history` in `_drive`, so
  nothing is destructively trimmed before the compactor can summarize it;
  the slimmed budget then applies to the compacted transcript.
- Events: `context.compaction.started {estimated_tokens, items}`,
  `context.compaction.completed {checkpoint_id, retained, dropped}`,
  `context.checkpoint {checkpoint_id}`.
- Never compacts: pending approvals, unpaired calls, the latest
  `max_history_events` tail, items needed by `function_call`→`output`,
  `computer_call`→`output`, `custom_tool_call`→`output` pairing.

# Phase 3 — async sub-agent orchestration (AG2-012/013)

## 15. Sub-agent handles (`_SubagentHandle` + `tools/subagents.py`)

`spawn_subagent` used to block the parent's driver inside `_spawn_subagent`
until the child terminated. It is now a first-class durable handle:

- `spawn_subagent` creates the child task (`tasks.parent_id`, additive
  column) and returns `{ok, status: "running", task_id}` immediately. A
  per-child watcher task (`_watch_child`) keeps relaying `subagent.event`
  payloads and escalating consequential child approvals onto the parent's
  stream — independent of any single tool call, so the parent's drive
  continues to the next turn while children run.
- `_SubagentHandle(parent_id, child_id, call_id, agent, queue, watcher,
  parent_cancel, deadline, status, result, done)` lives in
  `manager._subagents[parent_id][child_id]`; `done` is what `await`
  waits on, `status`/`result` hold the terminal outcome.
- Watchers replicate the old inline loop exactly: parent-cancel
  propagation, terminal detection by event (`task.completed`/`failed`/
  `cancelled`) or status poll, `handle.deadline` cancel past
  `min(max_seconds, _SUBAGENT_MAX_SECONDS)`, `subagent.finished` emit,
  unsubscribe + `_drop_pending_approvals(parent, child_id=)` on exit,
  and restoring a genuinely-paused parent to `running`.
- Rebuild: `manager._subagent_handles(task_id)` reconciles the registry
  against `store.children(parent_id)` on every access — so after a host
  restart the linkage is recovered from the durable `parent_id` column:
  terminal children come back `done` with their stored status/result,
  still-active ones (re-driven by Phase 2 recovery) get a fresh relay
  watcher whose `parent_cancel` binds to the parent's live cancel event.
- New tools (`mutability`): `await_subagents` (read; `task_id`/`task_ids`
  selection, `timeout_s` 0-600 default 300, cancel-aware via
  `_race_cancel`; emits `subagent.awaited` and returns per-child
  `{agent,status,running,result?}` plus `timed_out`/`missing`),
  `subagent_status` (read, no waiting), `cancel_subagent` (write —
  checkpointed side effect — emits `subagent.cancelled`). All are
  agent-mode only (`expose_read_only=False`).
- Escalation hardening for async parents: a child's escalated approval
  remains resolvable after the parent reaches a terminal state —
  `resolve_approval` accepts a pending child-escalation approval on a
  non-`awaiting_approval` parent, `_finish_state` keeps child-escalation
  entries in `_pending_approval_calls` (the child's watcher drops them
  on exit), and the resolution path only restores `status=running` when
  the parent is genuinely paused (never resurrects a completed task).
- Depth cap 1: children cannot spawn their own sub-agents. The four
  `_SUBAGENT_TOOLS` are filtered out of `provider_tools` for tasks with a
  `parent_id` (`allow_subagents=False` threaded through turn/stream_turn/
  plan payloads AND task kwargs), `_decide_spawn` returns an inline
  `PolicyDecision(False)` for parents-of-parents, and `_spawn_subagent`
  re-checks `task["parent_id"]` at execute time — schema, policy, and
  runtime all refuse, closing the terminal-parent escalation vector.

- Limits (per-parent, in task `limits`, clamped in `_limits`):
  `max_parallel_subagents` default 3 (1-8) — spawn refuses when running
  children hit the cap with a hint to `await_subagents` first;
  `max_subagents_total` default 8 (1-32) — refuses when total spawned
  hits the cap. Children inherit cwd/project boundary, provider, model,
  reduced limits, and the parent's cancel event; they do not inherit any
  approval to expand scope — consequential actions still escalate.
- `manager.close()` cancels live watchers alongside workers.

# Phase 4 — computer observation v2 + richer actions

## 16. Observation layer (`agent/observation.py`)
- `Observation` dataclass emits the handoff schema: `id`, `display_id`,
  `width`/`height` (frame pixels read straight from the JPEG SOF marker —
  no decoder needed), `dpr` (pixels ÷ logical display width), `captured_at`
  + computed `age_ms`, `artifact_id`, `frame_hash`, `hash_kind`, `changed`,
  `control_owner`, `capture_backend`; optional `region`/`region_cropped`,
  `pointer`, `previous_id`.
- `frame_identity(frame)` prefers a perceptual aHash (Pillow decode → 8×8
  grayscale → 64-bit fingerprint, Hamming ≤10 = unchanged) and falls back
  to a truncated SHA-256 exact hash where Pillow is absent — honest dedup
  on every host, stronger dedup where the image codec exists.
- `ObservationTracker` is one per `AgentManager` but dedup is keyed per
  stream — `(task scope, display_id, region)` — so `changed`/`previous_id`
  compare a frame only against the same task's same display+region (first
  frame of every stream reports `changed`); `control_owner` stays global
  because the physical screen is one shared resource.
- `scale_for_model(frame, max_px=TERMX_MODEL_IMAGE_MAX_PX∥1568)` downscales
  the model-bound copy only — the artifact store keeps the full frame.
  `crop_region(frame, region)` crops a region post-capture; both return
  the input unchanged / `None` where Pillow is missing.

## 17. Batching, dedup, control ownership (`manager._execute_computer`)
- Every computer batch gets a `batch_id`; emits `computer.action.started`
  `{batch_id, actions}` before input dispatch and
  `computer.action.finished` `{batch_id, ok, error?}` after (error reports
  `cancelled` distinctly for CancelledError).
- After capture: region crop (if requested + decodable) → observation
  record → artifacts (region crop saved as the screenshot artifact; the
  full frame is preserved via `full_artifact_id`) → `computer.screenshot`
  (unchanged legacy event) + `computer.observation`.
- Duplicate suppression: `changed=False` frames emit `computer.no_change`
  `{batch_id, observation_id, frame_hash}`. On the `use_computer` path the
  follow-up message is text-only ("Screen unchanged (frame matches
  observation …)") — no second `input_image` enters model context. On the
  native `computer` path the `computer_call_output` keeps its screenshot
  (API contract) and the signal rides an adjacent user message.
- `control_owner` transitions emit `computer.control.changed`: takeover →
  `"user"`, the next agent batch → `"agent"`.

## 18. Richer computer actions (`agent/computer.py`, `providers.py`, `policy.py`)
- `paste_text`: `clipboard_set` → read-back verification → native paste
  chord (`meta+v` macOS / `control+v` elsewhere). Hosts with no clipboard
  command fall back to per-character `type` events — the safe fallback the
  handoff requires.
- `mouse_down`/`mouse_up` → pointer `down`/`up` at normalized coords with
  the same button mapping as `click`; `key_down`/`key_up` → key `down`/`up`
  with modifiers; `release_all` exposed as an action (previously only the
  controller method); `set_display` switches `controller.display_id`.
- `screenshot` actions accept `region {x,y,width,height}` (normalized
  negative/zero-safe) — served cropped where the host can decode JPEG.
- `use_computer` schema advertises every new action kind plus
  `key`/`display_id`/`region` properties; `evaluate_computer` scans
  `paste_text` text with the same credential/secret pattern as `type`.
- `machine_snapshot` advertises `computer_observation_v2: true` and
  `agent_subagents: true`.

## 19. Worktree task execution (PROD-003)

`AgentTaskBody.execution_mode` accepts `direct` (default) or `worktree`. In worktree
mode `AgentManager.create_task` rejects Ask mode and non-git projects, provisions a
`termx/task-<slug>` branch at the repo's current HEAD via
`agent/worktrees.py` (git subprocess helpers), checks it out at
`<config_dir>/worktrees/<slug>` (outside the user checkout so the base tree is never
touched), and runs the whole task with `cwd` inside the worktree — the normal
project-boundary machinery then confines every tool to the isolated checkout. A
`task_worktrees` row (task_id PK → tasks CASCADE, mode, base_repo, base_ref,
worktree_path, branch, head_sha, **base_branch** (checkout branch at creation —
new column via PRAGMA-guarded ALTER), status active|applied|kept|discarded) persists the
link; `task.worktree.created` fires on creation and `task.worktree.final` (via
`_finalize_worktree`, called on the completed path and every `_finish_state`
transition) captures the terminal head SHA.

Resolution is explicit, never silent:

- `GET /api/agent/tasks/{id}/worktree` (agent-view) — `{"mode":"direct"}` for normal
  tasks; otherwise the record plus live `dirty` (porcelain lines), `head_sha`, and a
  name-status `diff` against the base ref.
- `POST /api/agent/tasks/{id}/worktree` (agent-control) — `action` in
  `apply|keep|discard`, `confirm` bool. `apply` auto-commits outstanding worktree
  changes (author `termx-agent`), `merge --no-ff` into the base repo, removes the
  worktree and deletes the branch → `task.worktree.applied`. Apply is branch-safe
  and conflict-transactional: it refuses when the base checkout drifted off
  `base_branch` or is detached (would silently merge into the wrong line), and on
  merge failure runs `git merge --abort`, leaves the base clean + the worktree
  alive for retry/discard, emits `task.worktree.conflict`, and raises 409
  `WorktreeApplyConflict` instead of a half-merged state. `keep` marks the row
  `kept` and leaves the checkout/branch in place → `task.worktree.kept`. `discard`
  removes the worktree (`--force`) and deletes the branch → `task.worktree.discarded`;
  uncommitted changes without `confirm` return **409**
  `{requires_confirm:true, dirty:[...]}` so the client can surface a confirmation
  (VRF "never delete a worktree containing unknown user changes"). Resolutions are
  one-way; repeating a terminal action returns the stored record.
- Orphans: `AgentManager.delete_task` / `prune_storage` best-effort remove the
  worktree dir + `termx/task-*` branch for records still `active` when the task
  row goes away (`kept` worktrees are the user's and are never touched).
- Capability: `machine.capabilities.agent_worktrees`.

## 20. Activity Center (PROD-004)

`termx.activity.activity_snapshot(state)` normalizes Termx-owned activity into
`{id, kind, project_id, title, state, started_at, updated_at, actions[]}`:

- **agent** — `agent_store.list_tasks(200)`; `stop`/`cancel` actions only while
  the task is in an ACTIVE state; `project_id` via longest project-root prefix
  on the task cwd (worktree cwds still resolve through the repo path when the
  worktree sits under a registered root, else None).
- **terminal** — live `SessionManager` sessions; `updated_at` tracks last PTY
  output; `state` = running|exited.
- **preview** — per-project saved previews (state `active`; `delete` action).
- **tunnel** — active `TunnelManager.status_public()` only.
- **forward** — running `ForwardManager.statuses()` joined with config rules.
- **desktop** — virtual displays (with owner) plus a viewer-count record when
  `DesktopManager._active` is non-empty (exposed as `viewer_count` in snapshot).

Endpoints: `GET /api/activity` gated by `_require_any_scope(agent-view |
screen-view | terminal)`; `WS /api/activity/events` (same auth, closes 4401 /
4403) streams `activity.upsert` / `activity.remove` diffs computed by re-running
the snapshot every 2s — no cross-manager event plumbing. `capabilities.activity`.

## 21. Port + process discovery (PROD-005)

`termx.processes` — Linux reads `/proc`; macOS runs the same discovery via
`lsof`/`ps`; Windows is gated off. `supported()` reports capability truth and
feeds `capabilities.process_discovery` + the `supported` field on
`/api/ports` + `/api/processes` responses (empty list + `supported:false`
instead of silent empty):

- `_proc_net_listeners()` parses `tcp`/`tcp6` LISTEN rows → address/port/inode
  (darwin: `_darwin_listeners()` parses `lsof -nP -iTCP -sTCP:LISTEN`).
- `listeners()` joins socket inodes to PIDs by scanning `/proc/<pid>/fd`
  (permitted only), enriches with comm/cmdline/cwd, and runs a bounded
  (250 ms) HTTP probe on loopback/wildcard listeners → `is_http` + `url`.
- `termx_processes(project_roots)` returns the host PID's whole descendant
  tree (PTY shells, forward/tunnel helpers, agent children) plus processes
  whose cwd prefixes a registered project root — nothing else.

Routes (any of agent-view|screen-view|terminal, both `?project_id=` filterable):
`GET /api/ports`, `GET /api/processes`,
`POST /api/projects/{id}/previews/from-port` (404 unless the port is a live
listener; creates the preview from the probed URL). `capabilities.process_discovery`.

## 22. Runbooks (PROD-006)

`termx.runbooks.validate_steps` normalizes `{kind:"shell", command, confirm?,
parallel?}`; `RunbookRunner` (one per AgentManager, `state.agent.runbooks`)
executes a `runbook_runs` row per invocation:

- sequential by default; consecutive `parallel:true` steps run via gather as
  one batch; any nonzero exit ends the run `failed` (stop-on-failure);
- `confirm:true` parks the run at `awaiting_confirmation` until
  `confirm(run_id)` — the human gate applies to REST runs AND agent-invoked
  runs alike;
- step output streams: stdout is read in 8KB chunks into a 48KB rolling tail
  (bounded memory — `communicate()` never buffers a full step), published to
  `step_results` as a `status:"running"` row every >=64KB, so live output is
  visible mid-step; the step's total wall clock still obeys the 900s budget.
- cancel kills the step's process group (`preexec_fn=os.setsid`, 900s/step
  timeout); `manager.close()` cancels live runs.

REST: `GET/POST /api/runbooks`, `GET/PATCH/DELETE /api/runbooks/{id}`,
`POST /{id}/run`, `GET /api/runbook-runs`, `POST /{run}/confirm|cancel`.
Reads use the shared view scopes; create/edit/run/confirm/cancel need
`agent-control`. Agent tool `run_runbook` (`approval="always"`, not exposed in
Ask mode) polls the run to a terminal or confirmation state and reports
step-level results. `capabilities.runbooks`.

## 23. Device scopes v2 (SEC-001 / SEC-002)

`SCOPES` (15): `machine-view`, `terminal-view`, `terminal-control`, `files-read`,
`files-write`, `git-read`, `git-write`, `desktop-view`, `desktop-control`,
`network-manage`, `agent-view`, `agent-run`, `agent-control`, `ai-settings`,
`host-admin`. `DEFAULT_DEVICE_SCOPES` = all except `host-admin` (granted to a
bare `/api/pair`); the passcode still maps to all 15 for admin recovery.

Token records now carry `device_name`, `created_at`, `last_seen`, `expires_at`,
`legacy` alongside `id/hash/scopes`; `check()` enforces expiry and throttles
`last_seen` persistence (60s). `POST /api/devices/{id}/scopes` is the explicit
re-scope/re-pair path (host-admin only).

Migration (`_normalize_scopes` + `LEGACY_SCOPE_MAP`): each v1 scope maps to its
exact v2 equivalent (`terminal` → terminal-view/control + files-* + git-*;
`screen-*` → desktop-*; `tunnel-admin` → network-manage; `settings-admin` →
ai-settings; agent-*/ai-settings identity). Migrated tokens additionally get
`machine-view` because any v1 credential could already reach read-only host
endpoints; `legacy: true` flags them in `list_public`. `host-admin` is never
granted implicitly — only via explicit `update_scopes` or re-pair with an
explicit scope list. A stored record with a missing/malformed scope list floors
at `machine-view` (never broadens).

Enforcement: every `/api/*` route uses `_require_scope` (except open
`/api/health`, `/api/pair` and static `/_/`, `/` assets). The pairing
surface itself — `/api/connect` (raw passcode + `?`QR view) and
`/api/connect/qr.svg` — requires `host-admin`, so a `machine-view` token can
no longer read the raw passcode and mint a host-admin token from it. WS
sockets gate per-scope: pty → `terminal-control`, lsp → `files-read`, desktop →
`desktop-view`, activity → `machine-view`, agent events → `agent-view`.
Sensitive-path denial stays inline (4xx), never an approval.

## 24. Conversations + custom agents (PROD-001/002) — execution wiring

`conversations` / `conversation_turns` / `conversation_context_refs` /
`custom_agents` are host-persisted and wired into `AgentManager.create_task`:

- `conversation_id` seeds the task's initial history with a
  `{"type":"conversation_context"}` user-message item listing the last 12 prior
  turns (prompt, mode, linked task status, truncated result) — durable
  carry-over between tasks in one thread. `provider_id`/`model` resolve
  explicit > conversation > custom-agent.
- `custom_agent_id` (or the conversation's linked agent) wraps the prompt with
  the agent's `instructions`, merges its `limits` as defaults under the
  request's, and fills `provider_id`/`model` when the request omits them
  (`AgentTaskBody.provider_id` is optional; the route 400s only when no source
  resolves). Unknown ids 404 in the route, `ValueError` in the manager.
- `POST /api/agent/tasks` still appends a `conversation_turns` row with the
  new `task_id` link — read and write paths both live.
