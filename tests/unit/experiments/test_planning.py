"""Deterministic execution-plan and technical manifest tests."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from experiments.contracts import (
    ExecutionLimits,
    ExperimentCondition,
    GenerationConfig,
)
from experiments.loader import ExperimentFixtureError, LoadedExperimentDataset
from experiments.planning import (
    build_execution_manifest,
    build_execution_plan,
    calculate_manifest_checksum,
    calculate_plan_checksum,
    load_execution_plan,
    plan_json_bytes,
    run_id_for,
    validate_execution_manifest,
)

CONDITION_CHECKSUM = "c" * 64
EXPECTED_PLAN_CHECKSUM = (
    "81c535b96bcd3b33ea217dd031953a7f7fc6ae586c995172956324b2b7b7996f"
)


def _generation(*, concurrency: int = 1) -> GenerationConfig:
    return GenerationConfig(
        provider="fake-provider",
        model="fake-writer-v1",
        sdk_version="0.0.0",
        temperature=0,
        max_output_tokens=512,
        request_timeout_seconds=30,
        concurrency=concurrency,
    )


def _limits() -> ExecutionLimits:
    return ExecutionLimits(
        max_provider_requests=720,
        max_prompt_tokens=1_000_000,
        max_completion_tokens=400_000,
        prompt_tokens_per_attempt_upper_bound=4_000,
    )


def test_plan_is_complete_deterministic_and_self_checksummed(
    dataset: LoadedExperimentDataset,
) -> None:
    first = build_execution_plan(dataset)
    second = build_execution_plan(dataset)

    assert first == second
    assert len(first.items) == 240
    assert first.plan_checksum == EXPECTED_PLAN_CHECKSUM
    assert first.plan_checksum == calculate_plan_checksum(first)
    assert [item.execution_order for item in first.items] == list(range(1, 241))
    assert len({item.run_id for item in first.items}) == 240

    conditions = Counter(item.condition for item in first.items)
    assert conditions == {
        ExperimentCondition.MANUAL: 120,
        ExperimentCondition.FACTORY: 120,
    }
    coordinates = Counter((item.condition, item.task_id) for item in first.items)
    assert set(coordinates.values()) == {5}


def test_run_identity_does_not_depend_on_execution_order() -> None:
    first = run_id_for(
        "writer-validation-v1",
        ExperimentCondition.FACTORY,
        "nexora-beginner-guide",
        2,
    )
    second = run_id_for(
        "writer-validation-v1",
        ExperimentCondition.FACTORY,
        "nexora-beginner-guide",
        2,
    )

    assert first == second
    assert first != run_id_for(
        "writer-validation-v1",
        ExperimentCondition.FACTORY,
        "nexora-beginner-guide",
        3,
    )


def test_plan_loader_requires_canonical_exact_plan(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    plan = build_execution_plan(dataset)
    path = tmp_path / "execution-plan.json"
    path.write_bytes(plan_json_bytes(plan))

    assert load_execution_plan(path, dataset) == plan

    tampered = plan.model_copy(
        update={
            "items": (
                plan.items[0].model_copy(update={"execution_order": 2}),
                plan.items[1].model_copy(update={"execution_order": 1}),
                *plan.items[2:],
            )
        }
    )
    path.write_bytes(plan_json_bytes(tampered))
    with pytest.raises(ExperimentFixtureError, match="does not match"):
        load_execution_plan(path, dataset)

    path.write_bytes(plan_json_bytes(plan).replace(b"\n", b"", 1))
    with pytest.raises(ExperimentFixtureError, match="not canonical"):
        load_execution_plan(path, dataset)


def test_plan_loader_rejects_missing_invalid_and_oversized_files(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ExperimentFixtureError, match="cannot be read"):
        load_execution_plan(missing, dataset)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ExperimentFixtureError, match="is invalid"):
        load_execution_plan(invalid, dataset)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ExperimentFixtureError, match="exceeds byte limit"):
        load_execution_plan(oversized, dataset)


def test_manifest_binds_dataset_plan_conditions_generation_and_limits(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    manifest = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=CONDITION_CHECKSUM,
        generation=_generation(),
        limits=_limits(),
    )

    validate_execution_manifest(manifest, dataset, plan)
    assert manifest.manifest_checksum == calculate_manifest_checksum(manifest)

    changed = manifest.model_copy(update={"condition_bundle_checksum": "d" * 64})
    with pytest.raises(ExperimentFixtureError, match="manifest checksum"):
        validate_execution_manifest(changed, dataset, plan)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("experiment_id", "another-experiment", "experiment identity"),
        ("dataset_checksum", "d" * 64, "dataset checksum"),
        ("plan_checksum", "e" * 64, "plan checksum"),
    ],
)
def test_manifest_rejects_each_recovery_identity_mismatch(
    dataset: LoadedExperimentDataset,
    field: str,
    value: str,
    message: str,
) -> None:
    plan = build_execution_plan(dataset)
    manifest = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=CONDITION_CHECKSUM,
        generation=_generation(),
        limits=_limits(),
    )
    changed = manifest.model_copy(update={field: value})
    changed = changed.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(changed)}
    )

    with pytest.raises(ExperimentFixtureError, match=message):
        validate_execution_manifest(changed, dataset, plan)


def test_manifest_rejects_unsupported_concurrency_and_impossible_reservation(
    dataset: LoadedExperimentDataset,
) -> None:
    plan = build_execution_plan(dataset)
    with pytest.raises(ValueError, match="concurrency=1"):
        build_execution_manifest(
            dataset=dataset,
            plan=plan,
            condition_bundle_checksum=CONDITION_CHECKSUM,
            generation=_generation(concurrency=2),
            limits=_limits(),
        )

    with pytest.raises(ValueError, match="reservation exceeds"):
        ExecutionLimits(
            max_provider_requests=1,
            max_prompt_tokens=100,
            max_completion_tokens=100,
            prompt_tokens_per_attempt_upper_bound=101,
        )
