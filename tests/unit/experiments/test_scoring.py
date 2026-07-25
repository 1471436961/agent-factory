"""Deterministic scoring over terminal runs and frozen Writer rubrics."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from experiments.contracts import (
    AttemptStatus,
    ExperimentCondition,
    ExperimentRun,
    GenerationConfig,
    RunAttempt,
    RunStatus,
)
from experiments.loader import LoadedExperimentDataset
from experiments.scoring import DeterministicScorer, ScoringError

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
RUN_NAMESPACE = UUID("7308e38d-8376-58fb-85f5-91c8d896a265")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _generation() -> GenerationConfig:
    return GenerationConfig(
        provider="fake-provider",
        model="fake-writer-v1",
        sdk_version="0.0.0",
        temperature=0,
        max_output_tokens=512,
        request_timeout_seconds=30,
    )


def _run(
    dataset: LoadedExperimentDataset,
    *,
    task_id: str,
    output: dict[str, object] | None,
    status: RunStatus = RunStatus.SUCCEEDED,
    condition: ExperimentCondition = ExperimentCondition.MANUAL,
) -> ExperimentRun:
    task = next(item for item in dataset.tasks if item.task_id == task_id)
    run_id = uuid5(RUN_NAMESPACE, f"{task_id}:{condition.value}:{status.value}")
    common = {
        "run_id": run_id,
        "experiment_id": dataset.definition.experiment_id,
        "manifest_checksum": SHA_B,
        "plan_checksum": SHA_A,
        "condition": condition,
        "task_id": task_id,
        "repetition": 1,
        "execution_order": 1,
        "generation": _generation(),
        "invocation": {"instructions": "Write.", "task_input": "Frozen task."},
        "prompt_hash": SHA_A,
        "knowledge_checksum": task.knowledge.checksum,
        "agent_spec_checksum": SHA_B
        if condition is ExperimentCondition.FACTORY
        else None,
        "status": status,
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
    }
    if status is RunStatus.BUDGET_STOPPED:
        return ExperimentRun.model_validate(
            {**common, "attempts": [], "output_text": None, "structured_output": None}
        )
    assert output is not None
    output_text = json.dumps(output, sort_keys=True, separators=(",", ":"))
    attempt = RunAttempt(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        provider_request_id="fake-request-1",
        response={"output": output},
        output_text=output_text,
        structured_output=output,
        prompt_tokens=100,
        completion_tokens=40,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    return ExperimentRun.model_validate(
        {
            **common,
            "attempts": [attempt],
            "output_text": output_text,
            "structured_output": output,
        }
    )


def _valid_reference_output() -> dict[str, object]:
    return {
        "title": "Nexora current integration reference",
        "summary": "Send batches with POST /v2/events and at most 80 events.",
        "key_points": [
            "Use X-Nexora-Event-ID for duplicate protection.",
            "Exclude legacy limits.",
        ],
        "next_action": "Configure the current endpoint and event ID header.",
    }


def test_scores_schema_facts_forbidden_and_quality_from_real_fixture(
    dataset: LoadedExperimentDataset,
) -> None:
    run = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
    )
    scorer = DeterministicScorer(dataset)

    score = scorer.score(run)

    assert score.schema_passed is True
    assert [item.covered for item in score.fact_checks] == [True, True, True]
    assert [item.matched_by_index for item in score.fact_checks] == [0, 0, 0]
    assert [item.violated for item in score.forbidden_checks] == [False]
    assert score.metric.deterministic_quality_score == 1
    assert score.run_checksum == scorer.score(run).run_checksum
    assert score == scorer.score(run)


def test_schema_failure_and_legacy_value_remain_scored_evidence(
    dataset: LoadedExperimentDataset,
) -> None:
    output = _valid_reference_output()
    output["summary"] = (
        "Use POST /v2/events with at most 80 events; the legacy text says "
        "100 events per batch."
    )
    output["key_points"] = ["Use X-Nexora-Event-ID."]
    output.pop("next_action")

    score = DeterministicScorer(dataset).score(
        _run(
            dataset,
            task_id="nexora-integration-reference",
            output=output,
        )
    )

    assert score.run_status is RunStatus.SUCCEEDED
    assert score.schema_passed is False
    assert {item.validator for item in score.schema_violations} == {
        "minItems",
        "required",
    }
    assert score.forbidden_checks[0].violated is True
    assert score.metric.deterministic_quality_score == 0.333333333333


def test_scalar_json_values_are_flattened_without_changing_fact_evidence(
    dataset: LoadedExperimentDataset,
) -> None:
    output = _valid_reference_output()
    output["metadata"] = {"attempts": 3, "enabled": True, "note": None}

    score = DeterministicScorer(dataset).score(
        _run(
            dataset,
            task_id="nexora-integration-reference",
            output=output,
        )
    )

    assert score.schema_passed is False
    assert [item.covered for item in score.fact_checks] == [True, True, True]


def test_personalization_respects_target_field_and_absent_expectation(
    dataset: LoadedExperimentDataset,
) -> None:
    output: dict[str, object] = {
        "title": "Risk: Nexora reliability brief",
        "summary": (
            "Retries occur after 10 seconds, 30 seconds, and 120 seconds. "
            "Use X-Nexora-Event-ID, retained for 36 hours."
        ),
        "key_points": ["Risk appears only in the title.", "Keep current values."],
        "next_action": "Add monitoring for duplicate protection.",
    }

    score = DeterministicScorer(dataset).score(
        _run(dataset, task_id="nexora-lead-brief", output=output)
    )

    assert [item.satisfied for item in score.personalization_checks] == [False, True]
    assert score.metric.personalization_satisfied == 1
    assert score.metric.deterministic_quality_score == 0.875

    beginner: dict[str, object] = {
        "title": "Nexora beginner guide",
        "summary": "In practice, POST /v2/events accepts at most 80 events.",
        "key_points": ["Use the current endpoint.", "Avoid legacy limits."],
        "next_action": "Send a small test batch.",
    }
    beginner_score = DeterministicScorer(dataset).score(
        _run(dataset, task_id="nexora-beginner-guide", output=beginner)
    )
    assert [item.satisfied for item in beginner_score.personalization_checks] == [
        True,
        True,
    ]


def test_execution_failure_has_no_deterministic_checks(
    dataset: LoadedExperimentDataset,
) -> None:
    run = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=None,
        status=RunStatus.BUDGET_STOPPED,
    )

    score = DeterministicScorer(dataset).score(run)

    assert score.schema_passed is None
    assert score.fact_checks == ()
    assert score.metric.deterministic_quality_score is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("experiment_id", "another-experiment", "another experiment"),
        ("task_id", "unknown-writer-task", "unknown task"),
        ("knowledge_checksum", SHA_A, "knowledge checksum"),
        ("repetition", 6, "repetition exceeds"),
    ],
)
def test_rejects_run_source_identity_mismatch(
    dataset: LoadedExperimentDataset,
    field: str,
    value: object,
    message: str,
) -> None:
    run = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
    ).model_copy(update={field: value})

    with pytest.raises(ScoringError, match=message):
        DeterministicScorer(dataset).score(run)


def test_rejects_condition_provenance_mismatch(
    dataset: LoadedExperimentDataset,
) -> None:
    manual = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
    ).model_copy(update={"agent_spec_checksum": SHA_B})
    with pytest.raises(ScoringError, match="MANUAL"):
        DeterministicScorer(dataset).score(manual)

    factory = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
        condition=ExperimentCondition.FACTORY,
    ).model_copy(update={"agent_spec_checksum": None})
    with pytest.raises(ScoringError, match="FACTORY"):
        DeterministicScorer(dataset).score(factory)


def test_scoring_surfaces_regex_timeout(
    dataset: LoadedExperimentDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr("experiments.matching.regex.search", raise_timeout)
    run = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
    )

    with pytest.raises(ScoringError, match="fact matcher exceeded timeout"):
        DeterministicScorer(dataset).score(run)


def test_rejects_missing_rubric_knowledge_and_required_fact_sources(
    dataset: LoadedExperimentDataset,
) -> None:
    run = _run(
        dataset,
        task_id="nexora-integration-reference",
        output=_valid_reference_output(),
    )
    task = next(
        item for item in dataset.tasks if item.task_id == "nexora-integration-reference"
    )

    missing_rubric = replace(
        dataset,
        rubrics=tuple(
            item for item in dataset.rubrics if item.rubric_id != task.rubric_id
        ),
    )
    with pytest.raises(ScoringError, match="rubric reference"):
        DeterministicScorer(missing_rubric).score(run)

    missing_knowledge = replace(
        dataset,
        knowledge=tuple(
            item
            for item in dataset.knowledge
            if item.knowledge_id != task.knowledge.knowledge_id
        ),
    )
    with pytest.raises(ScoringError, match="knowledge reference"):
        DeterministicScorer(missing_knowledge).score(run)

    changed_knowledge = tuple(
        (
            item.model_copy(
                update={
                    "facts": tuple(
                        fact
                        for fact in item.facts
                        if fact.fact_id != "ingestion-endpoint"
                    )
                }
            )
            if item.knowledge_id == task.knowledge.knowledge_id
            else item
        )
        for item in dataset.knowledge
    )
    missing_fact = replace(dataset, knowledge=changed_knowledge)
    with pytest.raises(ScoringError, match="unknown knowledge fact"):
        DeterministicScorer(missing_fact).score(run)
