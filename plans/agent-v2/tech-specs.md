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
- `apply_patch {patch}` → rejects absolute/`..` paths, `git apply --check`
  dry-run then `git apply`; returns `{ok, changed:[paths], summary}`.
  Not a repo → still works (working-tree apply); git missing → `ok:false`.

## 3. Search (`tools/search.py`)

- `search_project {query, path=".", mode="content|filename",
  case_sensitive}` — same walk rules/caps as `ProjectFiles.search`
  (20k visited, 5s deadline, 500 results); content mode returns
  `{path, line, text, revision}`.

## 4. Git / project tools (`tools/git.py`, `tools/project.py`)

- `git_status {}`, `git_diff {path?, staged?, range?}`, `git_stage
  {paths, unstage?}`, `git_commit {message}`, `git_branch {name?, create?,
  switch?}`, `git_fetch {}`, `git_pull {}`, `git_push {}`.
- All run `git -C <cwd>` via the existing `git_ops` helpers in a thread;
  mutating ops are `mutability="write"`, `parallel_safe=False`.
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
    async def aclose(self) -> None
```

- One client per provider id (base_url + auth header bound at creation);
  `limits=httpx.Limits(max_connections=10, max_keepalive_connections=4)`.
- `OpenAIResponsesAdapter(client=None)` — injected client ⇒ reuse, never
  close it; `None` ⇒ current per-call client (keeps unit tests valid).
- Manager `_default_adapter` passes the pooled client; `close()` calls
  `runtime.aclose()`. Deleting a provider evicts its client.

## 9. Metrics (`agent/metrics.py`)

- `TaskMetrics.record_*`: `provider_request(ms)`, `tool(name, ms)`,
  `shell(ms)`, `screenshot(bytes)`, `usage(dict)`, `approval_wait(ms)`.
- Persisted on `tasks.metrics` (new nullable JSON column via additive
  `ALTER TABLE`; `TASK_FIELDS += "metrics"`) at completion/failure and
  flushed periodically on long runs.
- Emit `task.metrics` event with the final summary.

## 10. Capability flags (`machine_snapshot`)

`agent_tools_v2: true`, `agent_context_v2: true`, `agent_streaming: false`
(streaming lands in Phase 2), `agent_parallel_tools: true`.
