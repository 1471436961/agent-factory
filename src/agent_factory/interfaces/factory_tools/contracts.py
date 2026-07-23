"""Transport-neutral contracts exposed to Factory Tool hosts."""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from agent_factory.application.commands import OptionalIdempotencyKey
from agent_factory.application.queries import PrototypeListQuery
from agent_factory.application.security import FactoryPermission, Principal
from agent_factory.domain.common import (
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    SemVer,
    Slug,
)
from agent_factory.domain.enums import AuditEntityType, AuditEventType
from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest,
    CloneAgentRequest,
    PromoteAgentRequest,
)

ToolName = Annotated[
    str,
    Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]
ErrorCode = Annotated[
    str,
    Field(min_length=3, max_length=128, pattern=r"^[A-Z][A-Z0-9_]+$"),
]


class ListPrototypesToolInput(PrototypeListQuery):
    """Model-visible prototype filters."""


class CloneAgentToolInput(CloneAgentRequest):
    """Model-visible clone input, including REST path parameters."""

    prototype_id: Slug
    version: SemVer


class BindKnowledgeToolInput(BindKnowledgeRequest):
    """Model-visible knowledge binding input, including the instance ID."""

    instance_id: UUID


class ApplyPromotionToolInput(PromoteAgentRequest):
    """Model-visible promotion input, including the instance ID."""

    instance_id: UUID


class QueryAuditLogToolInput(FrozenModel):
    """Model-visible audit filters without an actor identity field."""

    entity_type: AuditEntityType | None = None
    entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_types: frozenset[AuditEventType] = frozenset()
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def time_range_must_be_ordered(self) -> Self:
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from must not exceed created_to")
        return self


class FactoryToolCallContext(FrozenModel):
    """Trusted metadata supplied by an authenticated tool host."""

    request_id: UUID
    correlation_id: UUID
    principal: Principal
    idempotency_key: OptionalIdempotencyKey = None


class FactoryToolDefinition(FrozenModel):
    """Provider-neutral schema and permission metadata for one factory tool."""

    name: ToolName
    description: str = Field(min_length=1, max_length=1_000)
    input_schema: JsonObject
    output_schema: JsonObject
    required_permission: FactoryPermission


class FactoryToolError(FrozenModel):
    """Stable and safe error returned without transport-specific status codes."""

    code: ErrorCode
    message: str = Field(min_length=1, max_length=1_000)
    details: JsonObject = Field(default_factory=FrozenJsonObject)


class FactoryToolResult(FrozenModel):
    """Discriminated-by-invariant result envelope for every factory tool call."""

    request_id: UUID
    correlation_id: UUID
    ok: bool
    output: JsonObject | None = None
    error: FactoryToolError | None = None

    @model_validator(mode="after")
    def success_and_error_fields_must_match_status(self) -> Self:
        if self.ok and (self.output is None or self.error is not None):
            raise ValueError("successful result requires output without error")
        if not self.ok and (self.output is not None or self.error is None):
            raise ValueError("failed result requires error without output")
        return self
