"""In-memory metadata catalog for tools known to the M1 production layer."""

from collections.abc import Iterable

from agent_factory.domain.models import ResolvedToolSpec
from agent_factory.infrastructure.runtime.registry import default_tool_registry


class InMemoryToolCatalog:
    """Resolve immutable tool specifications without executable handlers."""

    def __init__(self, tools: Iterable[ResolvedToolSpec] = ()) -> None:
        materialized = tuple(tools)
        self._tools = {tool.name: tool for tool in materialized}
        if len(self._tools) != len(materialized):
            raise ValueError("tool catalog contains duplicate names")

    def get(self, name: str) -> ResolvedToolSpec | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)


def default_tool_catalog() -> InMemoryToolCatalog:
    """Return metadata derived from the executable Alpha definitions."""

    registry = default_tool_registry()
    return InMemoryToolCatalog(
        definition.resolved_spec() for definition in registry.definitions()
    )
