"""Task-paired deterministic analysis for frozen M5 score evidence."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agent_factory.domain.common import canonical_json_bytes, sha256_model
from experiments.contracts import (
    AnalysisConfig,
    AnalysisPopulation,
    AnalysisSummary,
    ConfidenceInterval,
    ExecutionPlan,
    ExperimentCondition,
    ExperimentScenario,
    HypothesisDecision,
    HypothesisName,
    HypothesisResult,
    RubricDefinition,
    RunScoreRecord,
    RunStatus,
    TaskConditionAggregate,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import validate_execution_plan


class AnalysisError(ValueError):
    """Score evidence cannot be analyzed against the frozen experiment."""


@dataclass(frozen=True, slots=True)
class _PairedObservation:
    task_id: str
    scenario: ExperimentScenario
    manual: float
    factory: float


class ExperimentAnalyzer:
    """Aggregate repetitions by task and run deterministic paired bootstrap."""

    def __init__(
        self,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        config: AnalysisConfig | None = None,
    ) -> None:
        try:
            validate_execution_plan(plan, dataset)
        except ValueError as exc:
            raise AnalysisError("execution plan does not match frozen dataset") from exc
        self._dataset = dataset
        self._plan = plan
        self._config = config or AnalysisConfig(
            bootstrap_seed=dataset.definition.randomization_seed
        )
        self._tasks = {task.task_id: task for task in dataset.tasks}
        self._rubrics = {rubric.rubric_id: rubric for rubric in dataset.rubrics}

    def analyze(self, scores: Iterable[RunScoreRecord]) -> AnalysisSummary:
        """Validate one complete score set and return a reproducible summary."""

        ordered_scores = self._validate_and_order_scores(tuple(scores))
        aggregates = self._build_aggregates(ordered_scores)
        hypotheses = tuple(
            result
            for population in AnalysisPopulation
            for result in self._hypothesis_results(population, aggregates)
        )
        score_set_checksum = hashlib.sha256(
            canonical_json_bytes(
                [score.model_dump(mode="json") for score in ordered_scores]
            )
        ).hexdigest()
        return AnalysisSummary(
            experiment_id=self._dataset.definition.experiment_id,
            dataset_checksum=self._dataset.dataset_checksum,
            definition_checksum=sha256_model(self._dataset.definition),
            plan_checksum=self._plan.plan_checksum,
            score_set_checksum=score_set_checksum,
            task_count=len(self._dataset.tasks),
            repetitions=self._dataset.definition.repetitions,
            config=self._config,
            config_checksum=sha256_model(self._config),
            aggregates=aggregates,
            hypotheses=hypotheses,
        )

    def _validate_and_order_scores(
        self,
        scores: tuple[RunScoreRecord, ...],
    ) -> tuple[RunScoreRecord, ...]:
        by_run_id: dict[object, RunScoreRecord] = {}
        for score in scores:
            if score.run_id in by_run_id:
                raise AnalysisError("score set contains duplicate run IDs")
            by_run_id[score.run_id] = score
        expected_ids = {item.run_id for item in self._plan.items}
        if set(by_run_id) != expected_ids:
            raise AnalysisError("score set does not match all execution plan runs")

        ordered: list[RunScoreRecord] = []
        for item in sorted(self._plan.items, key=lambda value: value.execution_order):
            score = by_run_id[item.run_id]
            task = self._tasks[item.task_id]
            rubric = self._rubrics[task.rubric_id]
            if (
                score.experiment_id != self._dataset.definition.experiment_id
                or score.plan_checksum != self._plan.plan_checksum
                or score.condition is not item.condition
                or score.task_id != item.task_id
                or score.repetition != item.repetition
                or score.execution_order != item.execution_order
                or score.scenario is not task.scenario
            ):
                raise AnalysisError("score coordinates do not match execution plan")
            self._validate_rubric_evidence(score, rubric)
            ordered.append(score)
        return tuple(ordered)

    @staticmethod
    def _validate_rubric_evidence(
        score: RunScoreRecord,
        rubric: RubricDefinition,
    ) -> None:
        if score.rubric_id != rubric.rubric_id or score.rubric_checksum != sha256_model(
            rubric
        ):
            raise AnalysisError("score rubric provenance does not match frozen rubric")
        if score.run_status is not RunStatus.SUCCEEDED:
            return
        if (
            tuple(item.fact_id for item in score.fact_checks)
            != rubric.required_fact_ids
        ):
            raise AnalysisError("score fact evidence does not match frozen rubric")
        if len(score.forbidden_checks) != len(rubric.forbidden_matchers):
            raise AnalysisError("score forbidden evidence does not match frozen rubric")
        actual_personalization = tuple(
            (item.constraint_id, item.expectation, item.target_field)
            for item in score.personalization_checks
        )
        expected_personalization = tuple(
            (item.constraint_id, item.expectation, item.target_field)
            for item in rubric.personalization_constraints
        )
        if actual_personalization != expected_personalization:
            raise AnalysisError(
                "score personalization evidence does not match frozen rubric"
            )

    def _build_aggregates(
        self,
        scores: tuple[RunScoreRecord, ...],
    ) -> tuple[TaskConditionAggregate, ...]:
        by_coordinate: dict[tuple[str, ExperimentCondition], list[RunScoreRecord]] = (
            defaultdict(list)
        )
        for score in scores:
            by_coordinate[(score.task_id, score.condition)].append(score)

        aggregates: list[TaskConditionAggregate] = []
        for population in AnalysisPopulation:
            for task in self._dataset.tasks:
                rubric = self._rubrics[task.rubric_id]
                for condition in self._dataset.definition.conditions:
                    coordinate_scores = sorted(
                        by_coordinate[(task.task_id, condition)],
                        key=lambda item: item.repetition,
                    )
                    aggregates.append(
                        self._aggregate_coordinate(
                            population=population,
                            task_id=task.task_id,
                            domain_id=task.domain_id,
                            scenario=task.scenario,
                            condition=condition,
                            rubric=rubric,
                            scores=coordinate_scores,
                        )
                    )
        return tuple(aggregates)

    @staticmethod
    def _aggregate_coordinate(
        *,
        population: AnalysisPopulation,
        task_id: str,
        domain_id: str,
        scenario: ExperimentScenario,
        condition: ExperimentCondition,
        rubric: RubricDefinition,
        scores: Sequence[RunScoreRecord],
    ) -> TaskConditionAggregate:
        succeeded_runs = sum(
            score.run_status is RunStatus.SUCCEEDED for score in scores
        )
        included = [
            score
            for score in scores
            if population is AnalysisPopulation.INTENTION_TO_TREAT
            or score.run_status is RunStatus.SUCCEEDED
        ]
        schema_passes = 0
        required_facts_total = 0
        required_facts_covered = 0
        personalization_total = 0
        personalization_satisfied = 0
        for score in included:
            required_facts_total += len(rubric.required_fact_ids)
            personalization_total += len(rubric.personalization_constraints)
            if score.run_status is not RunStatus.SUCCEEDED:
                continue
            metric = score.metric
            assert metric.schema_passed is not None
            assert metric.required_facts_covered is not None
            assert metric.personalization_satisfied is not None
            schema_passes += metric.schema_passed
            required_facts_covered += metric.required_facts_covered
            personalization_satisfied += metric.personalization_satisfied
        included_runs = len(included)
        return TaskConditionAggregate(
            task_id=task_id,
            domain_id=domain_id,
            scenario=scenario,
            condition=condition,
            population=population,
            planned_runs=len(scores),
            included_runs=included_runs,
            succeeded_runs=succeeded_runs,
            schema_passes=schema_passes,
            required_facts_total=required_facts_total,
            required_facts_covered=required_facts_covered,
            personalization_total=personalization_total,
            personalization_satisfied=personalization_satisfied,
            schema_pass_rate=(
                None if included_runs == 0 else round(schema_passes / included_runs, 12)
            ),
            omission_rate=(
                None
                if required_facts_total == 0
                else round(
                    1 - required_facts_covered / required_facts_total,
                    12,
                )
            ),
            adaptation_rate=(
                None
                if personalization_total == 0
                else round(
                    personalization_satisfied / personalization_total,
                    12,
                )
            ),
        )

    def _hypothesis_results(
        self,
        population: AnalysisPopulation,
        aggregates: tuple[TaskConditionAggregate, ...],
    ) -> tuple[HypothesisResult, ...]:
        population_aggregates = {
            (item.task_id, item.condition): item
            for item in aggregates
            if item.population is population
        }
        h1 = self._difference_result(
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=population,
            observations=self._observations(
                population_aggregates,
                metric="schema_pass_rate",
            ),
        )
        h2 = self._omission_result(
            population=population,
            observations=self._observations(
                population_aggregates,
                metric="omission_rate",
            ),
        )
        h4 = self._difference_result(
            hypothesis=HypothesisName.H4_PERSONALIZATION,
            population=population,
            observations=self._observations(
                population_aggregates,
                metric="adaptation_rate",
                scenario=ExperimentScenario.ADAPTATION,
            ),
        )
        return h1, h2, h4

    def _observations(
        self,
        aggregates: dict[tuple[str, ExperimentCondition], TaskConditionAggregate],
        *,
        metric: str,
        scenario: ExperimentScenario | None = None,
    ) -> tuple[_PairedObservation, ...]:
        observations: list[_PairedObservation] = []
        for task in self._dataset.tasks:
            if scenario is not None and task.scenario is not scenario:
                continue
            manual = aggregates[(task.task_id, ExperimentCondition.MANUAL)]
            factory = aggregates[(task.task_id, ExperimentCondition.FACTORY)]
            manual_value = getattr(manual, metric)
            factory_value = getattr(factory, metric)
            if manual_value is None or factory_value is None:
                continue
            observations.append(
                _PairedObservation(
                    task_id=task.task_id,
                    scenario=task.scenario,
                    manual=manual_value,
                    factory=factory_value,
                )
            )
        return tuple(sorted(observations, key=lambda item: item.task_id))

    def _difference_result(
        self,
        *,
        hypothesis: HypothesisName,
        population: AnalysisPopulation,
        observations: tuple[_PairedObservation, ...],
    ) -> HypothesisResult:
        estimate = (
            None
            if not observations
            else round(
                sum(item.factory - item.manual for item in observations)
                / len(observations),
                12,
            )
        )
        bootstrap_values = [
            sum(item.factory - item.manual for item in sample) / len(sample)
            for sample in self._bootstrap_samples(
                observations,
                hypothesis=hypothesis,
                population=population,
            )
        ]
        interval = _confidence_interval(
            bootstrap_values,
            requested=self._config.bootstrap_iterations,
        )
        decision = self._decision(
            hypothesis=hypothesis,
            population=population,
            interval=interval,
        )
        return HypothesisResult(
            hypothesis=hypothesis,
            population=population,
            paired_task_count=len(observations),
            effect_estimate=estimate,
            confidence_interval=interval,
            decision=decision,
        )

    def _omission_result(
        self,
        *,
        population: AnalysisPopulation,
        observations: tuple[_PairedObservation, ...],
    ) -> HypothesisResult:
        absolute, relative = _omission_effect(observations)
        absolute_values: list[float] = []
        relative_values: list[float] = []
        for sample in self._bootstrap_samples(
            observations,
            hypothesis=HypothesisName.H2_KNOWLEDGE_OMISSION,
            population=population,
        ):
            sampled_absolute, sampled_relative = _omission_effect(sample)
            assert sampled_absolute is not None
            absolute_values.append(sampled_absolute)
            if sampled_relative is not None:
                relative_values.append(sampled_relative)
        relative_interval = _confidence_interval(
            relative_values,
            requested=self._config.bootstrap_iterations,
        )
        absolute_interval = _confidence_interval(
            absolute_values,
            requested=self._config.bootstrap_iterations,
        )
        decision = self._decision(
            hypothesis=HypothesisName.H2_KNOWLEDGE_OMISSION,
            population=population,
            interval=relative_interval,
        )
        return HypothesisResult(
            hypothesis=HypothesisName.H2_KNOWLEDGE_OMISSION,
            population=population,
            paired_task_count=len(observations),
            effect_estimate=None if relative is None else round(relative, 12),
            confidence_interval=relative_interval,
            absolute_difference=(None if absolute is None else round(absolute, 12)),
            absolute_difference_interval=(
                None if not observations else absolute_interval
            ),
            decision=decision,
        )

    def _bootstrap_samples(
        self,
        observations: tuple[_PairedObservation, ...],
        *,
        hypothesis: HypothesisName,
        population: AnalysisPopulation,
    ) -> Iterable[tuple[_PairedObservation, ...]]:
        if not observations:
            return
        strata: dict[ExperimentScenario, list[_PairedObservation]] = defaultdict(list)
        for observation in observations:
            strata[observation.scenario].append(observation)
        ordered_strata = tuple(
            (scenario, tuple(sorted(items, key=lambda item: item.task_id)))
            for scenario, items in sorted(
                strata.items(), key=lambda item: item[0].value
            )
        )
        for replicate in range(self._config.bootstrap_iterations):
            sampled: list[_PairedObservation] = []
            for scenario, items in ordered_strata:
                for draw in range(len(items)):
                    index = _bounded_sha256_index(
                        size=len(items),
                        seed=self._config.bootstrap_seed,
                        hypothesis=hypothesis,
                        population=population,
                        scenario=scenario,
                        replicate=replicate,
                        draw=draw,
                    )
                    sampled.append(items[index])
            yield tuple(sampled)

    def _decision(
        self,
        *,
        hypothesis: HypothesisName,
        population: AnalysisPopulation,
        interval: ConfidenceInterval,
    ) -> HypothesisDecision:
        if population is AnalysisPopulation.SUCCEEDED_ONLY:
            return HypothesisDecision.NOT_EVALUATED
        if interval.lower is None or interval.upper is None:
            return HypothesisDecision.INSUFFICIENT_EVIDENCE
        thresholds = self._dataset.definition.thresholds
        if hypothesis is HypothesisName.H1_SCHEMA_CONSISTENCY:
            if interval.lower >= thresholds.h1_support_min_absolute_difference:
                return HypothesisDecision.SUPPORTED
            if interval.upper < thresholds.h1_not_support_below:
                return HypothesisDecision.NOT_SUPPORTED
        elif hypothesis is HypothesisName.H2_KNOWLEDGE_OMISSION:
            valid_fraction = interval.valid_replicates / interval.requested_replicates
            if valid_fraction < self._config.min_valid_relative_bootstrap_fraction:
                return HypothesisDecision.INSUFFICIENT_EVIDENCE
            if interval.lower >= thresholds.h2_support_min_relative_reduction:
                return HypothesisDecision.SUPPORTED
            if interval.upper <= 0:
                return HypothesisDecision.NOT_SUPPORTED
        else:
            if interval.lower >= thresholds.h4_noninferiority_margin:
                return HypothesisDecision.SUPPORTED
            if interval.upper < thresholds.h4_noninferiority_margin:
                return HypothesisDecision.NOT_SUPPORTED
        return HypothesisDecision.INSUFFICIENT_EVIDENCE


def _omission_effect(
    observations: Sequence[_PairedObservation],
) -> tuple[float | None, float | None]:
    if not observations:
        return None, None
    manual = sum(item.manual for item in observations) / len(observations)
    factory = sum(item.factory for item in observations) / len(observations)
    absolute = manual - factory
    relative = None if manual == 0 else absolute / manual
    return absolute, relative


def _bounded_sha256_index(
    *,
    size: int,
    seed: int,
    hypothesis: HypothesisName,
    population: AnalysisPopulation,
    scenario: ExperimentScenario,
    replicate: int,
    draw: int,
) -> int:
    if size <= 0:
        raise AnalysisError("bootstrap stratum must contain at least one task")
    range_size = 2**64
    acceptance_limit = range_size - (range_size % size)
    for nonce in range(1_000):
        digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "seed": seed,
                    "hypothesis": hypothesis.value,
                    "population": population.value,
                    "scenario": scenario.value,
                    "replicate": replicate,
                    "draw": draw,
                    "nonce": nonce,
                }
            )
        ).digest()
        candidate = int.from_bytes(digest[:8], "big")
        if candidate < acceptance_limit:
            return candidate % size
    raise AnalysisError("bootstrap index rejection limit exceeded")


def _confidence_interval(
    values: Sequence[float],
    *,
    requested: int,
) -> ConfidenceInterval:
    ordered = sorted(values)
    valid = len(ordered)
    return ConfidenceInterval(
        lower=(None if not ordered else round(_percentile_type7(ordered, 0.025), 12)),
        upper=(None if not ordered else round(_percentile_type7(ordered, 0.975), 12)),
        requested_replicates=requested,
        valid_replicates=valid,
        invalid_replicates=requested - valid,
    )


def _percentile_type7(ordered_values: Sequence[float], probability: float) -> float:
    """Return the explicit Hyndman-Fan type-7 sample quantile."""

    if not ordered_values:
        raise AnalysisError("percentile requires at least one value")
    if probability < 0 or probability > 1:
        raise AnalysisError("percentile probability must be between zero and one")
    position = (len(ordered_values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered_values[lower_index]
    fraction = position - lower_index
    return ordered_values[lower_index] + fraction * (
        ordered_values[upper_index] - ordered_values[lower_index]
    )
