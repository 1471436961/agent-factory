"""Pure deterministic scoring from terminal runs and frozen rubrics."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.domain.common import FrozenJsonObject, sha256_model
from experiments.contracts import (
    ExperimentCondition,
    ExperimentRun,
    ExperimentTask,
    FactCheck,
    ForbiddenMatcherCheck,
    KnowledgeFixture,
    MatchExpectation,
    MetricRecord,
    PersonalizationCheck,
    PersonalizationConstraint,
    RubricDefinition,
    RunScoreRecord,
    RunStatus,
    SchemaViolation,
    TextMatcher,
)
from experiments.loader import LoadedExperimentDataset
from experiments.matching import MatcherTimeoutError, matches_text


class ScoringError(ValueError):
    """A terminal run cannot be scored against the frozen dataset."""


class DeterministicScorer:
    """Score terminal runs without model calls, clocks, or mutable state."""

    def __init__(self, dataset: LoadedExperimentDataset) -> None:
        self._dataset = dataset
        self._tasks = {task.task_id: task for task in dataset.tasks}
        self._rubrics = {rubric.rubric_id: rubric for rubric in dataset.rubrics}
        self._knowledge = {
            (item.knowledge_id, item.version): item for item in dataset.knowledge
        }

    def score(self, run: ExperimentRun) -> RunScoreRecord:
        """Return one source-bound scoring record for a terminal run."""

        task, rubric, knowledge = self._resolve_sources(run)
        common = {
            "run_id": run.run_id,
            "run_checksum": sha256_model(run),
            "experiment_id": run.experiment_id,
            "plan_checksum": run.plan_checksum,
            "condition": run.condition,
            "task_id": run.task_id,
            "scenario": task.scenario,
            "repetition": run.repetition,
            "execution_order": run.execution_order,
            "run_status": run.status,
            "rubric_id": rubric.rubric_id,
            "rubric_checksum": sha256_model(rubric),
        }
        if run.status is not RunStatus.SUCCEEDED:
            return RunScoreRecord.model_validate(
                {
                    **common,
                    "metric": MetricRecord(
                        run_id=run.run_id,
                        run_status=run.status,
                    ),
                }
            )
        if not isinstance(run.structured_output, FrozenJsonObject):
            raise ScoringError("successful run output is not an immutable JSON object")
        output = run.structured_output.to_builtin()
        violations = _schema_violations(task.output_schema, output)
        full_text = _flatten_text(output)
        fact_by_id = {item.fact_id: item for item in knowledge.facts}
        fact_checks = []
        for fact_id in rubric.required_fact_ids:
            fact = fact_by_id.get(fact_id)
            if fact is None:
                raise ScoringError("rubric references an unknown knowledge fact")
            matched_by = next(
                (
                    index
                    for index, matcher in enumerate(fact.accepted_matchers)
                    if _matches_or_raise(matcher, full_text, evidence="fact")
                ),
                None,
            )
            fact_checks.append(
                FactCheck(
                    fact_id=fact_id,
                    covered=matched_by is not None,
                    matched_by_index=matched_by,
                )
            )
        forbidden_checks = tuple(
            ForbiddenMatcherCheck(
                matcher_index=index,
                violated=_matches_or_raise(
                    matcher,
                    full_text,
                    evidence="forbidden",
                ),
            )
            for index, matcher in enumerate(rubric.forbidden_matchers)
        )
        personalization_checks = tuple(
            _personalization_check(constraint, output, full_text)
            for constraint in rubric.personalization_constraints
        )
        schema_passed = not violations
        facts_covered = sum(item.covered for item in fact_checks)
        forbidden_violated = sum(item.violated for item in forbidden_checks)
        personalization_satisfied = sum(
            item.satisfied for item in personalization_checks
        )
        quality = _quality_score(
            schema_passed=schema_passed,
            facts_covered=facts_covered,
            facts_total=len(fact_checks),
            forbidden_violated=forbidden_violated,
            forbidden_total=len(forbidden_checks),
            personalization_satisfied=personalization_satisfied,
            personalization_total=len(personalization_checks),
        )
        return RunScoreRecord.model_validate(
            {
                **common,
                "schema_passed": schema_passed,
                "schema_violations": violations,
                "fact_checks": tuple(fact_checks),
                "forbidden_checks": forbidden_checks,
                "personalization_checks": personalization_checks,
                "metric": MetricRecord(
                    run_id=run.run_id,
                    run_status=run.status,
                    schema_passed=schema_passed,
                    required_facts_total=len(fact_checks),
                    required_facts_covered=facts_covered,
                    forbidden_matchers_total=len(forbidden_checks),
                    forbidden_matchers_violated=forbidden_violated,
                    personalization_total=len(personalization_checks),
                    personalization_satisfied=personalization_satisfied,
                    deterministic_quality_score=quality,
                ),
            }
        )

    def _resolve_sources(
        self,
        run: ExperimentRun,
    ) -> tuple[ExperimentTask, RubricDefinition, KnowledgeFixture]:
        if run.experiment_id != self._dataset.definition.experiment_id:
            raise ScoringError("run belongs to another experiment")
        if run.repetition > self._dataset.definition.repetitions:
            raise ScoringError("run repetition exceeds frozen experiment")
        task = self._tasks.get(run.task_id)
        if task is None:
            raise ScoringError("run references an unknown task")
        if run.knowledge_checksum != task.knowledge.checksum:
            raise ScoringError("run knowledge checksum does not match task")
        if run.condition is ExperimentCondition.FACTORY:
            if run.agent_spec_checksum is None:
                raise ScoringError("FACTORY run lacks AgentSpec provenance")
        elif run.agent_spec_checksum is not None:
            raise ScoringError("MANUAL run cannot claim AgentSpec provenance")
        rubric = self._rubrics.get(task.rubric_id)
        if rubric is None or rubric.task_id != task.task_id:
            raise ScoringError("task rubric reference does not resolve")
        knowledge = self._knowledge.get(
            (task.knowledge.knowledge_id, task.knowledge.version)
        )
        if knowledge is None or knowledge.content_checksum != task.knowledge.checksum:
            raise ScoringError("task knowledge reference does not resolve")
        return task, rubric, knowledge


def _schema_violations(
    schema: Mapping[str, object],
    output: Mapping[str, object],
) -> tuple[SchemaViolation, ...]:
    validator = Draft202012Validator(FrozenJsonObject(schema).to_builtin())
    identities = {
        (
            _json_path(error.absolute_path),
            _json_path(error.absolute_schema_path),
            str(error.validator),
        )
        for error in validator.iter_errors(dict(output))
    }
    return tuple(
        SchemaViolation(
            instance_path=instance_path,
            schema_path=schema_path,
            validator=validator_name,
        )
        for instance_path, schema_path, validator_name in sorted(identities)
    )


def _json_path(parts: Iterable[object]) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "$" + "".join(f"/{part}" for part in escaped)


def _flatten_text(value: object) -> str:
    leaves: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key in sorted(item):
                visit(item[key])
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            leaves.append(item)
        elif item is None or isinstance(item, (bool, int, float)):
            leaves.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        else:
            raise ScoringError("structured output contains a non-JSON value")

    visit(value)
    return "\n".join(leaves)


def _matches_or_raise(
    matcher: TextMatcher,
    text: str,
    *,
    evidence: str,
) -> bool:
    try:
        return matches_text(matcher, text)
    except MatcherTimeoutError as exc:
        raise ScoringError(f"{evidence} matcher exceeded timeout") from exc


def _personalization_check(
    constraint: PersonalizationConstraint,
    output: Mapping[str, object],
    full_text: str,
) -> PersonalizationCheck:
    text = (
        full_text
        if constraint.target_field is None
        else _flatten_text(output.get(constraint.target_field, ""))
    )
    matched = _matches_or_raise(
        constraint.matcher,
        text,
        evidence="personalization",
    )
    return PersonalizationCheck(
        constraint_id=constraint.constraint_id,
        expectation=constraint.expectation,
        target_field=constraint.target_field,
        matcher_matched=matched,
        satisfied=(
            matched
            if constraint.expectation is MatchExpectation.PRESENT
            else not matched
        ),
    )


def _quality_score(
    *,
    schema_passed: bool,
    facts_covered: int,
    facts_total: int,
    forbidden_violated: int,
    forbidden_total: int,
    personalization_satisfied: int,
    personalization_total: int,
) -> float:
    components = [float(schema_passed), facts_covered / facts_total]
    if forbidden_total:
        components.append(1 - forbidden_violated / forbidden_total)
    if personalization_total:
        components.append(personalization_satisfied / personalization_total)
    return round(sum(components) / len(components), 12)
