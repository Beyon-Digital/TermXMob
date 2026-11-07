"""Context Engine v1: compact project snapshot plus bounded history.

The snapshot replaces the raw flat manifest as provider context: it keeps the
existing ``files``/``omitted`` keys (the provider prompt contract) and adds
git state, detected manifests, language histogram, likely check commands, and
recently modified files. Snapshots are cached per task and invalidated after
any mutating tool call so later turns see fresh state without re-walking the
tree every turn.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable

from termx import git_ops
from termx.agent.context.manifest import SKIP_DIRS, _gitignore, _ignored, _secret_name, workspace_manifest

DEFAULT_CONTEXT_LIMITS = {
    "max_context_estimate_tokens": 120_000,
    "max_tool_output_chars_per_turn": 50_000,
    "max_history_events": 160,
    "max_images_in_context": 4,
}

# Output room held back from the window when the model does not report max_output.
DEFAULT_RESERVED_OUTPUT = 8_192
DEFAULT_TOOL_RESULT_CAP_CHARS = 8_000
_MIN_TOOL_RESULT_CAP_CHARS = 2_000
# The rule behind ``estimate_tokens``, shared by every size conversion below.
CHARS_PER_TOKEN = 4
# Share of a summariser's budget that the older transcript slice may take; the rest is its reply.
_SUMMARY_INPUT_SHARE = 0.6
# Fewest recent items a small window may keep verbatim.
_MIN_KEPT_ITEMS = 4
_SUMMARISER_TIMEOUT_S = 30.0
_RECENT_FILES = 12
_SNAPSHOT_DEPTH = 2
_SNAPSHOT_TTL_S = 30.0
# 1x1 transparent PNG — placeholder for screenshots dropped by the image budget
# (keeps computer_call_output schema-valid without shipping stale pixels).
_BLANK_IMAGE_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def limits_for_model(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Context limits for one model from its metadata (``Route.meta``).

    The budget is the window minus the output room (``max_output``, else a default, never more than
    half the window); compaction triggers at 75% of it. A model with no known window keeps the 120k
    default and reports ``context_window=None`` so the UI can say so.
    """
    # ``Route.meta`` is already normalised: values are positive ints or None.
    window = (meta or {}).get("context_window")
    if not window:
        return {
            "context_window": None,
            "max_context_estimate_tokens": DEFAULT_CONTEXT_LIMITS["max_context_estimate_tokens"],
            "tool_result_cap_chars": DEFAULT_TOOL_RESULT_CAP_CHARS,
        }
    reserved = min((meta or {}).get("max_output") or DEFAULT_RESERVED_OUTPUT, window // 2)
    return {
        "context_window": window,
        "max_context_estimate_tokens": window - reserved,
        # ~2% of the window in characters, bounded.
        "tool_result_cap_chars": max(
            _MIN_TOOL_RESULT_CAP_CHARS, min(DEFAULT_TOOL_RESULT_CAP_CHARS, window * CHARS_PER_TOKEN * 2 // 100)
        ),
    }


def summary_input_chars(meta: dict[str, Any] | None) -> int:
    """How much transcript text fits in a summariser call for the model described by ``meta``."""
    budget = limits_for_model(meta)["max_context_estimate_tokens"]
    return int(budget * CHARS_PER_TOKEN * _SUMMARY_INPUT_SHARE)


def project_snapshot(root: str) -> dict[str, Any]:
    """Build a compact snapshot of the project at ``root`` (blocking; run via to_thread)."""
    base = Path(root).resolve(strict=True)
    manifest = workspace_manifest(str(base))
    ignored = _gitignore(base)
    files = [str(item) for item in manifest.get("files") or []]
    snapshot: dict[str, Any] = {
        "kind": "project_snapshot",
        "root": str(base),
        "name": base.name,
        "files": manifest["files"],
        "omitted": manifest["omitted"],
        "truncated": manifest["truncated"],
        "manifests": _manifests(base),
        "languages": _languages(files),
        "commands": _commands(base),
        "tree": _tree(base, ignored),
        "recent": _recent(base, ignored),
        "git": _git(base),
    }
    return snapshot


def _manifests(base: Path) -> list[str]:
    candidates = [
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "requirements.txt",
        "uv.lock",
        "pnpm-lock.yaml",
        "package-lock.json",
        "Makefile",
    ]
    return [name for name in candidates if (base / name).is_file()]


def _languages(files: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for rel in files:
        suffix = Path(rel).suffix.lstrip(".").lower()
        if suffix:
            counts[suffix] += 1
    return dict(counts.most_common(5))


def _commands(base: Path) -> list[str]:
    commands: list[str] = []
    if (base / "pyproject.toml").is_file() or (base / "tests").is_dir():
        commands.append("uv run pytest -q" if (base / "uv.lock").is_file() else "pytest -q")
    package = base / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8", errors="replace")).get("scripts") or {}
        except ValueError:
            scripts = {}
        runner = "pnpm" if (base / "pnpm-lock.yaml").is_file() else "npm"
        for key in ("test", "lint", "typecheck", "build"):
            if key in scripts:
                commands.append(f"{runner} run {key}" if key != "test" else f"{runner} test")
    if (base / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (base / "go.mod").is_file():
        commands.append("go test ./...")
    return commands


def _tree(base: Path, ignored: list[str]) -> list[str]:
    """Top-level layout: root entries plus their immediate children."""
    entries: list[str] = []
    try:
        for top in sorted(base.iterdir()):
            if top.name in SKIP_DIRS or top.name.startswith(".") or _secret_name(top.name) or _ignored(top.name, ignored):
                continue
            if top.is_dir():
                entries.append(top.name + "/")
                if len(entries) >= 60:
                    break
                if top.is_symlink():
                    # Linked dirs are listed but never traversed: a symlink
                    # outside the project would leak external names otherwise.
                    try:
                        if not top.resolve(strict=True).is_relative_to(base.resolve()):
                            continue
                    except OSError:
                        continue
                for child in sorted(top.iterdir())[:20]:
                    rel_child = f"{top.name}/{child.name}"
                    if (
                        child.name.startswith(".")
                        or child.name in SKIP_DIRS
                        or _secret_name(child.name)
                        or _ignored(rel_child, ignored)
                    ):
                        continue
                    entries.append(f"{rel_child}{'/' if child.is_dir() else ''}")
            else:
                entries.append(top.name)
            if len(entries) >= 120:
                break
    except OSError:
        pass
    return entries[:120]


def _recent(base: Path, ignored: list[str]) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".") and not _secret_name(name)
        ]
        depth = len(current_path.relative_to(base).parts)
        if depth >= 4:
            dirnames[:] = []
        for name in filenames:
            rel = str((current_path / name).relative_to(base))
            if (
                name.startswith(".")
                or any(_secret_name(part) for part in Path(rel).parts)
                or _ignored(rel, ignored)
            ):
                continue
            try:
                mtime = (current_path / name).stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, rel))
    candidates.sort(reverse=True)
    return [rel for _mtime, rel in candidates[:_RECENT_FILES]]


def _git(base: Path) -> dict[str, Any] | None:
    try:
        status = git_ops.status(str(base))
    except Exception:
        return None
    changes = status.get("files") or []
    return {
        "branch": status.get("branch"),
        "changed": len(changes),
        "staged": sum(1 for item in changes if item.get("staged")),
    }


class ContextEngine:
    """Per-task context: cached project snapshot plus history budgeting."""

    def __init__(self, root: str, limits: dict[str, Any] | None = None) -> None:
        self._root = root
        self._limits = {**DEFAULT_CONTEXT_LIMITS, **(limits or {})}
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_at = 0.0
        # (input_tokens, history length) of the last real provider turn.
        self._usage: tuple[int, int] | None = None

    def configure(self, limits: dict[str, Any] | None) -> None:
        """Adopt the limits of the model the next call goes to.

        A different route means different tokenisation, so the last real usage no longer applies.
        """
        merged = {**DEFAULT_CONTEXT_LIMITS, **(limits or {})}
        if merged != self._limits:
            self._limits = merged
            self._usage = None

    def note_usage(self, input_tokens: int, history_len: int) -> None:
        """Record the real prompt size the provider reported for a history of ``history_len`` items."""
        self._usage = (input_tokens, history_len) if input_tokens > 0 else None

    def context_used(self, items: list[dict[str, Any]]) -> int:
        """Tokens the next prompt will take: the last real input size plus an estimate of what came after it."""
        if self._usage is not None and len(items) >= self._usage[1]:
            return self._usage[0] + estimate_tokens(items[self._usage[1]:])
        return estimate_tokens(items)

    async def snapshot(self) -> dict[str, Any]:
        import time

        if self._snapshot is None or time.monotonic() - self._snapshot_at > _SNAPSHOT_TTL_S:
            self._snapshot = await asyncio.to_thread(project_snapshot, self._root)
            self._snapshot_at = time.monotonic()
        return self._snapshot

    def note_mutation(self) -> None:
        self._snapshot = None
        self._snapshot_at = 0.0

    def plan_compaction(
        self,
        items: list[dict[str, Any]],
        *,
        git_state: dict[str, Any] | None = None,
        keep_recent: int = 40,
    ) -> dict[str, Any] | None:
        """Plan a context compaction checkpoint, or ``None`` if not yet needed.

        Returns ``{"history": [...], "payload": {...}, "dropped": int}`` where
        ``history`` is the compacted transcript (leading user item + injected
        checkpoint summary item + retained tail) and ``payload`` is the
        deterministic summary to persist as a ``checkpoints(kind="context")``
        row. Never severed: a call and its ``*_call_output`` either stay
        together in the retained tail or are both summarized; unresolved
        calls are recorded in ``pending_work`` (described, not re-injected —
        a bare call item without its output would not be API-valid).
        """
        limit = int(self._limits["max_context_estimate_tokens"])
        if self.context_used(items) < int(limit * 0.75):
            return None
        head: list[dict[str, Any]] = []
        body = list(items)
        if body and isinstance(body[0], dict) and body[0].get("role") == "user":
            head, body = body[:1], body[1:]
        split = len(body) - _kept_items(body, keep_recent, limit // 2)
        # Slide the boundary left past leading outputs so no retained item is
        # an orphan whose call was compacted away.
        while split > 0 and str(body[split].get("type") or "").endswith("call_output"):
            split -= 1
        compacted, retained = body[:split], list(body[split:])
        # Nothing older than the tail, or only an earlier checkpoint: nothing new to fold in.
        if not any(checkpoint_message_payload(item) is None for item in compacted):
            return None
        unresolved = _unresolved_call_ids(compacted)
        summary = _compaction_summary(compacted, retained, unresolved, git_state)
        self._usage = None
        return {
            "history": head + [checkpoint_message(summary, len(compacted))] + retained,
            "payload": summary,
            "history_cursor": len(items) - len(retained),
            "dropped": len(compacted),
            "older": compacted,
            "checkpoint_index": len(head),
            "summariser": None,
        }

    async def compact(
        self,
        items: list[dict[str, Any]],
        *,
        git_state: dict[str, Any] | None = None,
        keep_recent: int = 40,
        summarise: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]] | None = None,
        timeout_s: float = _SUMMARISER_TIMEOUT_S,
    ) -> dict[str, Any] | None:
        """``plan_compaction`` with an optional model-written summary of the older slice.

        ``summarise`` gets only the slice being dropped and returns ``summary``, ``decisions`` and
        ``pending_work``. Any error, timeout or malformed reply keeps the rule-based plan and sets
        ``summariser`` to ``"fallback"``; the retained tail and call pairing are unchanged either way.
        """
        plan = self.plan_compaction(items, git_state=git_state, keep_recent=keep_recent)
        if plan is None or summarise is None:
            return plan
        try:
            reply = await asyncio.wait_for(summarise(plan["older"]), timeout_s)
            text = reply.get("summary") if isinstance(reply, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise ValueError("summariser returned no summary")
        except Exception as exc:
            plan["summariser"] = "fallback"
            plan["summariser_error"] = (str(exc) or type(exc).__name__)[:200]
            return plan
        payload = plan["payload"]
        payload["summary"] = text.strip()[:4000]
        payload["decisions"] = list(dict.fromkeys(payload["decisions"] + _strings(reply.get("decisions"), 20)))[-20:]
        payload["pending_work"] = (payload["pending_work"] + _strings(reply.get("pending_work"), 20))[-20:]
        payload["summariser"] = "model"
        plan["history"][plan["checkpoint_index"]] = checkpoint_message(payload, plan["dropped"])
        plan["summariser"] = "model"
        return plan

    def cap_tool_results(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound each tool result to ``tool_result_cap_chars``, keeping its head and tail.

        The marker says how many characters were cut. A capped result is already within the cap,
        so running this again changes nothing. Inputs are not mutated.
        """
        cap = int(self._limits.get("tool_result_cap_chars") or DEFAULT_TOOL_RESULT_CAP_CHARS)
        capped: list[dict[str, Any]] = []
        for item in items:
            output = item.get("output") if isinstance(item, dict) else None
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and isinstance(output, str)
                and len(output) > cap
            ):
                item = {**item, "output": _cap_file_read(output, cap) or _head_tail(output, cap)}
            capped.append(item)
        return capped

    def slim_history(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound transcript size without breaking call/output pairing.

        Keeps the leading user item, bounds the transcript to the newest
        ``max_history_events`` items (never severing a tool call from its
        output), applies ``max_tool_output_chars_per_turn`` as a cumulative
        newest-first budget across tool outputs, and keeps at most
        ``max_images_in_context`` image parts (newest kept).
        """
        items = self.cap_tool_results(items)
        char_cap = int(self._limits["max_tool_output_chars_per_turn"])
        image_cap = int(self._limits["max_images_in_context"])
        max_events = int(self._limits["max_history_events"])
        head: list[dict[str, Any]] = []
        if items and isinstance(items[0], dict) and items[0].get("role") == "user":
            head, items = items[:1], items[1:]
        if len(items) > max(0, max_events - len(head)):
            items = items[max(0, len(items) - max(0, max_events - len(head))):]
            # An output whose call was cut would leave an unmatched pair — drop
            # leading orphans of every call-output kind.
            while items and isinstance(items[0], dict) and str(items[0].get("type") or "").endswith("call_output"):
                items = items[1:]
        slimmed: list[dict[str, Any]] = []
        for item in head + list(items):
            if not isinstance(item, dict):
                slimmed.append(item)
                continue
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                output = item["output"]
                if len(output) > char_cap:
                    item = {**item, "output": output[:char_cap] + "\n...[truncated by context engine]"}
            slimmed.append(item)
        # Newest outputs keep their budget; older ones collapse once the
        # cumulative allowance is spent.
        remaining = char_cap
        for index in range(len(slimmed) - 1, -1, -1):
            item = slimmed[index]
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output" or not isinstance(item.get("output"), str):
                continue
            output = item["output"]
            if len(output) <= remaining:
                remaining -= len(output)
                continue
            keep = max(0, remaining)
            remaining = 0
            marker = "\n...[elided by context budget]"
            slimmed[index] = {**item, "output": output[:keep] + marker if keep else marker.strip()}
        # Walk from newest to oldest to decide which image parts survive.
        # Covers both user `input_image` parts and native `computer_call_output`
        # screenshots (whose image lives in output.image_url).
        image_positions: list[tuple[int, int | None]] = []
        for index, item in enumerate(slimmed):
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list):
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "input_image":
                        image_positions.append((index, part_index))
            if isinstance(item, dict) and item.get("type") == "computer_call_output":
                output = item.get("output")
                if isinstance(output, dict) and output.get("image_url"):
                    image_positions.append((index, None))
        drop = set(image_positions[:-image_cap]) if len(image_positions) > image_cap else set()
        for index, part_index in sorted(drop):
            item = slimmed[index]
            if part_index is None:
                slimmed[index] = {
                    **item,
                    "output": {**item["output"], "image_url": _BLANK_IMAGE_URL},
                }
                continue
            content = list(item["content"])
            content[part_index] = {"type": "input_text", "text": "[image omitted by context budget]"}
            slimmed[index] = {**item, "content": content}
        return slimmed


SUMMARY_PROMPT = (
    "You compress the older part of an agent's transcript so the agent can continue the task. "
    "Reply with JSON only: {\"summary\": string (what was asked, done and learned), "
    "\"decisions\": [string], \"pending_work\": [string]}. Keep file paths, commands and error "
    "messages that still matter. Do not call tools."
)


def render_for_summary(items: list[dict[str, Any]], max_chars: int) -> str:
    """The older transcript slice as plain lines, newest kept when it does not fit ``max_chars``."""
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("role") or "item")
        label = f"{kind} {item['name']}" if item.get("name") else kind
        text = _item_text(item) or json.dumps(item.get("arguments") or item.get("input") or "", default=str)
        lines.append(f"[{label}] {text[:600]}")
    rendered = "\n".join(lines)
    return rendered if len(rendered) <= max_chars else "[earlier lines omitted]\n" + rendered[-max_chars:]


def parse_summary(text: str) -> dict[str, Any]:
    """The JSON object in a summariser reply (code fences tolerated). Raises ValueError when there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("summariser reply is not JSON")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("summariser reply is not an object")
    return parsed


def _kept_items(body: list[dict[str, Any]], keep_recent: int, token_room: int) -> int:
    """How many of the newest items stay verbatim: at most ``keep_recent``, fewer when they would not
    fit in ``token_room`` tokens (a small window), never fewer than a handful."""
    floor = min(_MIN_KEPT_ITEMS, keep_recent)
    kept, used = 0, 0
    for item in reversed(body[-keep_recent:] if keep_recent > 0 else []):
        used += estimate_tokens([item])
        if used > token_room and kept >= floor:
            break
        kept += 1
    return kept


def _head_tail(text: str, cap: int) -> str:
    def marker(cut: int) -> str:
        return f"\n...[truncated {cut} chars, re-read with offset]...\n"

    room = max(0, cap - len(marker(len(text))))
    head = room * 2 // 3
    tail = room - head
    return text[:head] + marker(len(text) - room) + (text[len(text) - tail:] if tail else "")


def _cap_file_read(output: str, cap: int) -> str | None:
    """Cap a ``read_file`` result by shortening only its ``content``, keeping the JSON valid.

    The result then says it is truncated and which line to read next. ``None`` when ``output`` is
    not a read_file result (or cannot be shortened enough), so the generic head and tail cut applies.
    """
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if not (isinstance(data, dict) and isinstance(data.get("content"), str) and isinstance(data.get("start_line"), int)):
        return None
    content = data["content"]
    overhead = len(json.dumps({**data, "content": "", "truncated": True, "next_offset": 10**9, "cap_note": " " * 120}))
    keep = cap - overhead
    while keep > 0:
        shown = content[:keep]
        if len(shown) < len(content) and "\n" in shown:
            shown = shown[: shown.rfind("\n") + 1]  # whole lines, so next_offset is exact
        lines = len(shown.splitlines())
        result = {
            **data,
            "content": shown,
            "truncated": True,
            "next_offset": int(data["start_line"]) + lines,
            "cap_note": f"truncated {len(content) - len(shown)} chars, re-read with offset={int(data['start_line']) + lines}",
        }
        encoded = json.dumps(result)
        if len(encoded) <= cap:
            return encoded
        keep -= len(encoded) - cap
    return None


def _strings(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:400] for item in value if str(item).strip()][:limit]


def estimate_tokens(items: list[dict[str, Any]]) -> int:
    """Rough transcript cost in tokens (chars/4 over the JSON encoding)."""
    try:
        return len(json.dumps(items, default=str)) // CHARS_PER_TOKEN
    except (TypeError, ValueError):
        return 0


CONTEXT_CHECKPOINT_TYPE = "context_checkpoint"


def checkpoint_message(
    summary: dict[str, Any],
    compacted_items: int,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    """Serialize a compaction summary as a normal user message item.

    The injected transcript entry must be a plain Responses-API input
    message — a custom ``type`` would be rejected by strict provider
    endpoints — so the structured payload travels inside ``content``
    as a JSON document tagged with ``CONTEXT_CHECKPOINT_TYPE``.
    """
    body = {
        "type": CONTEXT_CHECKPOINT_TYPE,
        "checkpoint_id": checkpoint_id,
        "summary": summary,
        "compacted_items": compacted_items,
    }
    return {"role": "user", "content": json.dumps(body, sort_keys=True)}


def checkpoint_message_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a serialized compaction message back into its payload dict."""
    if not isinstance(item, dict):
        return None
    if item.get("role") != "user" or not isinstance(item.get("content"), str):
        return None
    try:
        body = json.loads(item["content"])
    except (TypeError, ValueError):
        return None
    if isinstance(body, dict) and body.get("type") == CONTEXT_CHECKPOINT_TYPE:
        return body
    return None


_CALL_ITEM_TYPES = {"function_call", "computer_call", "custom_tool_call"}


def _call_item_id(item: dict[str, Any]) -> str:
    return str(item.get("call_id") or item.get("id") or "")


def _unresolved_call_ids(items: list[dict[str, Any]]) -> set[str]:
    """Call ids present as call items but never answered by a *_call_output."""
    outputs: set[str] = set()
    calls: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind in _CALL_ITEM_TYPES:
            call_id = _call_item_id(item)
            if call_id:
                calls.add(call_id)
        elif kind.endswith("call_output"):
            call_id = str(item.get("call_id") or "")
            if call_id:
                outputs.add(call_id)
    return calls - outputs


def _item_text(item: dict[str, Any]) -> str:
    """Best-effort short text for an item (assistant/user/output)."""
    for key in ("text", "output", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, list):
        parts = [
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        ]
        if parts:
            return " ".join(parts).strip()
    return ""


def _compaction_summary(
    compacted: list[dict[str, Any]],
    retained: list[dict[str, Any]],
    unresolved: set[str],
    git_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deterministic structured summary of the compacted history region."""
    import time

    steps: list[str] = []
    files: list[str] = []
    errors: list[str] = []
    pending: list[str] = []
    last_assistant = ""
    last_user = ""
    folded = 0
    decisions: list[str] = []
    narratives: list[str] = []
    outputs_by_call: dict[str, str] = {}
    for item in compacted:
        if not isinstance(item, dict):
            continue
        prior = checkpoint_message_payload(item)
        if prior is not None:
            # An earlier checkpoint is folded in, not retained beside the new one.
            earlier = prior.get("summary") or {}
            steps.extend(str(step) for step in earlier.get("completed_steps") or [])
            files.extend(f for f in map(str, earlier.get("important_files") or []) if f not in files)
            errors.extend(str(err) for err in earlier.get("errors") or [])
            decisions.extend(d for d in map(str, earlier.get("decisions") or []) if d not in decisions)
            # A model-written narrative is worth keeping; the rule-based line is rebuilt each time.
            narratives.append(str(earlier.get("earlier_summary") or ""))
            if earlier.get("summariser") == "model":
                narratives.append(str(earlier.get("summary") or ""))
            folded += int(prior.get("compacted_items") or 0)
            continue
        kind = str(item.get("type") or "")
        if kind.endswith("call_output"):
            call_id = str(item.get("call_id") or "")
            text = _item_text(item)
            if call_id and text:
                outputs_by_call[call_id] = text[:400]
            if text and ("error" in text.lower() or "failed" in text.lower()):
                errors.append(text[:200])
            continue
        if kind in _CALL_ITEM_TYPES:
            call_id = _call_item_id(item)
            name = str(item.get("name") or "computer")
            args = item.get("arguments") or item.get("input") or {}
            if isinstance(args, dict):
                for key in ("path", "file", "command"):
                    value = args.get(key)
                    if isinstance(value, str) and value and value not in files:
                        files.append(value)
            outcome = outputs_by_call.get(call_id)
            status = "pending" if call_id in unresolved else ("ok" if outcome is None else "done")
            line = f"{name}({json.dumps(args, default=str)[:80]}) -> {status}"
            steps.append(line)
            if call_id in unresolved:
                pending.append(line)
            continue
        role = str(item.get("role") or "")
        text = _item_text(item)
        if role == "assistant" and text:
            last_assistant = text[:400]
        elif role == "user" and text:
            last_user = text[:400]
    if last_assistant:
        pending.append(f"last_assistant: {last_assistant}")
    if last_user:
        pending.append(f"last_user: {last_user}")
    summary = {
        "summary": (
            f"Compacted {len(compacted) + folded} earlier transcript items: "
            f"{len(steps)} tool calls, {len(errors)} error outputs; "
            f"retained {len(retained)} recent items verbatim."
        ),
        "completed_steps": steps[-15:],
        "important_files": files[-20:],
        "decisions": decisions[-20:],
        "pending_work": pending[-10:],
        "errors": errors[-8:],
        "git_state": git_state or {},
        "approvals": [],
        "artifacts": [],
        "last_safe_execution_checkpoint": None,
        "created_at": time.time(),
    }
    earlier_summary = "\n".join(text for text in narratives if text.strip())[-2000:]
    if earlier_summary:
        summary["earlier_summary"] = earlier_summary
    return summary
