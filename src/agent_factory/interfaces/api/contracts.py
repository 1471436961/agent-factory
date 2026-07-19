"""Pydantic request and response contracts for the M1 REST interface."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, PositiveInt, field_validator

from agent_factory.application.commands import KnowledgeSelection
from agent_factory.domain.common import (
    FrozenModel,
    JsonObject,
    SemVer,
    Slug,
)
from agent_factory.domain.models import AgentDefinition, DomainKnowledgeDraft


class ErrorBody(FrozenModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1_000)
    details: JsonObject = Field(default_factory=dict)
    correlation_id: UUID


class ErrorResponse(FrozenModel):
    error: ErrorBody


class HealthResponse(FrozenModel):
    status: str = Field(pattern=r"^ok$")


class RegisterPrototypeRequest(FrozenModel):
    prototype_id: Slug
    version: SemVer
    definition: AgentDefinition
    publish: bool = False


class DeprecatePrototypeRequest(FrozenModel):
    reason: str = Field(min_length=1, max_length=1_000)


class CloneAgentRequest(FrozenModel):
    runtime_target: Slug | None = None


class RegisterKnowledgeRequest(DomainKnowledgeDraft):
    pass


class BindKnowledgeRequest(FrozenModel):
    expected_revision: PositiveInt
    selections: Annotated[tuple[KnowledgeSelection, ...], Field(min_length=1)]
    replace_existing: bool = False

    @field_validator("selections")
    @classmethod
    def selections_must_be_unique(
        cls,
        value: tuple[KnowledgeSelection, ...],
    ) -> tuple[KnowledgeSelection, ...]:
        refs = {
            (selection.slot_name, selection.knowledge_id, selection.version)
            for selection in value
        }
        if len(refs) != len(value):
            raise ValueError("selections contains duplicate knowledge references")
        return value


class ExportSpecRequest(FrozenModel):
    revision: PositiveInt | None = None
