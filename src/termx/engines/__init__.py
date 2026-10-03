"""External agent-engine integrations.

Each engine adapter owns a *native* session in the engine process — TermX never
runs a second agent loop around an external engine. See
plans/engine-extensions/tech-specs.md for the frozen contracts.
"""

from .types import (
    EffectiveRunConfiguration,
    EngineCapabilities,
    EngineDescriptor,
    EngineEvent,
    EngineKind,
    EngineSessionBinding,
)

__all__ = [
    "EffectiveRunConfiguration",
    "EngineCapabilities",
    "EngineDescriptor",
    "EngineEvent",
    "EngineKind",
    "EngineSessionBinding",
]
