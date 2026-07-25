"""Deterministic task-level analysis over complete synthetic score sets."""

from __future__ import annotations

import hashlib
from collections.abc import Collection

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import canonical_json_bytes, sha256_model
from experiments.analysis import (
    AnalysisError,
    ExperimentAnalyzer,
    _bounded_sha256_index,
    _percentile_type7,
)
from experiments.contracts import (
    AnalysisConfig,
    AnalysisPopulation,
    AnalysisSummary,
    ConfidenceInterval,
    ExecutionPlan,
    ExperimentCondition,
    ExperimentScenario,
    FactCheck,
    ForbiddenMatcherCheck,
    HypothesisDecision,
    HypothesisName,
    HypothesisResult,
    MatchExpectation,
    MetricRecord,
    PersonalizationCheck,
    RunScoreRecord,
    RunStatus,
    SchemaViolation,
    TaskConditionAggregate,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import build_execution_plan

SHA_F = "f" * 64


def _quality(
    *,
    schema_passed: bool,
    facts_covered: int,
    facts_total: int,
    forbidden_total: int,
    personalization_satisfied: int,
    personalization_total: int,
) -> float:
    components = [float(schema_passed), facts_covered / facts_total]
    if forbidden_total:
        components.append(1.0)
    if personalization_total:
        components.append(personalization_satisfied / personalization_total)
    return round(sum(components) / len(components), 12)


def _score_set(
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    *,
    schema_passed: dict[ExperimentCondition, bool] | None = None,
    facts_covered: dict[ExperimentCondition, bool] | None = None,
    personalization_satisfied: dict[ExperimentCondition, bool] | None = None,
    failed: Collection[tuple[ExperimentCondition, str, int]] = (),
    manual_fact_omission_tasks: Collection[str] | None = None,
) -> tuple[RunScoreRecord, ...]:
    schema_profile = schema_passed or {
        ExperimentCondition.MANUAL: False,
        ExperimentCondition.FACTORY: True,
    }
    fact_profile = facts_covered or {
        ExperimentCondition.MANUAL: False,
        ExperimentCondition.FACTORY: True,
    }
    personalization_profile = personalization_satisfied or {
        ExperimentCondition.MANUAL: True,
        ExperimentCondition.FACTORY: True,
    }
    tasks = {task.task_id: task for task in dataset.tasks}
    rubrics = {rubric.rubric_id: rubric for rubric in dataset.rubrics}
    records: list[RunScoreRecord] = []
    for item in plan.items:
        task = tasks[item.task_id]
        rubric = rubrics[task.rubric_id]
        common = {
            "run_id": item.run_id,
            "run_checksum": hashlib.sha256(
                canonical_json_bytes(
                    {
                        "run_id": str(item.run_id),
                        "condition": item.condition.value,
                        "task_id": item.task_id,
                        "repetition": item.repetition,
                    }
                )
            ).hexdigest(),
            "experiment_id": dataset.definition.experiment_id,
            "plan_checksum": plan.plan_checksum,
            "condition": item.condition,
            "task_id": item.task_id,
            "scenario": task.scenario,
            "repetition": item.repetition,
            "execution_order": item.execution_order,
            "rubric_id": rubric.rubric_id,
            "rubric_checksum": sha256_model(rubric),
        }
        coordinate = (item.condition, item.task_id, item.repetition)
        if coordinate in failed:
            records.append(
                RunScoreRecord.model_validate(
                    {
                        **common,
                        "run_status": RunStatus.PROVIDER_FAILED,
                        "metric": MetricRecord(
                            run_id=item.run_id,
                            run_status=RunStatus.PROVIDER_FAILED,
                        ),
                    }
                )
            )
            continue

        schema_ok = schema_profile[item.condition]
        facts_ok = fact_profile[item.condition]
        if (
            manual_fact_omission_tasks is not None
            and item.condition is ExperimentCondition.MANUAL
        ):
            facts_ok = item.task_id not in manual_fact_omission_tasks
        personalization_ok = personalization_profile[item.condition]
        fact_checks = tuple(
            FactCheck(
                fact_id=fact_id,
                covered=facts_ok,
                matched_by_index=0 if facts_ok else None,
            )
            for fact_id in rubric.required_fact_ids
        )
        forbidden_checks = tuple(
            ForbiddenMatcherCheck(matcher_index=index, violated=False)
            for index, _ in enumerate(rubric.forbidden_matchers)
        )
        personalization_checks = tuple(
            PersonalizationCheck(
                constraint_id=constraint.constraint_id,
                expectation=constraint.expectation,
                target_field=constraint.target_field,
                matcher_matched=(
                    personalization_ok
                    if constraint.expectation is MatchExpectation.PRESENT
                    else not personalization_ok
                ),
                satisfied=personalization_ok,
            )
            for constraint in rubric.personalization_constraints
        )
        facts_covered_count = sum(check.covered for check in fact_checks)
        personalization_satisfied_count = sum(
            check.satisfied for check in personalization_checks
        )
        records.append(
            RunScoreRecord.model_validate(
                {
                    **common,
                    "run_status": RunStatus.SUCCEEDED,
                    "schema_passed": schema_ok,
                    "schema_violations": (
                        ()
                        if schema_ok
                        else (
                            SchemaViolation(
                                instance_path="$",
                                schema_path="$",
                                validator="required",
                            ),
                        )
                    ),
                    "fact_checks": fact_checks,
                    "forbidden_checks": forbidden_checks,
                    "personalization_checks": personalization_checks,
                    "metric": MetricRecord(
                        run_id=item.run_id,
                        run_status=RunStatus.SUCCEEDED,
                        schema_passed=schema_ok,
                        required_facts_total=len(fact_checks),
                        required_facts_covered=facts_covered_count,
                        forbidden_matchers_total=len(forbidden_checks),
                        forbidden_matchers_violated=0,
                        personalization_total=len(personalization_checks),
                        personalization_satisfied=personalization_satisfied_count,
                        deterministic_quality_score=_quality(
                            schema_passed=schema_ok,
                            facts_covered=facts_covered_count,
                            facts_total=len(fact_checks),
                            forbidden_total=len(forbidden_checks),
                            personalization_satisfied=personalization_satisfied_count,
                            personalization_total=len(personalization_checks),
                        ),
                    ),
                }
            )
        )
    return tuple(records)


def _analyzer(
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
) -> ExperimentAnalyzer:
    return ExperimentAnalyzer(
        dataset,
        plan,
        AnalysisConfig(
            bootstrap_seed=dataset.definition.randomization_seed,
            bootstrap_iterations=200,
        ),
    )


def _result(
    hypotheses: tuple[HypothesisResult, ...],
    population: AnalysisPopulation,
    hypothesis: HypothesisName,
) -> HypothesisResult:
    return next(
        item
        for item in hypotheses
        if item.population is population and item.hypothesis is hypothesis
    )


def test_reports_known_primary_effects_and_threshold_decisions(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)

    summary = _analyzer(dataset, plan).analyze(_score_set(dataset, plan))

    h1 = _result(
        summary.hypotheses,
        AnalysisPopulation.INTENTION_TO_TREAT,
        HypothesisName.H1_SCHEMA_CONSISTENCY,
    )
    assert h1.paired_task_count == 24
    assert h1.effect_estimate == 1
    assert h1.confidence_interval.lower == 1
    assert h1.confidence_interval.upper == 1
    assert h1.decision is HypothesisDecision.SUPPORTED

    h2 = _result(
        summary.hypotheses,
        AnalysisPopulation.INTENTION_TO_TREAT,
        HypothesisName.H2_KNOWLEDGE_OMISSION,
    )
    assert h2.effect_estimate == 1
    assert h2.absolute_difference == 1
    assert h2.decision is HypothesisDecision.SUPPORTED

    h4 = _result(
        summary.hypotheses,
        AnalysisPopulation.INTENTION_TO_TREAT,
        HypothesisName.H4_PERSONALIZATION,
    )
    assert h4.paired_task_count == 12
    assert h4.effect_estimate == 0
    assert h4.decision is HypothesisDecision.SUPPORTED
    assert len(summary.aggregates) == 96


def test_unfavorable_effects_are_not_supported(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = _score_set(
        dataset,
        plan,
        schema_passed={
            ExperimentCondition.MANUAL: False,
            ExperimentCondition.FACTORY: False,
        },
        facts_covered={
            ExperimentCondition.MANUAL: False,
            ExperimentCondition.FACTORY: False,
        },
        personalization_satisfied={
            ExperimentCondition.MANUAL: True,
            ExperimentCondition.FACTORY: False,
        },
    )

    summary = _analyzer(dataset, plan).analyze(scores)

    for hypothesis in HypothesisName:
        result = _result(
            summary.hypotheses,
            AnalysisPopulation.INTENTION_TO_TREAT,
            hypothesis,
        )
        assert result.decision is HypothesisDecision.NOT_SUPPORTED


def test_input_order_is_irrelevant_and_repetitions_are_nested_within_tasks(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = _score_set(dataset, plan)
    analyzer = _analyzer(dataset, plan)

    forward = analyzer.analyze(scores)
    reverse = analyzer.analyze(reversed(scores))

    assert forward == reverse
    assert forward.score_set_checksum == reverse.score_set_checksum
    assert {item.planned_runs for item in forward.aggregates} == {5}
    assert {
        item.paired_task_count
        for item in forward.hypotheses
        if item.hypothesis is HypothesisName.H1_SCHEMA_CONSISTENCY
    } == {24}


def test_itt_maps_failed_runs_to_worst_case_while_sensitivity_drops_them(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    task_id = dataset.tasks[0].task_id
    failed = {
        (ExperimentCondition.MANUAL, task_id, repetition)
        for repetition in range(1, dataset.definition.repetitions + 1)
    }

    summary = _analyzer(dataset, plan).analyze(_score_set(dataset, plan, failed=failed))

    itt = next(
        item
        for item in summary.aggregates
        if item.population is AnalysisPopulation.INTENTION_TO_TREAT
        and item.task_id == task_id
        and item.condition is ExperimentCondition.MANUAL
    )
    sensitivity = next(
        item
        for item in summary.aggregates
        if item.population is AnalysisPopulation.SUCCEEDED_ONLY
        and item.task_id == task_id
        and item.condition is ExperimentCondition.MANUAL
    )
    assert (itt.planned_runs, itt.included_runs, itt.succeeded_runs) == (5, 5, 0)
    assert itt.schema_pass_rate == 0
    assert itt.omission_rate == 1
    if itt.scenario is ExperimentScenario.ADAPTATION:
        assert itt.adaptation_rate == 0
    assert sensitivity.included_runs == 0
    assert sensitivity.schema_pass_rate is None
    assert sensitivity.omission_rate is None

    sensitivity_h1 = _result(
        summary.hypotheses,
        AnalysisPopulation.SUCCEEDED_ONLY,
        HypothesisName.H1_SCHEMA_CONSISTENCY,
    )
    assert sensitivity_h1.paired_task_count == 23
    assert sensitivity_h1.decision is HypothesisDecision.NOT_EVALUATED


def test_zero_manual_omission_reports_absolute_effect_but_insufficient_relative(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = _score_set(
        dataset,
        plan,
        facts_covered={
            ExperimentCondition.MANUAL: True,
            ExperimentCondition.FACTORY: True,
        },
    )

    summary = _analyzer(dataset, plan).analyze(scores)
    result = _result(
        summary.hypotheses,
        AnalysisPopulation.INTENTION_TO_TREAT,
        HypothesisName.H2_KNOWLEDGE_OMISSION,
    )

    assert result.effect_estimate is None
    assert result.absolute_difference == 0
    assert result.confidence_interval.valid_replicates == 0
    assert result.confidence_interval.invalid_replicates == 200
    assert result.absolute_difference_interval is not None
    assert result.absolute_difference_interval.lower == 0
    assert result.decision is HypothesisDecision.INSUFFICIENT_EVIDENCE


def test_h2_requires_at_least_ninety_five_percent_valid_bootstrap_replicates(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = _score_set(
        dataset,
        plan,
        facts_covered={
            ExperimentCondition.MANUAL: True,
            ExperimentCondition.FACTORY: True,
        },
        manual_fact_omission_tasks={dataset.tasks[0].task_id},
    )

    summary = _analyzer(dataset, plan).analyze(scores)
    result = _result(
        summary.hypotheses,
        AnalysisPopulation.INTENTION_TO_TREAT,
        HypothesisName.H2_KNOWLEDGE_OMISSION,
    )

    valid_fraction = (
        result.confidence_interval.valid_replicates
        / result.confidence_interval.requested_replicates
    )
    assert result.effect_estimate == 1
    assert valid_fraction < 0.95
    assert result.decision is HypothesisDecision.INSUFFICIENT_EVIDENCE


def test_rejects_missing_and_duplicate_score_evidence(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = _score_set(dataset, plan)
    analyzer = _analyzer(dataset, plan)

    with pytest.raises(AnalysisError, match="all execution plan runs"):
        analyzer.analyze(scores[:-1])
    with pytest.raises(AnalysisError, match="duplicate run IDs"):
        analyzer.analyze((*scores, scores[0]))


def test_rejects_coordinate_and_rubric_provenance_mismatches(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = list(_score_set(dataset, plan))
    analyzer = _analyzer(dataset, plan)

    coordinate_mismatch = list(scores)
    coordinate_mismatch[0] = scores[0].model_copy(
        update={"execution_order": scores[0].execution_order + 1}
    )
    with pytest.raises(AnalysisError, match="coordinates"):
        analyzer.analyze(coordinate_mismatch)

    rubric_mismatch = list(scores)
    rubric_mismatch[0] = scores[0].model_copy(update={"rubric_checksum": SHA_F})
    with pytest.raises(AnalysisError, match="rubric provenance"):
        analyzer.analyze(rubric_mismatch)


def test_rejects_rubric_checksum_with_tampered_scoring_details(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    scores = list(_score_set(dataset, plan))
    analyzer = _analyzer(dataset, plan)

    fact_index = next(index for index, score in enumerate(scores) if score.fact_checks)
    tampered = list(scores)
    tampered[fact_index] = scores[fact_index].model_copy(
        update={"fact_checks": scores[fact_index].fact_checks[:-1]}
    )
    with pytest.raises(AnalysisError, match="fact evidence"):
        analyzer.analyze(tampered)

    forbidden_index = next(
        index for index, score in enumerate(scores) if score.forbidden_checks
    )
    tampered = list(scores)
    tampered[forbidden_index] = scores[forbidden_index].model_copy(
        update={"forbidden_checks": ()}
    )
    with pytest.raises(AnalysisError, match="forbidden evidence"):
        analyzer.analyze(tampered)

    personalization_index = next(
        index for index, score in enumerate(scores) if score.personalization_checks
    )
    tampered = list(scores)
    tampered[personalization_index] = scores[personalization_index].model_copy(
        update={"personalization_checks": ()}
    )
    with pytest.raises(AnalysisError, match="personalization evidence"):
        analyzer.analyze(tampered)


def test_succeeded_only_sensitivity_can_report_no_paired_tasks(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    failed = {
        (ExperimentCondition.MANUAL, task.task_id, repetition)
        for task in dataset.tasks
        for repetition in range(1, dataset.definition.repetitions + 1)
    }

    summary = _analyzer(dataset, plan).analyze(_score_set(dataset, plan, failed=failed))

    for hypothesis in HypothesisName:
        result = _result(
            summary.hypotheses,
            AnalysisPopulation.SUCCEEDED_ONLY,
            hypothesis,
        )
        assert result.paired_task_count == 0
        assert result.effect_estimate is None
        assert result.confidence_interval.valid_replicates == 0
        assert result.confidence_interval.invalid_replicates == 200
        assert result.decision is HypothesisDecision.NOT_EVALUATED


def test_rejects_stale_execution_plan(dataset: LoadedExperimentDataset) -> None:
    plan = build_execution_plan(dataset)
    stale = plan.model_copy(update={"plan_checksum": SHA_F})

    with pytest.raises(AnalysisError, match="execution plan"):
        ExperimentAnalyzer(dataset, stale)


def test_type7_percentile_uses_linear_interpolation() -> None:
    assert _percentile_type7([0.0, 10.0], 0.25) == 2.5
    assert _percentile_type7([0.0, 10.0, 20.0], 0.5) == 10
    with pytest.raises(AnalysisError, match="at least one"):
        _percentile_type7([], 0.5)
    with pytest.raises(AnalysisError, match="between zero and one"):
        _percentile_type7([1.0], 1.1)


def test_decision_bands_remain_inconclusive_between_frozen_thresholds(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    analyzer = _analyzer(dataset, plan)
    interval = ConfidenceInterval(
        lower=0.06,
        upper=0.09,
        requested_replicates=200,
        valid_replicates=200,
        invalid_replicates=0,
    )
    h4_interval = interval.model_copy(update={"lower": -0.06, "upper": -0.04})

    assert (
        analyzer._decision(
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            interval=interval,
        )
        is HypothesisDecision.INSUFFICIENT_EVIDENCE
    )
    assert (
        analyzer._decision(
            hypothesis=HypothesisName.H4_PERSONALIZATION,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            interval=h4_interval,
        )
        is HypothesisDecision.INSUFFICIENT_EVIDENCE
    )


def test_sha256_sampling_rejects_modulo_bias_and_has_a_bounded_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _bounded_sha256_index(
        size=12,
        seed=20260725,
        hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
        population=AnalysisPopulation.INTENTION_TO_TREAT,
        scenario=ExperimentScenario.CONSISTENCY,
        replicate=0,
        draw=0,
    )
    assert 0 <= index < 12
    with pytest.raises(AnalysisError, match="at least one task"):
        _bounded_sha256_index(
            size=0,
            seed=0,
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            scenario=ExperimentScenario.CONSISTENCY,
            replicate=0,
            draw=0,
        )

    class _RejectingDigest:
        def digest(self) -> bytes:
            return b"\xff" * 32

    def always_reject(_: bytes) -> _RejectingDigest:
        return _RejectingDigest()

    monkeypatch.setattr(hashlib, "sha256", always_reject)
    with pytest.raises(AnalysisError, match="rejection limit"):
        _bounded_sha256_index(
            size=3,
            seed=0,
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            scenario=ExperimentScenario.CONSISTENCY,
            replicate=0,
            draw=0,
        )


def test_default_analysis_contract_freezes_ten_thousand_replicates(
    dataset: LoadedExperimentDataset,
) -> None:
    config = AnalysisConfig(bootstrap_seed=dataset.definition.randomization_seed)

    assert config.bootstrap_iterations == 10_000
    assert config.confidence_level == 0.95


def test_analysis_contracts_reject_contradictory_derived_evidence(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    summary = _analyzer(dataset, plan).analyze(_score_set(dataset, plan))
    aggregate = summary.aggregates[0]
    aggregate_payload = aggregate.model_dump(mode="python")
    aggregate_payload["schema_pass_rate"] = 0.123
    with pytest.raises(ValidationError, match="schema pass rate"):
        TaskConditionAggregate.model_validate(aggregate_payload)

    interval_payload = {
        "lower": 1.0,
        "upper": 0.0,
        "requested_replicates": 200,
        "valid_replicates": 200,
        "invalid_replicates": 0,
    }
    with pytest.raises(ValidationError, match="lower bound exceeds"):
        ConfidenceInterval.model_validate(interval_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["config_checksum"] = SHA_F
    with pytest.raises(ValidationError, match="config checksum"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["aggregates"] = [
        *summary_payload["aggregates"],
        summary_payload["aggregates"][0],
    ]
    with pytest.raises(ValidationError, match="duplicate coordinates"):
        AnalysisSummary.model_validate(summary_payload)

    removed_task = summary.aggregates[0].task_id
    summary_payload = summary.model_dump(mode="python")
    summary_payload["aggregates"] = [
        item
        for item in summary_payload["aggregates"]
        if item["task_id"] != removed_task
    ]
    with pytest.raises(ValidationError, match="task count"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["aggregates"] = summary_payload["aggregates"][:-1]
    with pytest.raises(ValidationError, match="matrix is incomplete"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["repetitions"] = 4
    with pytest.raises(ValidationError, match="repetitions"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["aggregates"][0]["domain_id"] = "changed-domain"
    with pytest.raises(ValidationError, match="metadata"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    summary_payload["hypotheses"] = summary_payload["hypotheses"][:-1]
    with pytest.raises(ValidationError, match="both populations"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    itt_h1 = next(
        item
        for item in summary_payload["hypotheses"]
        if item["population"] is AnalysisPopulation.INTENTION_TO_TREAT
        and item["hypothesis"] is HypothesisName.H1_SCHEMA_CONSISTENCY
    )
    itt_h1["paired_task_count"] = 23
    with pytest.raises(ValidationError, match="ITT H1 and H2"):
        AnalysisSummary.model_validate(summary_payload)

    summary_payload = summary.model_dump(mode="python")
    itt_h4 = next(
        item
        for item in summary_payload["hypotheses"]
        if item["population"] is AnalysisPopulation.INTENTION_TO_TREAT
        and item["hypothesis"] is HypothesisName.H4_PERSONALIZATION
    )
    itt_h4["paired_task_count"] = 11
    with pytest.raises(ValidationError, match="ITT H4"):
        AnalysisSummary.model_validate(summary_payload)
