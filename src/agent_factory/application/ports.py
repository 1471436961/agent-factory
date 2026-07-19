"""Replaceable system boundaries used by deterministic application services."""

from contextvars import Token
from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_factory.domain.models import ResolvedToolSpec


class Clock(Protocol):
    """Provide timezone-aware timestamps."""

    def now(self) -> datetime:
        """Return the current instant with timezone information."""


class IdGenerator(Protocol):
    """Generate identifiers without coupling callers to UUID4."""

    def new(self) -> UUID:
        """Return a new identifier."""


class CorrelationContext(Protocol):
    """Store a request correlation ID in the current async context."""

    def get(self) -> str | None:
        """Return the active correlation ID, if one exists."""

    def set(self, correlation_id: str) -> Token[str | None]:
        """Set an ID and return a token that can restore the prior value."""

    def reset(self, token: Token[str | None]) -> None:
        """Restore the context value represented by ``token``."""


class ToolCatalog(Protocol):
    """Resolve immutable tool metadata without exposing executable handlers."""

    def get(self, name: str) -> ResolvedToolSpec | None:
        """Return the registered tool specification, if it exists."""

    def names(self) -> frozenset[str]:
        """Return all registered tool names."""
