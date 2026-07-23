"""Pydantic request and response contracts for the REST interface."""

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
from agent_factory.domain.enums import InstanceStatus, ReviewDecision
from agent_factory.domain.evaluation import (
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import AgentDefinition, DomainKnowledgeDraft
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillTreeDraft


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
    skill_tree: SkillTreeRef | None = None
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


class TransitionInstanceRequest(FrozenModel):
    expected_revision: PositiveInt
    target_status: InstanceStatus
    reason: str = Field(min_length=1, max_length=1_000)
    retry: bool = False


class RegisterEvaluationSuiteRequest(EvaluationSuiteDraft):
    pass


class RegisterSkillTreeRequest(SkillTreeDraft):
    pass


class EvaluateInstanceRequest(FrozenModel):
    expected_revision: PositiveInt
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    case_results: Annotated[
        tuple[SubmittedCaseResult, ...],
        Field(min_length=1),
    ]


class ReviewEvaluationRequest(FrozenModel):
    decision: ReviewDecision
    comment: str = Field(default="", max_length=2_000)


class PromoteAgentRequest(FrozenModel):
    expected_revision: PositiveInt
    target_node_id: Slug
    evaluation_report_id: UUID
    evaluation_review_id: UUID | None = None
    knowledge_selections: tuple[KnowledgeSelection, ...] = ()

    @field_validator("knowledge_selections")
    @classmethod
    def knowledge_selections_must_be_unique(
        cls,
        value: tuple[KnowledgeSelection, ...],
    ) -> tuple[KnowledgeSelection, ...]:
        refs = {
            (selection.slot_name, selection.knowledge_id, selection.version)
            for selection in value
        }
        if len(refs) != len(value):
            raise ValueError(
                "knowledge_selections contains duplicate knowledge references"
            )
        return value


class RecordTaskOutcomeRequest(FrozenModel):
    expected_revision: PositiveInt
    task_id: UUID
    skill_node_id: Slug
    passed: bool
    evaluation_report_id: UUID
