"""Groups a provider turn's calls into sequential execution segments.

Calls that are parallel-safe (read-only tools needing no approval) share one
segment and execute concurrently; every other call is its own segment so
writes, Git mutations, shell commands, sub-agents, and computer actions stay
serialized exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from termx.agent.policy import PolicyDecision

if TYPE_CHECKING:
    from termx.agent.providers import ProviderCall
    from termx.agent.tools.registry import ToolRegistry, ToolSpec


@dataclass
class ScheduledCall:
    call: "ProviderCall"
    spec: "ToolSpec | None"
    decision: PolicyDecision

    @property
    def parallel_safe(self) -> bool:
        return (
            self.spec is not None
            and self.spec.parallel_safe
            and not self.decision.approval_required
        )


class CallScheduler:
    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    def schedule(self, calls: "list[ProviderCall]", ctx: Any) -> list[list[ScheduledCall]]:
        """Order-preserving grouping: contiguous parallel-safe calls form one batch."""
        groups: list[list[ScheduledCall]] = []
        for call in calls:
            spec = self._registry.spec_for(call)
            decision = (
                spec.decide(call, ctx)
                if spec is not None
                else PolicyDecision(False, True, "Unknown tool", "Runs an unsupported tool request")
            )
            entry = ScheduledCall(call=call, spec=spec, decision=decision)
            if entry.parallel_safe and groups and all(item.parallel_safe for item in groups[-1]):
                groups[-1].append(entry)
            else:
                groups.append([entry])
        return groups
