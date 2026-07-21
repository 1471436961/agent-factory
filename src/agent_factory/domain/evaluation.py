"""Immutable M2 evaluation suites, submissions, outcomes, and reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Self
from uuid import UUID

import regex
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    Field,
    PositiveInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_factory.domain.common import (
    Actor,
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    canonical_json_bytes,
)
from agent_factory.domain.enums import (
    EvaluationDecision,
    ReviewDecision,
    RuleKind,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef

_SLUG_ADAPTER = TypeAdapter(Slug)
_MAX_TERMS = 32
_MAX_TERM_LENGTH = 64
_MAX_REGEX_LENGTH = 512
_MAX_EVIDENCE_BYTES = 4_096


def _require_parameter_keys(
    parameters: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    keys = set(parameters)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"missing rule parameters: {sorted(missing)}")
    if unknown:
        raise ValueError(f"unknown rule parameters: {sorted(unknown)}")


def _validate_terms(parameters: Mapping[str, object]) -> None:
    _require_parameter_keys(
        parameters,
        required=frozenset({"terms"}),
        optional=frozenset({"case_sensitive"}),
    )
    terms = parameters["terms"]
    if not isinstance(terms, tuple) or not 1 <= len(terms) <= _MAX_TERMS:
        raise ValueError(f"terms must contain between 1 and {_MAX_TERMS} items")
    if any(
        not isinstance(term, str) or not term or len(term) > _MAX_TERM_LENGTH
        for term in terms
    ):
        raise ValueError(
            f"terms must be non-empty strings up to {_MAX_TERM_LENGTH} characters"
        )
    if len(terms) != len(set(terms)):
        raise ValueError("terms must be unique")
    case_sensitive = parameters.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive must be a boolean")


def _validate_rule_parameters(
    kind: RuleKind,
    parameters: Mapping[str, object],
) -> None:
    if kind in {RuleKind.REQUIRED_TERMS, RuleKind.FORBIDDEN_TERMS}:
        _validate_terms(parameters)
        return
    if kind is RuleKind.JSON_SCHEMA:
        _require_parameter_keys(parameters, required=frozenset({"schema"}))
        schema = parameters["schema"]
        if not isinstance(schema, Mapping):
            raise ValueError("schema must be a JSON object")
        try:
            Draft202012Validator.check_schema(FrozenJsonObject(schema).to_builtin())
        except SchemaError as exc:
            raise ValueError(
                f"schema is not valid Draft 2020-12: {exc.message}"
            ) from exc
        return
    if kind is RuleKind.REGEX:
        _require_parameter_keys(parameters, required=frozenset({"pattern"}))
        pattern = parameters["pattern"]
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= _MAX_REGEX_LENGTH:
            raise ValueError(
                f"pattern must contain between 1 and {_MAX_REGEX_LENGTH} characters"
            )
        try:
            regex.compile(pattern, regex.VERSION1)
        except regex.error as exc:
            raise ValueError(
                f"pattern is not a valid regular expression: {exc}"
            ) from exc
        return
    if kind is RuleKind.MAX_LENGTH:
        _require_parameter_keys(parameters, required=frozenset({"max_chars"}))
        maximum = parameters["max_chars"]
        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 1 <= maximum <= 64_000
        ):
            raise ValueError("max_chars must be an integer between 1 and 64000")
        return
    if kind is RuleKind.TOOL_CALLED:
        _require_parameter_keys(parameters, required=frozenset({"tool_name"}))
        try:
            _SLUG_ADAPTER.validate_python(parameters["tool_name"])
        except ValidationError as exc:
            raise ValueError("tool_name must be a valid slug") from exc
        return
    raise ValueError(f"unsupported rule kind: {kind}")


class EvaluationRule(FrozenModel):
    rule_id: Slug
    kind: RuleKind
    hard: bool = True
    parameters: JsonObject
    weight: float = Field(default=1.0, gt=0, le=100)

    @model_validator(mode="after")
    def parameters_must_match_kind(self) -> Self:
        _validate_rule_parameters(self.kind, self.parameters)
        return self


class EvaluationCase(FrozenModel):
    case_id: Slug
    input: str = Field(min_length=1, max_length=64_000)
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)


class EvaluationSuiteDraft(FrozenModel):
    suite_id: Slug
    version: SemVer
    rules: Annotated[tuple[EvaluationRule, ...], Field(min_length=1)]
    cases: Annotated[tuple[EvaluationCase, ...], Field(min_length=1)]
    minimum_soft_score: float = Field(default=0.8, ge=0, le=1)
    require_manual_review: bool = False

    @field_validator("rules")
    @classmethod
    def rules_must_be_unique_and_sorted(
        cls,
        value: tuple[EvaluationRule, ...],
    ) -> tuple[EvaluationRule, ...]:
        ids = [item.rule_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation rule ids must be unique")
        return tuple(sorted(value, key=lambda item: item.rule_id))

    @field_validator("cases")
    @classmethod
    def cases_must_be_unique_and_sorted(
        cls,
        value: tuple[EvaluationCase, ...],
    ) -> tuple[EvaluationCase, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case ids must be unique")
        return tuple(sorted(value, key=lambda item: item.case_id))


class EvaluationSuite(EvaluationSuiteDraft):
    checksum: Sha256
    created_at: AwareDatetime
    created_by: Actor


class SubmittedCaseResult(FrozenModel):
    case_id: Slug
    output_text: str = Field(max_length=64_000)
    structured_output: JsonObject | None = None
    called_tools: tuple[Slug, ...] = ()
    artifact_uri: AnyHttpUrl | None = None

    @field_validator("called_tools")
    @classmethod
    def called_tools_must_be_unique_and_sorted(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("called_tools contains duplicate names")
        return tuple(sorted(value))


class EvaluationSubmission(FrozenModel):
    instance_id: UUID
    instance_revision: PositiveInt
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    case_results: Annotated[
        tuple[SubmittedCaseResult, ...],
        Field(min_length=1),
    ]

    @field_validator("case_results")
    @classmethod
    def case_results_must_be_unique_and_sorted(
        cls,
        value: tuple[SubmittedCaseResult, ...],
    ) -> tuple[SubmittedCaseResult, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("submitted case ids must be unique")
        return tuple(sorted(value, key=lambda item: item.case_id))


class CaseResultRef(FrozenModel):
    case_id: Slug
    checksum: Sha256
    artifact_uri: AnyHttpUrl | None = None


class RuleResult(FrozenModel):
    rule_id: Slug
    case_id: Slug
    passed: bool
    score: float = Field(ge=0, le=1)
    evidence: JsonObject = Field(default_factory=FrozenJsonObject)

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_bounded(
        cls,
        value: Mapping[str, object],
    ) -> Mapping[str, object]:
        if len(canonical_json_bytes(value)) > _MAX_EVIDENCE_BYTES:
            raise ValueError(
                f"rule evidence must not exceed {_MAX_EVIDENCE_BYTES} bytes"
            )
        return value


class JudgeSignal(FrozenModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    rubric_version: SemVer
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=4_000)


class EvaluationOutcome(FrozenModel):
    case_results: Annotated[tuple[CaseResultRef, ...], Field(min_length=1)]
    rule_results: Annotated[tuple[RuleResult, ...], Field(min_length=1)]
    hard_rules_passed: bool
    soft_score: float = Field(ge=0, le=1)
    decision: EvaluationDecision

    @field_validator("case_results")
    @classmethod
    def case_result_refs_must_be_sorted(
        cls,
        value: tuple[CaseResultRef, ...],
    ) -> tuple[CaseResultRef, ...]:
        return tuple(sorted(value, key=lambda item: item.case_id))

    @field_validator("rule_results")
    @classmethod
    def rule_results_must_be_sorted(
        cls,
        value: tuple[RuleResult, ...],
    ) -> tuple[RuleResult, ...]:
        return tuple(sorted(value, key=lambda item: (item.case_id, item.rule_id)))

    @model_validator(mode="after")
    def outcome_must_be_internally_consistent(self) -> Self:
        if (
            self.decision
            in {EvaluationDecision.PASS, EvaluationDecision.REVIEW_REQUIRED}
            and not self.hard_rules_passed
        ):
            raise ValueError("non-failing decisions require all hard rules to pass")
        case_ids = [item.case_id for item in self.case_results]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case result references must be unique")
        result_keys = [(item.rule_id, item.case_id) for item in self.rule_results]
        if len(result_keys) != len(set(result_keys)):
            raise ValueError("rule results must be unique per rule and case")
        unknown_case_ids = {item.case_id for item in self.rule_results} - set(case_ids)
        if unknown_case_ids:
            raise ValueError(
                f"rule results reference unknown cases: {sorted(unknown_case_ids)}"
            )
        return self


class EvaluationReport(EvaluationOutcome):
    report_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    skill_tree: SkillTreeRef
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    judge_signals: tuple[JudgeSignal, ...] = ()
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def report_must_be_internally_consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class EvaluationReview(FrozenModel):
    review_id: UUID
    report_id: UUID
    reviewer: Actor
    decision: ReviewDecision
    comment: str = Field(default="", max_length=2_000)
    reviewed_at: AwareDatetime
