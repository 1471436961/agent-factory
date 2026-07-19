"""Pure application policy for metadata-only tool resolution."""

from collections.abc import Iterable

from agent_factory.application.ports import ToolCatalog
from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.errors import ToolPermissionDeniedError, UnknownToolError
from agent_factory.domain.models import ResolvedToolSpec


class ToolPolicy:
    """Resolve declared tools and enforce a configurable permission ceiling."""

    def __init__(
        self,
        catalog: ToolCatalog,
        *,
        allowed_permissions: frozenset[ToolPermission],
    ) -> None:
        self._catalog = catalog
        self._allowed_permissions = allowed_permissions

    def resolve(self, names: Iterable[str]) -> tuple[ResolvedToolSpec, ...]:
        resolved: list[ResolvedToolSpec] = []
        for name in names:
            tool = self._catalog.get(name)
            if tool is None:
                raise UnknownToolError(details={"tool_name": name})
            denied = tool.permission_tags - self._allowed_permissions
            if denied:
                raise ToolPermissionDeniedError(
                    details={
                        "tool_name": name,
                        "denied_permissions": sorted(
                            permission.value for permission in denied
                        ),
                    }
                )
            resolved.append(tool)
        return tuple(sorted(resolved, key=lambda tool: tool.name))
