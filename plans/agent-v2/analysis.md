# Agent v2 — Current-state analysis

Audit of `src/termx/agent/**` against `TERMX_BACKEND_AGENT_V2_HANDOFF.md`.
Verified by reading the code on 2026-09-28 (commit `a6f341d`); where the
handoff and the code disagree, the code below is authoritative.

## Call graph today

```text
POST /api/agent/tasks            -> AgentManager.create_task
  -> workspace_manifest(cwd)     -> adapter.plan -> store task (awaiting_approval)
POST .../approvals/{id}          -> resolve_approval -> _launch(_drive)
_drive (per provider turn):
  -> adapter.turn(prompt, cwd, manifest, input_items=history)
  -> emit assistant.message / provider.usage
  -> _run_calls -> for each ProviderCall, strictly sequential:
       _decision(call)            -> approval pause (durable) OR
       _execute_call(call)        -> run_shell | share_file | spawn_subagent | computer
  -> runtime {manifest, history, step} persisted each turn
```

## Verified findings

| Finding | Evidence in code |
| --- | --- |
| Shell-centric tool surface | `manager._execute_call` handles exactly `run_shell`, `share_file`, `spawn_subagent`, `computer`. All file/Git work goes through `run_shell`. |
| Structured services already exist | `project_files.ProjectFiles` (listing/read/save with sha256 revision guard, mutate, search, previews), `git_ops` (status/diff/stage/hunk/commit/branches/fetch/pull/push), `machine.machine_snapshot`, `lsp` WS bridge. None are reachable from the Agent. |
| Serial tool execution | `_run_calls` `for index, call in enumerate(calls)` — no concurrency; each call awaited before the next is evaluated. |
| Buffered shell output | `execution.run_shell` uses `process.communicate()`; one `ShellResult` at exit, `OUTPUT_LIMIT` tail truncation only. |
| New HTTP client per request | `OpenAIResponsesAdapter._post` constructs `httpx.AsyncClient(...)` inside an `async with` on every call (test at `test_agent.py:979` pins this shape for cancellation). |
| Static shallow context | `context.workspace_manifest` — flat filename list (≤500, secrets/skips excluded) captured once in `create_task`, stored in `runtime["manifest"]`, reused every turn; `_task_input` dumps up to 500 paths into the prompt each first turn. |
| Blocking sub-agents | `_spawn_subagent` creates a child task, auto-approves its plan, then polls the child's subscriber queue until terminal status — the parent cannot do other work meanwhile. Consequential child actions escalate via `_escalate_child_approval`. |
| Fail-on-restart recovery | `AgentManager.__init__` marks every ACTIVE task `failed` at boot. |
| Capabilities surface | `machine_snapshot` exposes `capabilities.agent*`; no v2 flags yet. |

## Preserved invariants (do not regress)

- Durable event ledger (`append_event`, `next_sequence`), ordered WS replay
  (`/api/agent/tasks/{id}/events`), approvals persisted + private call payloads
  kept in memory only (`_pending_approval_calls`).
- Approval policy: `_CONSEQUENTIAL` shell patterns → approval; sensitive paths /
  `..` traversal → approval; Ask mode = read-only (mutating shell refused inline,
  no computer, no subagents); `share_file`/`spawn_subagent` → always approval.
- Redaction: `policy.redact` on outputs, `ProviderCall.public()`, shell env
  scrubbed via `SENSITIVE_ENV`; provider secrets host-only/write-only
  (`CredentialStore`, `secret_configured` flag only).
- Cancellation: `_race_cancel` around provider turn; `ComputerController`
  releases input on any failure; takeover releases + single `control.takeover`.
- `store: false` provider calls; local transcript replayed when
  `previous_response_id` is absent (compatible endpoints).

## Seams chosen (minimal-diff migration)

- `context.py` -> `context/` package: `manifest.py` keeps `workspace_manifest`
  verbatim (re-exported from `context/__init__.py`, so existing imports and
  tests keep working); `engine.py` adds the Context Engine beside it.
- New `tools/` package owns typed tools; `manager` keeps lifecycle and delegates
  dispatch to `tools/registry.py`. Legacy tool names keep the same wire shape
  (`function_call_output` JSON).
- `scheduler.py` wraps `_run_calls` batching only — approval pause semantics,
  `_resume_approved`, read-only Ask fast-path are untouched.
- `runtime.py` owns a per-provider `httpx.AsyncClient` pool; adapters accept an
  optional injected client (default: current ephemeral construction, so the
  pinned cancellation/redirect tests still pass).
- `execution.run_shell` stays as the buffered API; a sibling streaming runner
  feeds `process.*` events. `run_shell` tool now reports via streaming runner.
- Metrics persist on `tasks.metrics` (additive column), not a new table —
  Phase 2+ can normalize if needed.
