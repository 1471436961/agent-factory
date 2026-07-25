"""Deterministic M5 execution-plan and execution-manifest construction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import ValidationError

from agent_factory.domain.common import canonical_json_bytes, sha256_model
from experiments.contracts import (
    ExecutionLimits,
    ExecutionManifest,
    ExecutionPlan,
    ExecutionPlanItem,
    ExperimentCondition,
    GenerationConfig,
)
from experiments.loader import ExperimentFixtureError, LoadedExperimentDataset

PLAN_NAMESPACE = UUID("75cf45d7-04d8-53e5-b129-a840ee31d323")
_MAX_PLAN_BYTES = 2 * 1024 * 1024


def build_execution_plan(dataset: LoadedExperimentDataset) -> ExecutionPlan:
    """Create the complete language-independent pseudo-random run order."""

    definition = dataset.definition
    coordinates = [
        (condition, task.task_id, repetition)
        for condition in definition.conditions
        for task in dataset.tasks
        for repetition in range(1, definition.repetitions + 1)
    ]
    coordinates.sort(
        key=lambda coordinate: (
            _coordinate_priority(definition.randomization_seed, *coordinate),
            coordinate[0].value,
            coordinate[1],
            coordinate[2],
        )
    )
    items = tuple(
        ExecutionPlanItem(
            run_id=run_id_for(
                definition.experiment_id,
                condition,
                task_id,
                repetition,
            ),
            condition=condition,
            task_id=task_id,
            repetition=repetition,
            execution_order=order,
        )
        for order, (condition, task_id, repetition) in enumerate(
            coordinates,
            start=1,
        )
    )
    unsigned = ExecutionPlan(
        experiment_id=definition.experiment_id,
        definition_checksum=sha256_model(definition),
        randomization_seed=definition.randomization_seed,
        items=items,
        plan_checksum="0" * 64,
    )
    return unsigned.model_copy(
        update={"plan_checksum": calculate_plan_checksum(unsigned)}
    )


def run_id_for(
    experiment_id: str,
    condition: ExperimentCondition,
    task_id: str,
    repetition: int,
) -> UUID:
    """Derive run identity without depending on execution order."""

    coordinate = f"{experiment_id}\x00{condition.value}\x00{task_id}\x00{repetition}"
    return uuid5(PLAN_NAMESPACE, coordinate)


def calculate_plan_checksum(plan: ExecutionPlan) -> str:
    """Hash all plan fields except the self-referential checksum."""

    return sha256_model(plan, exclude={"plan_checksum"})


def validate_execution_plan(
    plan: ExecutionPlan,
    dataset: LoadedExperimentDataset,
) -> None:
    """Reject stale, incomplete, reordered, or identity-inconsistent plans."""

    expected = build_execution_plan(dataset)
    if plan != expected:
        raise ExperimentFixtureError("execution plan does not match frozen dataset")
    if calculate_plan_checksum(plan) != plan.plan_checksum:
        raise ExperimentFixtureError("execution plan checksum mismatch")


def plan_json_bytes(plan: ExecutionPlan) -> bytes:
    """Return canonical, newline-terminated plan bytes for review and storage."""

    payload = plan.model_dump(mode="json", exclude_none=False)
    return canonical_json_bytes(payload) + b"\n"


def load_execution_plan(
    path: Path,
    dataset: LoadedExperimentDataset,
) -> ExecutionPlan:
    """Load a bounded JSON plan and validate it against the current dataset."""

    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ExperimentFixtureError(
            "execution plan cannot be read",
            path=path,
        ) from exc
    if len(content) > _MAX_PLAN_BYTES:
        raise ExperimentFixtureError("execution plan exceeds byte limit", path=path)
    try:
        raw = json.loads(content)
        plan = ExecutionPlan.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExperimentFixtureError("execution plan is invalid", path=path) from exc
    validate_execution_plan(plan, dataset)
    if content != plan_json_bytes(plan):
        raise ExperimentFixtureError("execution plan is not canonical", path=path)
    return plan


def build_execution_manifest(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    condition_bundle_checksum: str,
    generation: GenerationConfig,
    limits: ExecutionLimits,
) -> ExecutionManifest:
    """Bind one technical execution identity without claiming formal M5.5 freeze."""

    validate_execution_plan(plan, dataset)
    unsigned = ExecutionManifest(
        experiment_id=dataset.definition.experiment_id,
        dataset_checksum=dataset.dataset_checksum,
        plan_checksum=plan.plan_checksum,
        condition_bundle_checksum=condition_bundle_checksum,
        generation=generation,
        limits=limits,
        manifest_checksum="0" * 64,
    )
    return unsigned.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(unsigned)}
    )


def calculate_manifest_checksum(manifest: ExecutionManifest) -> str:
    """Hash an execution identity except its self-referential checksum."""

    return sha256_model(manifest, exclude={"manifest_checksum"})


def validate_execution_manifest(
    manifest: ExecutionManifest,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
) -> None:
    """Validate recovery identity before any provider attempt begins."""

    if manifest.experiment_id != dataset.definition.experiment_id:
        raise ExperimentFixtureError("manifest experiment identity mismatch")
    if manifest.dataset_checksum != dataset.dataset_checksum:
        raise ExperimentFixtureError("manifest dataset checksum mismatch")
    if manifest.plan_checksum != plan.plan_checksum:
        raise ExperimentFixtureError("manifest plan checksum mismatch")
    if calculate_manifest_checksum(manifest) != manifest.manifest_checksum:
        raise ExperimentFixtureError("execution manifest checksum mismatch")


def _coordinate_priority(
    seed: int,
    condition: ExperimentCondition,
    task_id: str,
    repetition: int,
) -> bytes:
    payload = canonical_json_bytes(
        {
            "seed": seed,
            "condition": condition.value,
            "task_id": task_id,
            "repetition": repetition,
        }
    )
    return hashlib.sha256(payload).digest()
