"""In-memory metadata catalog for tools known to the M1 production layer."""

from collections.abc import Iterable

from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.models import ResolvedToolSpec


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
    """Return the Alpha catalog; entries are metadata, not executable tools."""

    return InMemoryToolCatalog(
        (
            ResolvedToolSpec(
                name="document-search",
                version="1.0.0",
                description="Search documents bound to the current Agent instance.",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                        },
                    },
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "required": ["results"],
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["knowledge_id", "content"],
                                "properties": {
                                    "knowledge_id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "score": {"type": "number"},
                                },
                                "additionalProperties": False,
                            },
                        }
                    },
                    "additionalProperties": False,
                },
                permission_tags=frozenset({ToolPermission.READ_ONLY}),
            ),
        )
    )
