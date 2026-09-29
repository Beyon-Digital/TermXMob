"""Durable approval policies: intents, fingerprints, precedence engine.

The engine decides whether a tool call auto-resolves against remembered
rules, auto-approves under a custom agent's mode, or asks the user. Regex
command policy stays as the intent/UX layer underneath; the sandbox — never
these rules — remains the enforcement boundary.
"""
from __future__ import annotations

from termx.agent.policies.engine import PolicyEngine
from termx.agent.policies.models import PolicyIntent

__all__ = ["PolicyEngine", "PolicyIntent"]
