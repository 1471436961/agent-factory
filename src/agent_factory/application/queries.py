"""Transport-neutral query and pagination contracts."""

from __future__ import annotations

from typing import Generic, Self, TypeVar

from pydantic import AwareDatetime, Field, model_validator

from agent_factory.domain.common import Actor, FrozenModel, Slug
from agent_factory.domain.enums import (
    AuditEntityType,
    AuditEventType,
    PrototypeStatus,
)

T = TypeVar("T")


class Page(FrozenModel, Generic[T]):
    items: tuple[T, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class PrototypeListQuery(FrozenModel):
    status: PrototypeStatus | None = None
    agent_type: Slug | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AuditQuery(FrozenModel):
    entity_type: AuditEntityType | None = None
    entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_types: frozenset[AuditEventType] = frozenset()
    actor: Actor | None = None
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
