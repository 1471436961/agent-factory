"""Stable, redacted exceptions raised by the Python SDK."""

from uuid import UUID

from agent_factory.domain.common import FrozenJsonObject, JsonObject


class AgentFactorySdkError(Exception):
    """Base class for errors produced by the SDK boundary."""


class AgentFactoryApiError(AgentFactorySdkError):
    """A non-success HTTP response represented without raw response data."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: JsonObject | None,
        correlation_id: UUID,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = FrozenJsonObject(details)
        self.correlation_id = correlation_id
        super().__init__(f"{code}: {message} (HTTP {status_code})")


class AgentFactoryTransportError(AgentFactorySdkError):
    """A request failed before a valid HTTP response was received."""

    def __init__(self, *, correlation_id: UUID, cause_type: str) -> None:
        self.correlation_id = correlation_id
        self.cause_type = cause_type
        super().__init__(
            "Agent Factory request failed before receiving an HTTP response "
            f"(correlation_id={correlation_id}, cause_type={cause_type})"
        )


class AgentFactoryProtocolError(AgentFactorySdkError):
    """The server response did not satisfy the declared SDK contract."""

    def __init__(self, *, status_code: int, correlation_id: UUID) -> None:
        self.status_code = status_code
        self.correlation_id = correlation_id
        super().__init__(
            "Agent Factory response violated the SDK protocol "
            f"(HTTP {status_code}, correlation_id={correlation_id})"
        )


class AgentFactoryClientClosedError(AgentFactorySdkError):
    """The caller attempted to reuse a closed SDK client."""

    def __init__(self) -> None:
        super().__init__("Agent Factory client is closed")
