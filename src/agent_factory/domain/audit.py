"""Immutable audit events persisted with Agent Factory state changes."""

from uuid import UUID

from pydantic import AwareDatetime, Field

from agent_factory.domain.common import Actor, FrozenModel, JsonObject
from agent_factory.domain.enums import AuditEntityType, AuditEventType


class AuditEvent(FrozenModel):
    event_id: UUID
    event_type: AuditEventType
    entity_type: AuditEntityType
    entity_id: str = Field(min_length=1, max_length=128)
    entity_revision: int | None = Field(default=None, ge=1)
    actor: Actor
    correlation_id: UUID
    causation_id: UUID | None = None
    payload: JsonObject
    created_at: AwareDatetime
