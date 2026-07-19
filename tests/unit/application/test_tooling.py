"""Unit tests for metadata-only tool resolution policy."""

import pytest

from agent_factory.application.tooling import ToolPolicy
from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.errors import ToolPermissionDeniedError, UnknownToolError
from agent_factory.domain.models import ResolvedToolSpec


class StubToolCatalog:
    def __init__(self, *tools: ResolvedToolSpec) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> ResolvedToolSpec | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)


def _tool(*, permissions: frozenset[ToolPermission]) -> ResolvedToolSpec:
    return ResolvedToolSpec(
        name="document-search",
        version="1.0.0",
        description="Search bound documents.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission_tags=permissions,
    )


def test_tool_policy_resolves_allowed_metadata() -> None:
    tool = _tool(permissions=frozenset({ToolPermission.READ_ONLY}))
    policy = ToolPolicy(
        StubToolCatalog(tool),
        allowed_permissions=frozenset({ToolPermission.READ_ONLY}),
    )

    assert policy.resolve(("document-search",)) == (tool,)


def test_tool_policy_rejects_unknown_and_denied_tools() -> None:
    denied_tool = _tool(permissions=frozenset({ToolPermission.NETWORK}))
    policy = ToolPolicy(
        StubToolCatalog(denied_tool),
        allowed_permissions=frozenset({ToolPermission.READ_ONLY}),
    )

    with pytest.raises(UnknownToolError):
        policy.resolve(("missing-tool",))
    with pytest.raises(ToolPermissionDeniedError):
        policy.resolve(("document-search",))
