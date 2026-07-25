"""Validation tests for immutable M5 experiment contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import canonical_json_bytes, sha256_model
from agent_factory.domain.enums import AuditEventType
from experiments.contracts import (
    AttemptStatus,
    AuditStepResult,
    AuditVerificationRecord,
    BuildSession,
    ExecutionPlan,
    ExecutionPlanItem,
    ExperimentCondition,
    ExperimentDefinition,
    ExperimentRun,
    ExperimentScenario,
    FactCheck,
    FactDefinition,
    ForbiddenMatcherCheck,
    GenerationConfig,
    HypothesisThresholds,
    KnowledgeFixture,
    MatcherKind,
    MatchExpectation,
    MetricRecord,
    PersonalizationCheck,
    PersonalizationConstraint,
    RubricDefinition,
    RunAttempt,
    RunScoreRecord,
    RunStatus,
    SchemaViolation,
    TaskBundle,
    TextMatcher,
)
from experiments.loader import LoadedExperimentDataset

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("20000000-0000-0000-0000-000000000001")
INSTANCE_ID = UUID("30000000-0000-0000-0000-000000000001")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _generation() -> GenerationConfig:
    return GenerationConfig(
        provider="local-fake",
        model="fake-model-v1",
        sdk_version="1.0.0",
        temperature=0,
        max_output_tokens=512,
        request_timeout_seconds=30,
    )


def _success_attempt() -> RunAttempt:
    return RunAttempt(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        provider_request_id="provider-request-1",
        response={"output": {"title": "Example"}},
        output_text='{"title":"Example"}',
        structured_output={"title": "Example"},
        prompt_tokens=10,
        completion_tokens=5,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )


def _run_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "experiment_id": "writer-validation-v1",
        "manifest_checksum": SHA_B,
        "plan_checksum": SHA_A,
        "condition": ExperimentCondition.MANUAL,
        "task_id": "nexora-beginner-guide",
        "repetition": 1,
        "execution_order": 1,
        "generation": _generation(),
        "invocation": {"instructions": "Write.", "input": "Task."},
        "prompt_hash": SHA_A,
        "knowledge_checksum": SHA_B,
        "status": RunStatus.SUCCEEDED,
        "attempts": [_success_attempt()],
        "output_text": '{"title":"Example"}',
        "structured_output": {"title": "Example"},
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
    }


def _successful_metric(
    *,
    quality: float = 0.833333333333,
    personalization_total: int = 0,
    personalization_satisfied: int = 0,
) -> MetricRecord:
    return MetricRecord(
        run_id=RUN_ID,
        run_status=RunStatus.SUCCEEDED,
        schema_passed=True,
        required_facts_total=2,
        required_facts_covered=1,
        forbidden_matchers_total=1,
        forbidden_matchers_violated=0,
        personalization_total=personalization_total,
        personalization_satisfied=personalization_satisfied,
        deterministic_quality_score=quality,
    )


def _score_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "run_checksum": SHA_A,
        "experiment_id": "writer-validation-v1",
        "plan_checksum": SHA_A,
        "condition": ExperimentCondition.MANUAL,
        "task_id": "nexora-integration-reference",
        "scenario": ExperimentScenario.CONSISTENCY,
        "repetition": 1,
        "execution_order": 1,
        "run_status": RunStatus.SUCCEEDED,
        "rubric_id": "nexora-integration-reference-rubric",
        "rubric_checksum": SHA_B,
        "schema_passed": True,
        "fact_checks": [
            FactCheck(fact_id="ingestion-endpoint", covered=True, matched_by_index=0),
            FactCheck(fact_id="batch-limit", covered=False),
        ],
        "forbidden_checks": [ForbiddenMatcherCheck(matcher_index=0, violated=False)],
        "metric": _successful_metric(),
    }


def test_matcher_rejects_invalid_regex_and_is_frozen() -> None:
    with pytest.raises(ValidationError, match="must compile"):
        TextMatcher(kind=MatcherKind.REGEX, pattern="(")

    matcher = TextMatcher(kind=MatcherKind.EXACT, pattern="current")
    with pytest.raises(ValidationError, match="frozen"):
        matcher.pattern = "changed"
    with pytest.raises(ValidationError, match="extra"):
        TextMatcher.model_validate(
            {"kind": "exact", "pattern": "current", "unknown": True}
        )


def test_personalization_constraint_accepts_json_field_names() -> None:
    constraint = PersonalizationConstraint(
        constraint_id="plain-language",
        description="Use a plain-language opening.",
        expectation=MatchExpectation.PRESENT,
        matcher=TextMatcher(kind=MatcherKind.EXACT, pattern="In plain language"),
        target_field="next_action",
    )

    assert constraint.target_field == "next_action"


def test_definition_requires_canonical_complete_collections(
    experiment_root: ExperimentDefinition,
) -> None:
    payload = experiment_root.model_dump(mode="python")
    payload["domain_ids"] = tuple(reversed(experiment_root.domain_ids))
    with pytest.raises(ValidationError, match="canonical sorted order"):
        ExperimentDefinition.model_validate(payload)

    payload = experiment_root.model_dump(mode="python")
    payload["conditions"] = [ExperimentCondition.MANUAL]
    with pytest.raises(ValidationError, match="MANUAL and FACTORY"):
        ExperimentDefinition.model_validate(payload)

    payload = experiment_root.model_dump(mode="python")
    payload["rubric_files"] = experiment_root.rubric_files[:-1]
    with pytest.raises(ValidationError, match="each domain requires"):
        ExperimentDefinition.model_validate(payload)


def test_thresholds_require_a_real_inconclusive_band() -> None:
    with pytest.raises(ValidationError, match="below support threshold"):
        HypothesisThresholds(
            h1_support_min_absolute_difference=0.05,
            h1_not_support_below=0.05,
            h2_support_min_relative_reduction=0.2,
            h4_noninferiority_margin=-0.05,
        )


def test_knowledge_task_and_rubric_reject_duplicate_or_cross_domain_items(
    dataset: LoadedExperimentDataset,
) -> None:
    fixture = dataset.knowledge[0]
    payload = fixture.model_dump(mode="python")
    payload["facts"] = [fixture.facts[0], fixture.facts[0]]
    with pytest.raises(ValidationError, match="duplicate fact_id"):
        KnowledgeFixture.model_validate(payload)

    task = dataset.tasks[0]
    task_payload = task.model_dump(mode="python", exclude={"output_schema"})
    with pytest.raises(ValidationError, match="must match its bundle"):
        TaskBundle.model_validate(
            {
                "domain_id": "different-domain",
                "output_schema": task.output_schema,
                "tasks": [task_payload],
            }
        )

    rubric = dataset.rubrics[0]
    rubric_payload = rubric.model_dump(mode="python")
    rubric_payload["required_fact_ids"] = [
        rubric.required_fact_ids[0],
        rubric.required_fact_ids[0],
    ]
    with pytest.raises(ValidationError, match="required_fact_ids contains duplicates"):
        RubricDefinition.model_validate(rubric_payload)

    fact_payload = fixture.facts[0].model_dump(mode="python")
    fact_payload["accepted_matchers"] = [
        fixture.facts[0].accepted_matchers[0],
        fixture.facts[0].accepted_matchers[0],
    ]
    with pytest.raises(ValidationError, match="accepted_matchers contains duplicates"):
        FactDefinition.model_validate(fact_payload)


def test_model_checksum_is_independent_of_mapping_key_order() -> None:
    left = TextMatcher.model_validate(
        {"kind": "exact", "pattern": "value", "case_sensitive": False}
    )
    right = TextMatcher.model_validate(
        {"case_sensitive": False, "pattern": "value", "kind": "exact"}
    )

    assert canonical_json_bytes(left.model_dump(mode="json")) == (
        canonical_json_bytes(right.model_dump(mode="json"))
    )
    assert sha256_model(left) == sha256_model(right)


def test_execution_plan_rejects_gaps_and_duplicate_coordinates() -> None:
    first = ExecutionPlanItem(
        run_id=RUN_ID,
        condition=ExperimentCondition.MANUAL,
        task_id="nexora-beginner-guide",
        repetition=1,
        execution_order=1,
    )
    gap = first.model_copy(
        update={
            "run_id": UUID("10000000-0000-0000-0000-000000000002"),
            "condition": ExperimentCondition.FACTORY,
            "execution_order": 3,
        }
    )
    with pytest.raises(ValidationError, match="contiguous"):
        ExecutionPlan(
            experiment_id="writer-validation-v1",
            definition_checksum=SHA_A,
            randomization_seed=1,
            items=(first, gap),
            plan_checksum=SHA_B,
        )

    duplicate = first.model_copy(
        update={
            "run_id": UUID("10000000-0000-0000-0000-000000000003"),
            "execution_order": 2,
        }
    )
    with pytest.raises(ValidationError, match="duplicate run coordinates"):
        ExecutionPlan(
            experiment_id="writer-validation-v1",
            definition_checksum=SHA_A,
            randomization_seed=1,
            items=(first, duplicate),
            plan_checksum=SHA_B,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "attempt_number": 1,
            "status": AttemptStatus.SUCCEEDED,
            "error_code": "PROVIDER_FAILED",
            "started_at": NOW,
            "completed_at": NOW,
        },
        {
            "attempt_number": 1,
            "status": AttemptStatus.PROVIDER_FAILED,
            "response": {"unexpected": True},
            "started_at": NOW,
            "completed_at": NOW,
        },
        {
            "attempt_number": 1,
            "status": AttemptStatus.TIMED_OUT,
            "error_code": "TIMED_OUT",
            "started_at": NOW,
            "completed_at": NOW - timedelta(seconds=1),
        },
    ],
)
def test_attempt_rejects_inconsistent_terminal_evidence(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RunAttempt.model_validate(payload)


def test_run_requires_status_to_match_attempts_and_output() -> None:
    run = ExperimentRun.model_validate(_run_payload())
    assert run.status is RunStatus.SUCCEEDED

    failed = _run_payload()
    failed["status"] = RunStatus.PROVIDER_FAILED
    with pytest.raises(ValidationError, match="run status must match"):
        ExperimentRun.model_validate(failed)

    budget = _run_payload()
    budget.update(
        {
            "status": RunStatus.BUDGET_STOPPED,
            "attempts": [],
            "output_text": None,
            "structured_output": None,
        }
    )
    assert ExperimentRun.model_validate(budget).attempts == ()

    replaced = _run_payload()
    replaced["output_text"] = '{"title":"Replaced"}'
    with pytest.raises(ValidationError, match="must match its final attempt"):
        ExperimentRun.model_validate(replaced)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"attempts": []}, "requires at least one attempt"),
        (
            {"attempts": [_success_attempt().model_copy(update={"attempt_number": 2})]},
            "contiguous from one",
        ),
        ({"structured_output": None}, "requires final structured output"),
        (
            {
                "status": RunStatus.TIMED_OUT,
                "attempts": [
                    RunAttempt(
                        attempt_number=1,
                        status=AttemptStatus.TIMED_OUT,
                        error_code="TIMED_OUT",
                        started_at=NOW,
                        completed_at=NOW,
                    )
                ],
            },
            "failed run cannot contain final output",
        ),
        (
            {
                "status": RunStatus.BUDGET_STOPPED,
                "attempts": [_success_attempt()],
            },
            "cannot contain provider attempts",
        ),
        (
            {
                "status": RunStatus.BUDGET_STOPPED,
                "attempts": [],
                "output_text": None,
            },
            "cannot contain final output",
        ),
    ],
)
def test_run_rejects_incomplete_or_contradictory_artifacts(
    changes: dict[str, object],
    message: str,
) -> None:
    payload = _run_payload()
    payload.update(changes)
    with pytest.raises(ValidationError, match=message):
        ExperimentRun.model_validate(payload)


def test_metric_counts_cannot_exceed_denominators() -> None:
    with pytest.raises(ValidationError, match="covered facts"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.SUCCEEDED,
            schema_passed=True,
            required_facts_total=2,
            required_facts_covered=3,
            forbidden_matchers_total=1,
            forbidden_matchers_violated=0,
            personalization_total=0,
            personalization_satisfied=0,
            deterministic_quality_score=1,
        )

    failed = MetricRecord(run_id=RUN_ID, run_status=RunStatus.TIMED_OUT)
    assert failed.schema_passed is None
    with pytest.raises(ValidationError, match="failed run"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.TIMED_OUT,
            schema_passed=False,
        )
    with pytest.raises(ValidationError, match="failed run"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.TIMED_OUT,
            human_quality_score=3,
        )

    with pytest.raises(ValidationError, match="complete deterministic scores"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.SUCCEEDED,
            schema_passed=True,
        )
    with pytest.raises(ValidationError, match="satisfied constraints"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.SUCCEEDED,
            schema_passed=True,
            required_facts_total=2,
            required_facts_covered=2,
            forbidden_matchers_total=1,
            forbidden_matchers_violated=0,
            personalization_total=1,
            personalization_satisfied=2,
            deterministic_quality_score=1,
        )

    with pytest.raises(ValidationError, match="violated forbidden"):
        MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.SUCCEEDED,
            schema_passed=True,
            required_facts_total=2,
            required_facts_covered=2,
            forbidden_matchers_total=1,
            forbidden_matchers_violated=2,
            personalization_total=0,
            personalization_satisfied=0,
            deterministic_quality_score=1,
        )


def test_scoring_checks_reject_self_contradictory_evidence() -> None:
    with pytest.raises(ValidationError, match="coverage must match"):
        FactCheck(fact_id="batch-limit", covered=True)

    with pytest.raises(ValidationError, match="contradicts expectation"):
        PersonalizationCheck(
            constraint_id="risk-label",
            expectation=MatchExpectation.ABSENT,
            matcher_matched=True,
            satisfied=True,
        )


def test_run_score_binds_successful_check_evidence_to_metric() -> None:
    score = RunScoreRecord.model_validate(_score_payload())

    assert score.metric.required_facts_covered == 1
    assert score.metric.forbidden_matchers_violated == 0
    assert score.schema_violations == ()


def test_successful_run_can_record_schema_failure_as_scoring_evidence() -> None:
    payload = _score_payload()
    payload.update(
        {
            "schema_passed": False,
            "schema_violations": [
                SchemaViolation(
                    instance_path="$.summary",
                    schema_path="$.properties.summary.minLength",
                    validator="minLength",
                )
            ],
            "metric": _successful_metric(quality=0.5).model_copy(
                update={"schema_passed": False}
            ),
        }
    )

    score = RunScoreRecord.model_validate(payload)

    assert score.run_status is RunStatus.SUCCEEDED
    assert score.schema_passed is False
    assert len(score.schema_violations) == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"metric": _successful_metric().model_copy(update={"run_id": INSTANCE_ID})},
            "references another run",
        ),
        ({"schema_passed": False}, "contradicts violation evidence"),
        (
            {
                "schema_violations": [
                    SchemaViolation(
                        instance_path="$.summary",
                        schema_path="$.properties.summary.minLength",
                        validator="minLength",
                    ),
                    SchemaViolation(
                        instance_path="$.summary",
                        schema_path="$.properties.summary.minLength",
                        validator="minLength",
                    ),
                ]
            },
            "unique and sorted",
        ),
        (
            {
                "forbidden_checks": [
                    ForbiddenMatcherCheck(matcher_index=1, violated=False)
                ]
            },
            "indices must be contiguous",
        ),
        (
            {
                "metric": _successful_metric().model_copy(
                    update={"required_facts_covered": 2}
                )
            },
            "counts do not match",
        ),
        (
            {
                "metric": _successful_metric().model_copy(
                    update={"deterministic_quality_score": 0.9}
                )
            },
            "quality score does not match",
        ),
    ],
)
def test_run_score_rejects_cross_artifact_contradictions(
    changes: dict[str, object],
    message: str,
) -> None:
    payload = _score_payload()
    payload.update(changes)
    with pytest.raises(ValidationError, match=message):
        RunScoreRecord.model_validate(payload)


def test_run_score_enforces_scenario_and_failed_run_boundaries() -> None:
    adaptation = _score_payload()
    adaptation.update(
        {
            "scenario": ExperimentScenario.ADAPTATION,
            "personalization_checks": [
                PersonalizationCheck(
                    constraint_id="risk-label",
                    expectation=MatchExpectation.PRESENT,
                    target_field="summary",
                    matcher_matched=True,
                    satisfied=True,
                )
            ],
            "metric": _successful_metric(
                quality=0.875,
                personalization_total=1,
                personalization_satisfied=1,
            ),
        }
    )
    assert len(RunScoreRecord.model_validate(adaptation).personalization_checks) == 1

    missing = _score_payload()
    missing["scenario"] = ExperimentScenario.ADAPTATION
    with pytest.raises(ValidationError, match="requires personalization"):
        RunScoreRecord.model_validate(missing)

    failed = _score_payload()
    failed.update(
        {
            "run_status": RunStatus.TIMED_OUT,
            "schema_passed": None,
            "fact_checks": [],
            "forbidden_checks": [],
            "metric": MetricRecord(run_id=RUN_ID, run_status=RunStatus.TIMED_OUT),
        }
    )
    assert RunScoreRecord.model_validate(failed).fact_checks == ()

    failed["schema_passed"] = False
    with pytest.raises(ValidationError, match="failed run cannot contain"):
        RunScoreRecord.model_validate(failed)


def test_build_session_rejects_impossible_duration_accounting() -> None:
    with pytest.raises(ValidationError, match="exceed wall clock"):
        BuildSession(
            session_id=RUN_ID,
            condition=ExperimentCondition.FACTORY,
            domain_id="nexora-events",
            sequence_number=2,
            active_seconds=50,
            wall_clock_seconds=60,
            excluded_wait_seconds=20,
            successful=True,
        )

    valid = BuildSession(
        session_id=RUN_ID,
        condition=ExperimentCondition.MANUAL,
        domain_id="nexora-events",
        sequence_number=1,
        active_seconds=40,
        wall_clock_seconds=60,
        excluded_wait_seconds=20,
        successful=True,
    )
    assert valid.wall_clock_seconds == 60


def test_audit_verification_recomputes_completeness() -> None:
    passed_step = AuditStepResult(
        step_id="prototype-registered",
        expected_event_type=AuditEventType.PROTOTYPE_REGISTERED,
        matched_event_id=EVENT_ID,
        passed=True,
    )
    failed_step = AuditStepResult(
        step_id="knowledge-bound",
        expected_event_type=AuditEventType.KNOWLEDGE_BOUND,
        passed=False,
        reason="missing event",
    )

    with pytest.raises(ValidationError, match="does not match"):
        AuditVerificationRecord(
            verification_id=RUN_ID,
            experiment_id="writer-validation-v1",
            instance_id=INSTANCE_ID,
            checked_at=NOW,
            steps=(passed_step, failed_step),
            completeness=1,
            passed=True,
        )

    record = AuditVerificationRecord(
        verification_id=RUN_ID,
        experiment_id="writer-validation-v1",
        instance_id=INSTANCE_ID,
        checked_at=NOW,
        steps=(passed_step,),
        completeness=1,
        passed=True,
    )
    with pytest.raises(AttributeError):
        cast(list[AuditStepResult], record.steps).append(failed_step)


def test_audit_steps_and_record_reject_contradictory_metadata() -> None:
    with pytest.raises(ValidationError, match="requires event"):
        AuditStepResult(
            step_id="prototype-registered",
            expected_event_type=AuditEventType.PROTOTYPE_REGISTERED,
            passed=True,
        )
    with pytest.raises(ValidationError, match="requires reason"):
        AuditStepResult(
            step_id="knowledge-bound",
            expected_event_type=AuditEventType.KNOWLEDGE_BOUND,
            passed=False,
        )

    step = AuditStepResult(
        step_id="prototype-registered",
        expected_event_type=AuditEventType.PROTOTYPE_REGISTERED,
        matched_event_id=EVENT_ID,
        passed=True,
    )
    with pytest.raises(ValidationError, match="duplicate step IDs"):
        AuditVerificationRecord(
            verification_id=RUN_ID,
            experiment_id="writer-validation-v1",
            instance_id=INSTANCE_ID,
            checked_at=NOW,
            steps=(step, step),
            completeness=1,
            passed=True,
        )
    with pytest.raises(ValidationError, match="passed requires complete evidence"):
        AuditVerificationRecord(
            verification_id=RUN_ID,
            experiment_id="writer-validation-v1",
            instance_id=INSTANCE_ID,
            checked_at=NOW,
            steps=(
                AuditStepResult(
                    step_id="knowledge-bound",
                    expected_event_type=AuditEventType.KNOWLEDGE_BOUND,
                    passed=False,
                    reason="missing",
                ),
            ),
            completeness=0,
            passed=True,
        )
