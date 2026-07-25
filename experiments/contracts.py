"""Strict contracts for M5 experiment definitions and immutable artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

import regex
from pydantic import (
    AwareDatetime,
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_factory.domain.common import (
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    canonical_json_bytes,
)
from agent_factory.domain.enums import AuditEventType

ArtifactPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    ),
]
JsonFieldName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$"),
]


class ExperimentCondition(StrEnum):
    MANUAL = "manual-agent"
    FACTORY = "factory-agent"


class ExperimentScenario(StrEnum):
    CONSISTENCY = "consistency"
    ADAPTATION = "adaptation"


class MatcherKind(StrEnum):
    EXACT = "exact"
    REGEX = "regex"


class MatchExpectation(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PROVIDER_FAILED = "provider-failed"
    TIMED_OUT = "timed-out"
    FILTERED = "filtered"
    INVALID_RESPONSE = "invalid-response"
    BUDGET_STOPPED = "budget-stopped"


class AttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PROVIDER_FAILED = "provider-failed"
    TIMED_OUT = "timed-out"
    FILTERED = "filtered"
    INVALID_RESPONSE = "invalid-response"


class TextMatcher(FrozenModel):
    kind: MatcherKind
    pattern: str = Field(min_length=1, max_length=1_000)
    case_sensitive: bool = False

    @field_validator("pattern")
    @classmethod
    def regex_pattern_must_compile(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("kind") is MatcherKind.REGEX:
            try:
                regex.compile(value)
            except regex.error as exc:
                raise ValueError("regex matcher pattern must compile") from exc
        return value


class FactDefinition(FrozenModel):
    fact_id: Slug
    statement: str = Field(min_length=1, max_length=1_000)
    accepted_matchers: Annotated[tuple[TextMatcher, ...], Field(min_length=1)]

    @field_validator("accepted_matchers")
    @classmethod
    def matchers_must_be_unique(
        cls,
        value: tuple[TextMatcher, ...],
    ) -> tuple[TextMatcher, ...]:
        if len(value) != len(set(value)):
            raise ValueError("accepted_matchers contains duplicates")
        return value


class KnowledgeFixture(FrozenModel):
    domain_id: Slug
    knowledge_id: Slug
    version: SemVer
    name: str = Field(min_length=1, max_length=256)
    content_path: ArtifactPath
    content_checksum: Sha256
    synthetic: Literal[True] = True
    facts: Annotated[tuple[FactDefinition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def fact_ids_must_be_unique(self) -> Self:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("knowledge facts contain duplicate fact_id values")
        return self


class ExperimentKnowledgeRef(FrozenModel):
    knowledge_id: Slug
    version: SemVer
    checksum: Sha256


class ExperimentTaskInput(FrozenModel):
    task_id: Slug
    domain_id: Slug
    scenario: ExperimentScenario
    instruction: str = Field(min_length=1, max_length=8_000)
    reader_profile: str = Field(min_length=1, max_length=1_000)
    knowledge: ExperimentKnowledgeRef
    rubric_id: Slug


class ExperimentTask(ExperimentTaskInput):
    output_schema: JsonObject


class TaskBundle(FrozenModel):
    domain_id: Slug
    output_schema: JsonObject
    tasks: Annotated[tuple[ExperimentTaskInput, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def task_identity_must_match_bundle(self) -> Self:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task bundle contains duplicate task_id values")
        if any(task.domain_id != self.domain_id for task in self.tasks):
            raise ValueError("task domain_id must match its bundle")
        return self


class PersonalizationConstraint(FrozenModel):
    constraint_id: Slug
    description: str = Field(min_length=1, max_length=1_000)
    expectation: MatchExpectation
    matcher: TextMatcher
    target_field: JsonFieldName | None = None


class RubricDefinition(FrozenModel):
    rubric_id: Slug
    task_id: Slug
    required_fact_ids: Annotated[tuple[Slug, ...], Field(min_length=1)]
    forbidden_matchers: tuple[TextMatcher, ...] = ()
    personalization_constraints: tuple[PersonalizationConstraint, ...] = ()

    @model_validator(mode="after")
    def rubric_items_must_be_unique(self) -> Self:
        if len(self.required_fact_ids) != len(set(self.required_fact_ids)):
            raise ValueError("required_fact_ids contains duplicates")
        if len(self.forbidden_matchers) != len(set(self.forbidden_matchers)):
            raise ValueError("forbidden_matchers contains duplicates")
        constraint_ids = [
            constraint.constraint_id for constraint in self.personalization_constraints
        ]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("personalization constraint IDs must be unique")
        return self


class RubricBundle(FrozenModel):
    domain_id: Slug
    rubrics: Annotated[tuple[RubricDefinition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def rubric_ids_and_tasks_must_be_unique(self) -> Self:
        rubric_ids = [rubric.rubric_id for rubric in self.rubrics]
        task_ids = [rubric.task_id for rubric in self.rubrics]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise ValueError("rubric bundle contains duplicate rubric_id values")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("rubric bundle contains duplicate task_id values")
        return self


class HypothesisThresholds(FrozenModel):
    h1_support_min_absolute_difference: float = Field(ge=0, le=1)
    h1_not_support_below: float = Field(ge=0, le=1)
    h2_support_min_relative_reduction: float = Field(ge=0, le=1)
    h4_noninferiority_margin: float = Field(ge=-1, le=0)

    @model_validator(mode="after")
    def h1_thresholds_must_leave_an_inconclusive_band(self) -> Self:
        if self.h1_not_support_below >= self.h1_support_min_absolute_difference:
            raise ValueError("H1 not-support threshold must be below support threshold")
        return self


class ExperimentDefinition(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    title: str = Field(min_length=1, max_length=256)
    domain_ids: Annotated[tuple[Slug, ...], Field(min_length=1)]
    conditions: tuple[ExperimentCondition, ...] = (
        ExperimentCondition.MANUAL,
        ExperimentCondition.FACTORY,
    )
    repetitions: int = Field(default=5, ge=1, le=20)
    randomization_seed: int = Field(ge=0, le=2**63 - 1)
    expected_task_count: PositiveInt
    knowledge_files: Annotated[tuple[ArtifactPath, ...], Field(min_length=1)]
    task_files: Annotated[tuple[ArtifactPath, ...], Field(min_length=1)]
    rubric_files: Annotated[tuple[ArtifactPath, ...], Field(min_length=1)]
    thresholds: HypothesisThresholds

    @model_validator(mode="after")
    def collections_must_be_unique_sorted_and_complete(self) -> Self:
        if set(self.conditions) != set(ExperimentCondition):
            raise ValueError("conditions must contain MANUAL and FACTORY exactly once")
        for name, values in (
            ("domain_ids", self.domain_ids),
            ("knowledge_files", self.knowledge_files),
            ("task_files", self.task_files),
            ("rubric_files", self.rubric_files),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} contains duplicates")
            if tuple(sorted(values)) != values:
                raise ValueError(f"{name} must use canonical sorted order")
        if not (
            len(self.domain_ids)
            == len(self.knowledge_files)
            == len(self.task_files)
            == len(self.rubric_files)
        ):
            raise ValueError("each domain requires one knowledge, task and rubric file")
        return self


class GenerationConfig(FrozenModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    sdk_version: str = Field(min_length=1, max_length=64)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(gt=0, le=128_000)
    seed: int | None = None
    request_timeout_seconds: float = Field(gt=0, le=600)
    max_attempts: int = Field(default=3, ge=1, le=3)
    concurrency: int = Field(default=1, ge=1, le=16)


class ExecutionLimits(FrozenModel):
    max_provider_requests: PositiveInt
    max_prompt_tokens: PositiveInt
    max_completion_tokens: PositiveInt
    prompt_tokens_per_attempt_upper_bound: PositiveInt

    @model_validator(mode="after")
    def per_attempt_reservation_must_fit_total(self) -> Self:
        if self.prompt_tokens_per_attempt_upper_bound > self.max_prompt_tokens:
            raise ValueError("per-attempt prompt reservation exceeds total limit")
        return self


class ExecutionPlanItem(FrozenModel):
    run_id: UUID
    condition: ExperimentCondition
    task_id: Slug
    repetition: int = Field(ge=1, le=20)
    execution_order: PositiveInt


class ExecutionPlan(FrozenModel):
    experiment_id: Slug
    definition_checksum: Sha256
    randomization_seed: int = Field(ge=0, le=2**63 - 1)
    items: Annotated[tuple[ExecutionPlanItem, ...], Field(min_length=1)]
    plan_checksum: Sha256

    @model_validator(mode="after")
    def plan_identity_must_be_unique(self) -> Self:
        run_ids = [item.run_id for item in self.items]
        orders = [item.execution_order for item in self.items]
        coordinates = [
            (item.condition, item.task_id, item.repetition) for item in self.items
        ]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("execution plan contains duplicate run_id values")
        if len(orders) != len(set(orders)) or set(orders) != set(
            range(1, len(orders) + 1)
        ):
            raise ValueError("execution_order must be contiguous and unique")
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("execution plan contains duplicate run coordinates")
        return self


class ExecutionManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    dataset_checksum: Sha256
    plan_checksum: Sha256
    condition_bundle_checksum: Sha256
    generation: GenerationConfig
    limits: ExecutionLimits
    manifest_checksum: Sha256

    @model_validator(mode="after")
    def m5_3_requires_sequential_execution(self) -> Self:
        if self.generation.concurrency != 1:
            raise ValueError("M5.3 executor supports concurrency=1 only")
        return self


class RenderedInvocation(FrozenModel):
    renderer_version: Literal["1.0"] = "1.0"
    condition: ExperimentCondition
    task_id: Slug
    instructions: str = Field(min_length=1, max_length=64_000)
    task_input: str = Field(min_length=1, max_length=128_000)
    output_schema: JsonObject | None = None
    knowledge_checksum: Sha256
    agent_spec_checksum: Sha256 | None = None
    prompt_hash: Sha256

    @model_validator(mode="after")
    def provenance_must_match_condition(self) -> Self:
        if self.condition is ExperimentCondition.FACTORY:
            if self.agent_spec_checksum is None or self.output_schema is None:
                raise ValueError("FACTORY invocation requires AgentSpec provenance")
        elif self.agent_spec_checksum is not None:
            raise ValueError("MANUAL invocation cannot claim AgentSpec provenance")
        visible = {
            "instructions": self.instructions,
            "task_input": self.task_input,
            "output_schema": self.output_schema,
        }
        expected_hash = hashlib.sha256(canonical_json_bytes(visible)).hexdigest()
        if self.prompt_hash != expected_hash:
            raise ValueError("rendered invocation prompt_hash does not match")
        return self


class ExperimentRunRequest(FrozenModel):
    run_id: UUID
    experiment_id: Slug
    manifest_checksum: Sha256
    plan_checksum: Sha256
    condition: ExperimentCondition
    task_id: Slug
    repetition: int = Field(ge=1, le=20)
    execution_order: PositiveInt
    generation: GenerationConfig
    invocation: JsonObject
    prompt_hash: Sha256
    knowledge_checksum: Sha256
    agent_spec_checksum: Sha256 | None = None
    started_at: AwareDatetime

    @model_validator(mode="after")
    def agent_spec_provenance_must_match_condition(self) -> Self:
        if (
            self.condition is ExperimentCondition.FACTORY
            and self.agent_spec_checksum is None
        ):
            raise ValueError("FACTORY run request requires AgentSpec provenance")
        if (
            self.condition is ExperimentCondition.MANUAL
            and self.agent_spec_checksum is not None
        ):
            raise ValueError("MANUAL run request cannot claim AgentSpec provenance")
        expected_hash = hashlib.sha256(
            canonical_json_bytes(self.invocation)
        ).hexdigest()
        if self.prompt_hash != expected_hash:
            raise ValueError("run request prompt_hash does not match invocation")
        return self


class AttemptIntent(FrozenModel):
    run_id: UUID
    manifest_checksum: Sha256
    attempt_number: int = Field(ge=1, le=3)
    prompt_hash: Sha256
    reserved_prompt_tokens: PositiveInt
    reserved_completion_tokens: PositiveInt
    backoff_seconds: float = Field(ge=0, le=300)
    started_at: AwareDatetime


class RunAttempt(FrozenModel):
    attempt_number: int = Field(ge=1, le=3)
    status: AttemptStatus
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=256)
    response: JsonObject | None = None
    error_response: JsonObject | None = None
    output_text: str | None = Field(default=None, max_length=256_000)
    structured_output: JsonObject | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    retryable: bool = False
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def terminal_fields_must_match_status(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("attempt completed_at must not precede started_at")
        if self.status is AttemptStatus.SUCCEEDED:
            if (
                self.response is None
                or self.error_code is not None
                or self.error_response is not None
                or self.output_text is None
                or self.structured_output is None
                or self.retryable
            ):
                raise ValueError("successful attempt requires response without error")
        elif (
            self.error_code is None
            or self.response is not None
            or self.output_text is not None
            or self.structured_output is not None
        ):
            raise ValueError("failed attempt requires error without response")
        return self


class AttemptCompletion(FrozenModel):
    run_id: UUID
    manifest_checksum: Sha256
    attempt: RunAttempt


class ExperimentRun(FrozenModel):
    run_id: UUID
    experiment_id: Slug
    manifest_checksum: Sha256
    plan_checksum: Sha256
    condition: ExperimentCondition
    task_id: Slug
    repetition: int = Field(ge=1, le=20)
    execution_order: PositiveInt
    generation: GenerationConfig
    invocation: JsonObject
    prompt_hash: Sha256
    knowledge_checksum: Sha256
    agent_spec_checksum: Sha256 | None = None
    status: RunStatus
    attempts: tuple[RunAttempt, ...] = ()
    output_text: str | None = Field(default=None, max_length=256_000)
    structured_output: JsonObject | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def run_terminal_state_must_be_consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("run completed_at must not precede started_at")
        numbers = [attempt.attempt_number for attempt in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("attempt numbers must be contiguous from one")
        if len(self.attempts) > self.generation.max_attempts:
            raise ValueError("run exceeds generation max_attempts")
        if self.status is RunStatus.BUDGET_STOPPED:
            if self.attempts:
                raise ValueError("budget-stopped run cannot contain provider attempts")
            if self.output_text is not None or self.structured_output is not None:
                raise ValueError("budget-stopped run cannot contain final output")
            return self
        if not self.attempts:
            raise ValueError("provider terminal run requires at least one attempt")
        expected_attempt = AttemptStatus(self.status.value)
        if self.attempts[-1].status is not expected_attempt:
            raise ValueError("run status must match its final attempt")
        if self.status is RunStatus.SUCCEEDED:
            if self.structured_output is None or self.output_text is None:
                raise ValueError("successful run requires final structured output")
            if (
                self.output_text != self.attempts[-1].output_text
                or self.structured_output != self.attempts[-1].structured_output
            ):
                raise ValueError("run output must match its final attempt")
        elif self.structured_output is not None or self.output_text is not None:
            raise ValueError("failed run cannot contain final output")
        return self


class MetricRecord(FrozenModel):
    run_id: UUID
    run_status: RunStatus
    schema_passed: bool | None = None
    required_facts_total: int | None = Field(default=None, ge=1)
    required_facts_covered: int | None = Field(default=None, ge=0)
    personalization_total: int | None = Field(default=None, ge=0)
    personalization_satisfied: int | None = Field(default=None, ge=0)
    deterministic_quality_score: float | None = Field(default=None, ge=0, le=1)
    human_quality_score: float | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def metric_fields_must_match_run_status(self) -> Self:
        deterministic = (
            self.schema_passed,
            self.required_facts_total,
            self.required_facts_covered,
            self.personalization_total,
            self.personalization_satisfied,
            self.deterministic_quality_score,
        )
        if self.run_status is not RunStatus.SUCCEEDED:
            quality_scores = (*deterministic, self.human_quality_score)
            if any(value is not None for value in quality_scores):
                raise ValueError("failed run cannot contain quality scores")
            return self
        if any(value is None for value in deterministic):
            raise ValueError("successful run requires complete deterministic scores")
        assert self.required_facts_total is not None
        assert self.required_facts_covered is not None
        assert self.personalization_total is not None
        assert self.personalization_satisfied is not None
        if self.required_facts_covered > self.required_facts_total:
            raise ValueError("covered facts cannot exceed total facts")
        if self.personalization_satisfied > self.personalization_total:
            raise ValueError("satisfied constraints cannot exceed total constraints")
        return self


class BuildSession(FrozenModel):
    session_id: UUID
    condition: ExperimentCondition
    domain_id: Slug
    sequence_number: PositiveInt
    active_seconds: int = Field(ge=0)
    wall_clock_seconds: int = Field(ge=0)
    excluded_wait_seconds: int = Field(ge=0)
    successful: bool

    @model_validator(mode="after")
    def durations_must_be_possible(self) -> Self:
        if self.active_seconds + self.excluded_wait_seconds > self.wall_clock_seconds:
            raise ValueError("active and excluded durations exceed wall clock duration")
        return self


class AuditStepResult(FrozenModel):
    step_id: Slug
    expected_event_type: AuditEventType
    matched_event_id: UUID | None = None
    passed: bool
    reason: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def result_metadata_must_match_passed(self) -> Self:
        if self.passed and (self.matched_event_id is None or self.reason is not None):
            raise ValueError("passed audit step requires event without failure reason")
        if not self.passed and self.reason is None:
            raise ValueError("failed audit step requires reason")
        return self


class AuditVerificationRecord(FrozenModel):
    verification_id: UUID
    experiment_id: Slug
    instance_id: UUID
    checked_at: AwareDatetime
    steps: Annotated[tuple[AuditStepResult, ...], Field(min_length=1)]
    completeness: float = Field(ge=0, le=1)
    passed: bool

    @model_validator(mode="after")
    def completeness_must_match_steps(self) -> Self:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("audit verification contains duplicate step IDs")
        calculated = sum(step.passed for step in self.steps) / len(self.steps)
        if abs(calculated - self.completeness) > 1e-12:
            raise ValueError("audit completeness does not match step results")
        if self.passed != (self.completeness == 1.0):
            raise ValueError("audit passed requires complete evidence")
        return self


def model_payload(model: FrozenModel) -> Mapping[str, object]:
    """Return a typed JSON-mode payload for aggregate experiment checksums."""

    return cast(
        Mapping[str, object],
        model.model_dump(mode="json", exclude_none=False),
    )
