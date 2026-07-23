"""Runtime adapters and fixed executable tool infrastructure."""

from agent_factory.infrastructure.runtime.demo import OfflineDemoRuntimeAdapter
from agent_factory.infrastructure.runtime.model import ModelRuntimeAdapter
from agent_factory.infrastructure.runtime.registry import (
    DocumentSearchHit,
    DocumentSearchInput,
    DocumentSearchOutput,
    InMemoryToolRegistry,
    default_tool_registry,
)

__all__ = [
    "DocumentSearchHit",
    "DocumentSearchInput",
    "DocumentSearchOutput",
    "InMemoryToolRegistry",
    "ModelRuntimeAdapter",
    "OfflineDemoRuntimeAdapter",
    "default_tool_registry",
]
