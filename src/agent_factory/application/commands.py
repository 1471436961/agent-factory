"""Validated application inputs for the M1 production chain."""

from typing import Annotated, TypeAlias
from uuid import UUID

from pydantic import Field, PositiveInt, field_validator

from agent_factory.domain.common import (
    Actor,
    FrozenModel,
    IdempotencyKey,
    SemVer,
    Slug,
)
from agent_factory.domain.models import AgentDefinition, DomainKnowledgeDraft

OptionalIdempotencyKey: TypeAlias = IdempotencyKey | None


class RegisterPrototypeCommand(FrozenModel):
    prototype_id: Slug
    version: SemVer
    definition: AgentDefinition
    publish: bool = False
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None


class PublishPrototypeCommand(FrozenModel):
    prototype_id: Slug
    version: SemVer
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None


class DeprecatePrototypeCommand(FrozenModel):
    prototype_id: Slug
    version: SemVer
    reason: str = Field(min_length=1, max_length=1_000)
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None


class CloneAgentCommand(FrozenModel):
    prototype_id: Slug
    prototype_version: SemVer
    runtime_target: Slug | None = None
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None


class RegisterKnowledgeCommand(FrozenModel):
    knowledge: DomainKnowledgeDraft
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None


class KnowledgeSelection(FrozenModel):
    slot_name: Slug
    knowledge_id: Slug
    version: SemVer


class BindKnowledgeCommand(FrozenModel):
    instance_id: UUID
    expected_revision: PositiveInt
    selections: Annotated[tuple[KnowledgeSelection, ...], Field(min_length=1)]
    replace_existing: bool = False
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None

    @field_validator("selections")
    @classmethod
    def selections_must_be_unique(
        cls,
        value: tuple[KnowledgeSelection, ...],
    ) -> tuple[KnowledgeSelection, ...]:
        keys = {
            (selection.slot_name, selection.knowledge_id, selection.version)
            for selection in value
        }
        if len(keys) != len(value):
            raise ValueError("selections contains duplicate knowledge references")
        return value
