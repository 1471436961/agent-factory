"""Default implementations of clock, identity, and correlation boundaries."""

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    """UTC wall clock used outside deterministic tests."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UUID4Generator:
    """Cryptographically strong random UUID generator."""

    def new(self) -> UUID:
        return uuid4()


class ContextVarCorrelationContext:
    """Async-task-local correlation storage backed by ``ContextVar``."""

    def __init__(self) -> None:
        self._correlation_id: ContextVar[str | None] = ContextVar(
            "agent_factory_correlation_id",
            default=None,
        )

    def get(self) -> str | None:
        return self._correlation_id.get()

    def set(self, correlation_id: str) -> Token[str | None]:
        normalized = correlation_id.strip()
        if not normalized:
            raise ValueError("correlation_id must not be blank")
        return self._correlation_id.set(normalized)

    def reset(self, token: Token[str | None]) -> None:
        self._correlation_id.reset(token)
