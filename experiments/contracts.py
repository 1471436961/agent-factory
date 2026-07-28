"""Strict contracts for M5 experiment definitions and immutable artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

import regex
from pydantic import (
    AliasChoices,
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
    sha256_model,
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
EvidenceArtifactPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$",
    ),
]
FreezeArtifactPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=(
            r"^(?:[A-Za-z0-9][A-Za-z0-9._/-]*|"
            r"\.tmp/[A-Za-z0-9][A-Za-z0-9._/-]*)$"
        ),
    ),
]
GitCommit = Annotated[str, Field(pattern=r"^[a-f0-9]{40,64}$")]
HttpsUrl = Annotated[
    str,
    Field(min_length=9, max_length=2_048, pattern=r"^https://[^\s]+$"),
]
CurrencyCode = Literal["USD", "CNY"]
PositiveCurrencyMicros = Annotated[int, Field(strict=True, gt=0, le=10**12)]
JsonFieldName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$"),
]


class ExperimentCondition(StrEnum):
    MANUAL = "manual-agent"
    FACTORY = "factory-agent"


class ExperimentPurpose(StrEnum):
    PILOT = "pilot"
    FORMAL = "formal"


class ExperimentScenario(StrEnum):
    CONSISTENCY = "consistency"
    ADAPTATION = "adaptation"


class AnalysisPopulation(StrEnum):
    INTENTION_TO_TREAT = "intention-to-treat"
    SUCCEEDED_ONLY = "succeeded-only"


class HypothesisName(StrEnum):
    H1_SCHEMA_CONSISTENCY = "h1-schema-consistency"
    H2_KNOWLEDGE_OMISSION = "h2-knowledge-omission"
    H4_PERSONALIZATION = "h4-personalization"


class HypothesisDecision(StrEnum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not-supported"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"
    NOT_EVALUATED = "not-evaluated"


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
    tasks_per_scenario_per_domain: int = Field(default=2, ge=1, le=20)
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
        expected_task_count = (
            len(self.domain_ids)
            * len(ExperimentScenario)
            * self.tasks_per_scenario_per_domain
        )
        if self.expected_task_count != expected_task_count:
            raise ValueError("expected_task_count does not match the scenario matrix")
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
    provider_options: JsonObject = Field(default_factory=dict)


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
    schema_version: Literal["1.1"] = "1.1"
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
        if (self.prompt_tokens is None) != (self.completion_tokens is None):
            raise ValueError("attempt usage must provide both token counts")
        if self.status is AttemptStatus.SUCCEEDED:
            if (
                self.response is None
                or self.error_code is not None
                or self.error_response is not None
                or self.output_text is None
                or self.structured_output is None
                or self.prompt_tokens is None
                or self.completion_tokens is None
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
    forbidden_matchers_total: int | None = Field(default=None, ge=0)
    forbidden_matchers_violated: int | None = Field(default=None, ge=0)
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
            self.forbidden_matchers_total,
            self.forbidden_matchers_violated,
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
        assert self.forbidden_matchers_total is not None
        assert self.forbidden_matchers_violated is not None
        assert self.personalization_total is not None
        assert self.personalization_satisfied is not None
        if self.required_facts_covered > self.required_facts_total:
            raise ValueError("covered facts cannot exceed total facts")
        if self.forbidden_matchers_violated > self.forbidden_matchers_total:
            raise ValueError("violated forbidden matchers cannot exceed total")
        if self.personalization_satisfied > self.personalization_total:
            raise ValueError("satisfied constraints cannot exceed total constraints")
        return self


class SchemaViolation(FrozenModel):
    instance_path: str = Field(min_length=1, max_length=1_000)
    schema_path: str = Field(min_length=1, max_length=1_000)
    validator: str = Field(min_length=1, max_length=128)


class FactCheck(FrozenModel):
    fact_id: Slug
    covered: bool
    matched_by_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def matcher_index_must_match_coverage(self) -> Self:
        if self.covered != (self.matched_by_index is not None):
            raise ValueError("fact coverage must match matcher index evidence")
        return self


class ForbiddenMatcherCheck(FrozenModel):
    matcher_index: int = Field(ge=0)
    violated: bool


class PersonalizationCheck(FrozenModel):
    constraint_id: Slug
    expectation: MatchExpectation
    target_field: JsonFieldName | None = None
    matcher_matched: bool
    satisfied: bool

    @model_validator(mode="after")
    def satisfaction_must_match_expectation(self) -> Self:
        expected = (
            self.matcher_matched
            if self.expectation is MatchExpectation.PRESENT
            else not self.matcher_matched
        )
        if self.satisfied != expected:
            raise ValueError("personalization satisfaction contradicts expectation")
        return self


class RunScoreRecord(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    scorer_version: Literal["1.0"] = "1.0"
    run_id: UUID
    run_checksum: Sha256
    experiment_id: Slug
    plan_checksum: Sha256
    condition: ExperimentCondition
    task_id: Slug
    scenario: ExperimentScenario
    repetition: int = Field(ge=1, le=20)
    execution_order: PositiveInt
    run_status: RunStatus
    rubric_id: Slug
    rubric_checksum: Sha256
    schema_passed: bool | None = None
    schema_violations: Annotated[
        tuple[SchemaViolation, ...],
        Field(max_length=128),
    ] = ()
    fact_checks: tuple[FactCheck, ...] = ()
    forbidden_checks: tuple[ForbiddenMatcherCheck, ...] = ()
    personalization_checks: tuple[PersonalizationCheck, ...] = ()
    metric: MetricRecord

    @model_validator(mode="after")
    def evidence_must_match_metric(self) -> Self:
        if self.metric.run_id != self.run_id:
            raise ValueError("score metric references another run")
        if self.metric.run_status is not self.run_status:
            raise ValueError("score metric status does not match run")
        if self.metric.human_quality_score is not None:
            raise ValueError("deterministic run score cannot contain human rating")
        self._validate_unique_evidence()
        if self.run_status is not RunStatus.SUCCEEDED:
            if self.schema_passed is not None or any(
                (
                    self.schema_violations,
                    self.fact_checks,
                    self.forbidden_checks,
                    self.personalization_checks,
                )
            ):
                raise ValueError("failed run cannot contain deterministic checks")
            return self
        if self.schema_passed is None or not self.fact_checks:
            raise ValueError("successful run requires schema and fact checks")
        if self.schema_passed != (not self.schema_violations):
            raise ValueError("schema result contradicts violation evidence")
        self._validate_successful_counts()
        return self

    def _validate_unique_evidence(self) -> None:
        violations = [
            (item.instance_path, item.schema_path, item.validator)
            for item in self.schema_violations
        ]
        if len(violations) != len(set(violations)) or violations != sorted(violations):
            raise ValueError("schema violations must be unique and sorted")
        fact_ids = [item.fact_id for item in self.fact_checks]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact checks contain duplicate fact IDs")
        matcher_indices = [item.matcher_index for item in self.forbidden_checks]
        if matcher_indices != list(range(len(matcher_indices))):
            raise ValueError("forbidden matcher indices must be contiguous")
        constraint_ids = [item.constraint_id for item in self.personalization_checks]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("personalization checks contain duplicate IDs")

    def _validate_successful_counts(self) -> None:
        metric = self.metric
        assert self.schema_passed is not None
        assert metric.required_facts_total is not None
        assert metric.required_facts_covered is not None
        assert metric.forbidden_matchers_total is not None
        assert metric.forbidden_matchers_violated is not None
        assert metric.personalization_total is not None
        assert metric.personalization_satisfied is not None
        assert metric.deterministic_quality_score is not None
        if metric.schema_passed is not self.schema_passed:
            raise ValueError("score metric schema result does not match checks")
        expected_counts = (
            len(self.fact_checks),
            sum(item.covered for item in self.fact_checks),
            len(self.forbidden_checks),
            sum(item.violated for item in self.forbidden_checks),
            len(self.personalization_checks),
            sum(item.satisfied for item in self.personalization_checks),
        )
        actual_counts = (
            metric.required_facts_total,
            metric.required_facts_covered,
            metric.forbidden_matchers_total,
            metric.forbidden_matchers_violated,
            metric.personalization_total,
            metric.personalization_satisfied,
        )
        if actual_counts != expected_counts:
            raise ValueError("score metric counts do not match check evidence")
        if self.scenario is ExperimentScenario.CONSISTENCY:
            if self.personalization_checks:
                raise ValueError("consistency score cannot contain personalization")
        elif not self.personalization_checks:
            raise ValueError("adaptation score requires personalization checks")
        components = [
            float(self.schema_passed),
            metric.required_facts_covered / metric.required_facts_total,
        ]
        if metric.forbidden_matchers_total:
            components.append(
                1 - metric.forbidden_matchers_violated / metric.forbidden_matchers_total
            )
        if metric.personalization_total:
            components.append(
                metric.personalization_satisfied / metric.personalization_total
            )
        expected_quality = round(sum(components) / len(components), 12)
        if abs(metric.deterministic_quality_score - expected_quality) > 1e-12:
            raise ValueError("deterministic quality score does not match checks")


class ScoreArtifactRecord(FrozenModel):
    run_id: UUID
    execution_order: PositiveInt
    path: ArtifactPath
    run_checksum: Sha256
    score_checksum: Sha256
    byte_size: PositiveInt

    @model_validator(mode="after")
    def path_must_match_run_identity(self) -> Self:
        if self.path != f"records/{self.run_id}.json":
            raise ValueError("score artifact path does not match run identity")
        return self


class ScoreArtifactManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    scorer_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    dataset_checksum: Sha256
    plan_checksum: Sha256
    execution_manifest_checksum: Sha256
    score_set_checksum: Sha256
    run_count: PositiveInt
    records: Annotated[
        tuple[ScoreArtifactRecord, ...],
        Field(min_length=1, max_length=10_000),
    ]

    @model_validator(mode="after")
    def records_must_be_unique_complete_and_ordered(self) -> Self:
        if len(self.records) != self.run_count:
            raise ValueError("score artifact count does not match run_count")
        run_ids = [item.run_id for item in self.records]
        paths = [item.path for item in self.records]
        orders = [item.execution_order for item in self.records]
        if len(run_ids) != len(set(run_ids)) or len(paths) != len(set(paths)):
            raise ValueError("score artifacts contain duplicate identities")
        if orders != list(range(1, self.run_count + 1)):
            raise ValueError("score artifacts must use contiguous execution order")
        return self


class AnalysisConfig(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    bootstrap_seed: int = Field(ge=0, le=2**63 - 1)
    bootstrap_iterations: int = Field(default=10_000, ge=100, le=100_000)
    confidence_level: float = Field(default=0.95, ge=0.95, le=0.95)
    min_valid_relative_bootstrap_fraction: float = Field(
        default=0.95,
        ge=0.5,
        le=1,
    )


class FrozenArtifact(FrozenModel):
    path: FreezeArtifactPath
    byte_size: PositiveInt
    content_checksum: Sha256


class SourceSnapshot(FrozenModel):
    source_commit: GitCommit
    working_tree_clean: Literal[True]
    python_implementation: Literal["CPython"]
    python_version: SemVer
    lockfile_path: Literal["uv.lock"] = "uv.lock"
    lockfile_checksum: Sha256


class ProviderSnapshot(FrozenModel):
    provider: Slug
    model: str = Field(min_length=1, max_length=256)
    api_name: Slug
    sdk_name: Slug
    sdk_version: SemVer
    model_is_immutable_snapshot: bool


class PriceSnapshot(FrozenModel):
    provider: Slug
    model: str = Field(min_length=1, max_length=256)
    currency: CurrencyCode
    unit_tokens: Literal[1_000_000] = 1_000_000
    input_micros_per_unit: PositiveCurrencyMicros = Field(
        validation_alias=AliasChoices(
            "input_micros_per_unit",
            "input_usd_micros_per_unit",
        )
    )
    cached_input_micros_per_unit: PositiveCurrencyMicros | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "cached_input_micros_per_unit",
            "cached_input_usd_micros_per_unit",
        ),
    )
    output_micros_per_unit: PositiveCurrencyMicros = Field(
        validation_alias=AliasChoices(
            "output_micros_per_unit",
            "output_usd_micros_per_unit",
        )
    )
    source_url: HttpsUrl
    captured_at: AwareDatetime


def calculate_conservative_cost_micros(
    *,
    input_tokens: int,
    output_tokens: int,
    pricing: PriceSnapshot,
) -> int:
    """Round each uncached token component up to a whole USD micro."""

    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts cannot be negative")
    unit = pricing.unit_tokens
    input_cost = (input_tokens * pricing.input_micros_per_unit + unit - 1) // unit
    output_cost = (output_tokens * pricing.output_micros_per_unit + unit - 1) // unit
    return input_cost + output_cost


class CostBudget(FrozenModel):
    currency: CurrencyCode
    estimated_provider_requests: PositiveInt
    estimated_prompt_tokens: PositiveInt
    estimated_completion_tokens: PositiveInt
    estimated_cost_micros: PositiveCurrencyMicros = Field(
        validation_alias=AliasChoices(
            "estimated_cost_micros",
            "estimated_cost_usd_micros",
        )
    )
    hard_cost_limit_micros: PositiveCurrencyMicros = Field(
        validation_alias=AliasChoices(
            "hard_cost_limit_micros",
            "hard_cost_limit_usd_micros",
        )
    )

    @model_validator(mode="after")
    def hard_limit_must_cover_estimate(self) -> Self:
        if self.hard_cost_limit_micros < self.estimated_cost_micros:
            raise ValueError("hard cost limit cannot be below estimated cost")
        return self


class PilotEvidenceArtifact(FrozenModel):
    path: EvidenceArtifactPath
    byte_size: PositiveInt
    content_checksum: Sha256


class PilotEvidenceStatusCount(FrozenModel):
    status: RunStatus
    count: PositiveInt


class PilotEvidenceSeal(FrozenModel):
    """Content identity for one validated, externally retained Pilot journal."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    evidence_root_label: Slug
    freeze_manifest_checksum: Sha256
    execution_manifest_checksum: Sha256
    plan_checksum: Sha256
    run_count: PositiveInt
    attempt_count: PositiveInt
    status_counts: Annotated[
        tuple[PilotEvidenceStatusCount, ...],
        Field(min_length=1),
    ]
    files: Annotated[
        tuple[PilotEvidenceArtifact, ...],
        Field(min_length=1, max_length=10_000),
    ]
    total_bytes: PositiveInt
    seal_checksum: Sha256

    @model_validator(mode="after")
    def evidence_identity_must_be_canonical(self) -> Self:
        statuses = [item.status for item in self.status_counts]
        if statuses != sorted(statuses) or len(statuses) != len(set(statuses)):
            raise ValueError("Pilot evidence statuses must be unique and sorted")
        if sum(item.count for item in self.status_counts) != self.run_count:
            raise ValueError("Pilot evidence status counts must cover every run")
        paths = [item.path for item in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("Pilot evidence files must be unique and sorted")
        if sum(item.byte_size for item in self.files) != self.total_bytes:
            raise ValueError("Pilot evidence byte total does not match file inventory")
        return self


class PilotEvidenceRef(FrozenModel):
    experiment_id: Slug
    freeze_manifest_path: FreezeArtifactPath
    freeze_manifest_checksum: Sha256
    report_path: FreezeArtifactPath
    report_checksum: Sha256
    evidence_seal_path: FreezeArtifactPath
    evidence_seal_checksum: Sha256


class BlindReviewItem(FrozenModel):
    """Condition-free material delivered to a human reviewer."""

    schema_version: Literal["1.0"] = "1.0"
    review_item_id: UUID
    task_id: Slug
    scenario: ExperimentScenario
    instruction: str = Field(min_length=1, max_length=8_000)
    reader_profile: str = Field(min_length=1, max_length=1_000)
    run_status: RunStatus
    output_text: str | None = Field(default=None, max_length=256_000)
    structured_output: JsonObject | None = None
    required_facts: Annotated[tuple[FactDefinition, ...], Field(min_length=1)]
    rubric: RubricDefinition

    @model_validator(mode="after")
    def review_material_must_match_task_and_status(self) -> Self:
        if self.rubric.task_id != self.task_id:
            raise ValueError("blind review rubric does not match task")
        fact_ids = {fact.fact_id for fact in self.required_facts}
        if fact_ids != set(self.rubric.required_fact_ids):
            raise ValueError("blind review facts do not match rubric")
        if self.run_status is RunStatus.SUCCEEDED:
            if self.output_text is None or self.structured_output is None:
                raise ValueError("successful blind review item requires model output")
        elif self.output_text is not None or self.structured_output is not None:
            raise ValueError("failed blind review item cannot contain model output")
        return self


class BlindReviewMappingRecord(FrozenModel):
    review_item_id: UUID
    review_item_checksum: Sha256
    run_id: UUID
    run_checksum: Sha256
    condition: ExperimentCondition
    task_id: Slug
    repetition: int = Field(ge=1, le=20)
    execution_order: PositiveInt


class BlindReviewMapping(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    execution_manifest_checksum: Sha256
    plan_checksum: Sha256
    records: Annotated[
        tuple[BlindReviewMappingRecord, ...],
        Field(min_length=1, max_length=10_000),
    ]
    mapping_checksum: Sha256

    @model_validator(mode="after")
    def mapping_must_cover_unique_plan_order(self) -> Self:
        review_ids = [item.review_item_id for item in self.records]
        run_ids = [item.run_id for item in self.records]
        orders = [item.execution_order for item in self.records]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("blind review IDs must be unique")
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("blind review run IDs must be unique")
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("blind review mapping must use complete plan order")
        return self


class BlindReviewArtifact(FrozenModel):
    review_item_id: UUID
    path: ArtifactPath
    byte_size: PositiveInt
    content_checksum: Sha256


class BlindReviewPackageManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    execution_manifest_checksum: Sha256
    plan_checksum: Sha256
    mapping_checksum: Sha256
    item_count: PositiveInt
    files: Annotated[
        tuple[BlindReviewArtifact, ...],
        Field(min_length=1, max_length=10_000),
    ]
    package_checksum: Sha256

    @model_validator(mode="after")
    def package_files_must_be_unique_and_complete(self) -> Self:
        ids = [item.review_item_id for item in self.files]
        paths = [item.path for item in self.files]
        if self.item_count != len(self.files):
            raise ValueError("blind review item count does not match files")
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("blind review files must be unique")
        if paths != sorted(paths):
            raise ValueError("blind review files must be sorted")
        return self


class FreezeCandidateSpec(FrozenModel):
    """Reviewed inputs from which machine-observed freeze evidence is derived."""

    schema_version: Literal["1.1"] = "1.1"
    purpose: ExperimentPurpose
    freeze_id: Slug
    experiment_id: Slug
    definition_checksum: Sha256
    execution_manifest: ExecutionManifest
    analysis_config: AnalysisConfig
    provider: ProviderSnapshot
    pricing: PriceSnapshot
    cost_budget: CostBudget
    pilot_evidence: PilotEvidenceRef | None = None
    inventory_paths: Annotated[
        tuple[FreezeArtifactPath, ...],
        Field(min_length=1, max_length=1_000),
    ]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def candidate_sources_must_be_consistent(self) -> Self:
        generation = self.execution_manifest.generation
        if (
            self.experiment_id != self.execution_manifest.experiment_id
            or self.provider.provider != generation.provider
            or self.provider.model != generation.model
            or self.provider.sdk_version != generation.sdk_version
            or self.pricing.provider != self.provider.provider
            or self.pricing.model != self.provider.model
            or self.pricing.currency != self.cost_budget.currency
        ):
            raise ValueError("freeze candidate source identities do not match")
        paths = list(self.inventory_paths)
        if len(paths) != len(set(paths)) or paths != sorted(paths):
            raise ValueError("freeze candidate inventory must be unique and sorted")
        if "uv.lock" not in paths:
            raise ValueError("freeze candidate inventory must include uv.lock")
        if self.purpose is ExperimentPurpose.PILOT:
            if self.pilot_evidence is not None:
                raise ValueError("pilot candidate cannot reference pilot evidence")
        elif self.pilot_evidence is None:
            raise ValueError("formal candidate requires pilot evidence")
        elif self.pilot_evidence.experiment_id == self.experiment_id:
            raise ValueError("pilot and formal experiment IDs must differ")
        elif not {
            self.pilot_evidence.freeze_manifest_path,
            self.pilot_evidence.report_path,
            self.pilot_evidence.evidence_seal_path,
        }.issubset(paths):
            raise ValueError("formal candidate inventory must include Pilot evidence")
        return self


class FrozenExperimentManifest(FrozenModel):
    schema_version: Literal["1.1"] = "1.1"
    purpose: ExperimentPurpose
    freeze_id: Slug
    experiment_id: Slug
    definition_checksum: Sha256
    candidate_spec_path: FreezeArtifactPath
    execution_manifest: ExecutionManifest
    analysis_config: AnalysisConfig
    analysis_config_checksum: Sha256
    source: SourceSnapshot
    provider: ProviderSnapshot
    pricing: PriceSnapshot
    cost_budget: CostBudget
    pilot_evidence: PilotEvidenceRef | None = None
    files: Annotated[
        tuple[FrozenArtifact, ...],
        Field(min_length=1, max_length=1_000),
    ]
    created_at: AwareDatetime
    manifest_checksum: Sha256

    @model_validator(mode="after")
    def frozen_sources_must_be_consistent(self) -> Self:
        execution = self.execution_manifest
        generation = execution.generation
        if (
            self.experiment_id != execution.experiment_id
            or self.provider.provider != generation.provider
            or self.provider.model != generation.model
            or self.provider.sdk_version != generation.sdk_version
            or self.pricing.provider != self.provider.provider
            or self.pricing.model != self.provider.model
            or self.pricing.currency != self.cost_budget.currency
            or self.analysis_config_checksum != sha256_model(self.analysis_config)
        ):
            raise ValueError("frozen manifest source identities do not match")
        self._validate_file_inventory()
        self._validate_pilot_boundary()
        self._validate_budget()
        return self

    def _validate_file_inventory(self) -> None:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)) or paths != sorted(paths):
            raise ValueError("frozen files must be unique and sorted")
        lockfiles = [
            item for item in self.files if item.path == self.source.lockfile_path
        ]
        if (
            len(lockfiles) != 1
            or lockfiles[0].content_checksum != self.source.lockfile_checksum
        ):
            raise ValueError("frozen files do not bind the declared lockfile")
        if self.candidate_spec_path not in paths:
            raise ValueError("frozen files do not bind the candidate spec")

    def _validate_pilot_boundary(self) -> None:
        if self.purpose is ExperimentPurpose.PILOT:
            if self.pilot_evidence is not None:
                raise ValueError("pilot manifest cannot reference pilot evidence")
            return
        if self.pilot_evidence is None:
            raise ValueError("formal manifest requires pilot evidence")
        if self.pilot_evidence.experiment_id == self.experiment_id:
            raise ValueError("pilot and formal experiment IDs must differ")

    def _validate_budget(self) -> None:
        limits = self.execution_manifest.limits
        budget = self.cost_budget
        if (
            budget.estimated_provider_requests > limits.max_provider_requests
            or budget.estimated_prompt_tokens > limits.max_prompt_tokens
            or budget.estimated_completion_tokens > limits.max_completion_tokens
        ):
            raise ValueError("estimated usage exceeds technical execution limits")
        expected_cost = calculate_conservative_cost_micros(
            input_tokens=budget.estimated_prompt_tokens,
            output_tokens=budget.estimated_completion_tokens,
            pricing=self.pricing,
        )
        if budget.estimated_cost_micros != expected_cost:
            raise ValueError("estimated cost does not match tokens and pricing")
        token_ceiling_cost = calculate_conservative_cost_micros(
            input_tokens=limits.max_prompt_tokens,
            output_tokens=limits.max_completion_tokens,
            pricing=self.pricing,
        )
        if budget.hard_cost_limit_micros > token_ceiling_cost:
            raise ValueError("hard cost limit exceeds token-bound cost ceiling")


class TaskConditionAggregate(FrozenModel):
    task_id: Slug
    domain_id: Slug
    scenario: ExperimentScenario
    condition: ExperimentCondition
    population: AnalysisPopulation
    planned_runs: PositiveInt
    included_runs: int = Field(ge=0)
    succeeded_runs: int = Field(ge=0)
    schema_passes: int = Field(ge=0)
    required_facts_total: int = Field(ge=0)
    required_facts_covered: int = Field(ge=0)
    personalization_total: int = Field(ge=0)
    personalization_satisfied: int = Field(ge=0)
    schema_pass_rate: float | None = Field(default=None, ge=0, le=1)
    omission_rate: float | None = Field(default=None, ge=0, le=1)
    adaptation_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def counts_and_rates_must_be_consistent(self) -> Self:
        if self.succeeded_runs > self.planned_runs:
            raise ValueError("succeeded runs cannot exceed planned runs")
        expected_included = (
            self.planned_runs
            if self.population is AnalysisPopulation.INTENTION_TO_TREAT
            else self.succeeded_runs
        )
        if self.included_runs != expected_included:
            raise ValueError("included runs do not match analysis population")
        if self.schema_passes > self.included_runs:
            raise ValueError("schema passes cannot exceed included runs")
        if self.required_facts_covered > self.required_facts_total:
            raise ValueError("covered facts cannot exceed required facts")
        if self.personalization_satisfied > self.personalization_total:
            raise ValueError("satisfied constraints cannot exceed total constraints")
        expected_schema = (
            None
            if self.included_runs == 0
            else round(self.schema_passes / self.included_runs, 12)
        )
        expected_omission = (
            None
            if self.required_facts_total == 0
            else round(
                1 - self.required_facts_covered / self.required_facts_total,
                12,
            )
        )
        expected_adaptation = (
            None
            if self.personalization_total == 0
            else round(
                self.personalization_satisfied / self.personalization_total,
                12,
            )
        )
        if self.schema_pass_rate != expected_schema:
            raise ValueError("schema pass rate does not match counts")
        if self.omission_rate != expected_omission:
            raise ValueError("omission rate does not match counts")
        if self.adaptation_rate != expected_adaptation:
            raise ValueError("adaptation rate does not match counts")
        if self.included_runs == 0 and any(
            (
                self.required_facts_total,
                self.required_facts_covered,
                self.personalization_total,
                self.personalization_satisfied,
            )
        ):
            raise ValueError("empty aggregate cannot contain scoring counts")
        if self.scenario is ExperimentScenario.CONSISTENCY:
            if self.personalization_total or self.adaptation_rate is not None:
                raise ValueError("consistency aggregate cannot contain adaptation")
        elif self.included_runs and self.personalization_total == 0:
            raise ValueError("adaptation aggregate requires personalization evidence")
        return self


class ConfidenceInterval(FrozenModel):
    confidence_level: float = Field(default=0.95, ge=0.95, le=0.95)
    lower: float | None = None
    upper: float | None = None
    requested_replicates: PositiveInt
    valid_replicates: int = Field(ge=0)
    invalid_replicates: int = Field(ge=0)

    @model_validator(mode="after")
    def bounds_must_match_valid_replicates(self) -> Self:
        if self.valid_replicates + self.invalid_replicates != self.requested_replicates:
            raise ValueError("bootstrap replicate counts do not add up")
        if self.valid_replicates == 0:
            if self.lower is not None or self.upper is not None:
                raise ValueError("empty bootstrap interval cannot contain bounds")
        elif self.lower is None or self.upper is None:
            raise ValueError("valid bootstrap interval requires both bounds")
        elif self.lower > self.upper:
            raise ValueError("bootstrap interval lower bound exceeds upper bound")
        return self


class HypothesisResult(FrozenModel):
    hypothesis: HypothesisName
    population: AnalysisPopulation
    paired_task_count: int = Field(ge=0)
    effect_estimate: float | None = None
    confidence_interval: ConfidenceInterval
    absolute_difference: float | None = Field(default=None, ge=-1, le=1)
    absolute_difference_interval: ConfidenceInterval | None = None
    decision: HypothesisDecision

    @model_validator(mode="after")
    def result_shape_must_match_hypothesis_and_population(self) -> Self:
        if self.paired_task_count == 0 and self.effect_estimate is not None:
            raise ValueError("empty hypothesis result cannot contain an effect")
        if self.hypothesis is HypothesisName.H2_KNOWLEDGE_OMISSION:
            if self.paired_task_count and (
                self.absolute_difference is None
                or self.absolute_difference_interval is None
            ):
                raise ValueError("H2 requires an absolute omission difference")
        elif (
            self.absolute_difference is not None
            or self.absolute_difference_interval is not None
        ):
            raise ValueError("auxiliary absolute difference is reserved for H2")
        if self.population is AnalysisPopulation.SUCCEEDED_ONLY:
            if self.decision is not HypothesisDecision.NOT_EVALUATED:
                raise ValueError("succeeded-only sensitivity cannot decide hypotheses")
        elif self.decision is HypothesisDecision.NOT_EVALUATED:
            raise ValueError("primary ITT analysis requires a hypothesis decision")
        return self


class AnalysisSummary(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    dataset_checksum: Sha256
    definition_checksum: Sha256
    plan_checksum: Sha256
    score_set_checksum: Sha256
    task_count: PositiveInt
    repetitions: int = Field(ge=1, le=20)
    config: AnalysisConfig
    config_checksum: Sha256
    aggregates: Annotated[tuple[TaskConditionAggregate, ...], Field(min_length=1)]
    hypotheses: Annotated[tuple[HypothesisResult, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def analysis_evidence_must_be_unique_and_complete(self) -> Self:
        if self.config_checksum != sha256_model(self.config):
            raise ValueError("analysis config checksum does not match config")
        aggregate_keys = [
            (item.population, item.task_id, item.condition) for item in self.aggregates
        ]
        if len(aggregate_keys) != len(set(aggregate_keys)):
            raise ValueError("analysis aggregates contain duplicate coordinates")
        task_ids = {item.task_id for item in self.aggregates}
        if len(task_ids) != self.task_count:
            raise ValueError("analysis aggregate task count does not match declaration")
        expected_aggregates = {
            (population, task_id, condition)
            for population in AnalysisPopulation
            for task_id in task_ids
            for condition in ExperimentCondition
        }
        if set(aggregate_keys) != expected_aggregates:
            raise ValueError("analysis aggregate matrix is incomplete")
        if any(item.planned_runs != self.repetitions for item in self.aggregates):
            raise ValueError("analysis aggregate repetitions do not match declaration")
        metadata_by_task = {
            task_id: {
                (item.domain_id, item.scenario)
                for item in self.aggregates
                if item.task_id == task_id
            }
            for task_id in task_ids
        }
        if any(len(metadata) != 1 for metadata in metadata_by_task.values()):
            raise ValueError("analysis aggregate task metadata is inconsistent")
        hypothesis_keys = [
            (item.population, item.hypothesis) for item in self.hypotheses
        ]
        expected_hypotheses = {
            (population, hypothesis)
            for population in AnalysisPopulation
            for hypothesis in HypothesisName
        }
        if set(hypothesis_keys) != expected_hypotheses or len(hypothesis_keys) != len(
            expected_hypotheses
        ):
            raise ValueError("analysis must contain both populations for H1, H2 and H4")
        hypotheses_by_key = {
            (item.population, item.hypothesis): item for item in self.hypotheses
        }
        adaptation_task_count = sum(
            next(iter(metadata))[1] is ExperimentScenario.ADAPTATION
            for metadata in metadata_by_task.values()
        )
        if any(
            hypotheses_by_key[
                (AnalysisPopulation.INTENTION_TO_TREAT, hypothesis)
            ].paired_task_count
            != self.task_count
            for hypothesis in (
                HypothesisName.H1_SCHEMA_CONSISTENCY,
                HypothesisName.H2_KNOWLEDGE_OMISSION,
            )
        ):
            raise ValueError("ITT H1 and H2 must include every frozen task")
        if (
            hypotheses_by_key[
                (
                    AnalysisPopulation.INTENTION_TO_TREAT,
                    HypothesisName.H4_PERSONALIZATION,
                )
            ].paired_task_count
            != adaptation_task_count
        ):
            raise ValueError("ITT H4 must include every adaptation task")
        return self


class AnalysisArtifactFile(FrozenModel):
    path: ArtifactPath
    media_type: Literal["application/json", "text/csv", "text/markdown"]
    content_checksum: Sha256
    byte_size: PositiveInt


class AnalysisArtifactManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    analysis_checksum: Sha256
    files: Annotated[
        tuple[AnalysisArtifactFile, ...],
        Field(min_length=3, max_length=3),
    ]

    @model_validator(mode="after")
    def files_must_match_canonical_report_package(self) -> Self:
        identities = tuple((item.path, item.media_type) for item in self.files)
        expected = (
            ("summary.json", "application/json"),
            ("metrics.csv", "text/csv"),
            ("report.md", "text/markdown"),
        )
        if identities != expected:
            raise ValueError(
                "analysis artifact files must use canonical order and types"
            )
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
