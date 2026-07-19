"""Validated application inputs for the M1 production chain."""

from typing import TypeAlias
from uuid import UUID

from pydantic import Field, PositiveInt

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
    selections: tuple[KnowledgeSelection, ...]
    replace_existing: bool = False
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None
