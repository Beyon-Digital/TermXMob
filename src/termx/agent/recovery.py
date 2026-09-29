"""Crash-safe resume decisions for execution checkpoints.

The decision is deliberately conservative and never replays an ambiguous
side effect automatically:

  prepared                -> resume_safe        (side effect never started)
  committed               -> resume_committed   (result already in history)
  completed_uncommitted   -> resume_reuse       (stored result, no re-exec)
  running / unknown       -> confirm_required   (may have started; user decides)
  no checkpoint / blocked -> not_resumable      (legacy fail-fast)
"""
from __future__ import annotations

from typing import Any

DECISIONS = frozenset(
    {"resume_safe", "resume_committed", "resume_reuse", "confirm_required", "not_resumable"}
)


def recovery_decision(checkpoint: dict[str, Any] | None) -> str:
    """Classify the latest execution checkpoint for restart recovery."""
    if checkpoint is None:
        return "not_resumable"
    if not checkpoint.get("resumable", True):
        return "not_resumable"
    if not checkpoint.get("payload"):
        return "not_resumable"
    state = str(checkpoint.get("side_effect_state") or "")
    if state == "prepared":
        return "resume_safe"
    if state == "committed":
        return "resume_committed"
    if state == "completed_uncommitted":
        return "resume_reuse" if checkpoint.get("result") else "confirm_required"
    return "confirm_required"


def public_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Slim checkpoint projection for events (drops the resume payload)."""
    return {
        "id": checkpoint["id"],
        "kind": checkpoint["kind"],
        "side_effect_state": checkpoint["side_effect_state"],
        "pending_call_id": checkpoint["pending_call_id"],
        "plan_step": checkpoint["plan_step"],
        "resumable": checkpoint["resumable"],
        "created_at": checkpoint["created_at"],
    }
