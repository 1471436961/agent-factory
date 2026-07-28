"""Recoverable generation of condition-free human review artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from agent_factory.domain.common import sha256_model
from experiments.artifacts import (
    ArtifactStore,
    ArtifactStoreError,
    canonical_model_bytes,
)
from experiments.contracts import (
    BlindReviewArtifact,
    BlindReviewItem,
    BlindReviewMapping,
    BlindReviewMappingRecord,
    BlindReviewPackageManifest,
    ExecutionPlan,
    ExperimentRun,
)
from experiments.evidence import ExperimentEvidenceLoader
from experiments.loader import LoadedExperimentDataset


class BlindReviewError(RuntimeError):
    """Blind review artifacts cannot be generated without provenance leakage."""


@dataclass(frozen=True, slots=True)
class BlindReviewBuildResult:
    package: BlindReviewPackageManifest
    mapping: BlindReviewMapping


def build_blind_review_package(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    run_store: ArtifactStore,
    review_root: Path,
    mapping_root: Path,
    id_factory: Callable[[], UUID] = uuid4,
) -> BlindReviewBuildResult:
    """Publish public review items separately from their private condition map."""

    evidence = ExperimentEvidenceLoader(
        dataset=dataset,
        plan=plan,
        store=run_store,
    ).load()
    _validate_separate_roots(review_root, mapping_root)
    review_store = ArtifactStore(review_root)
    mapping_store = ArtifactStore(mapping_root)
    mapping_path = _mapping_path(plan.experiment_id)
    runs = {run.run_id: run for run in evidence.runs}

    if mapping_store.exists(mapping_path):
        mapping = mapping_store.read_model(mapping_path, BlindReviewMapping)
        items = _items_from_existing_mapping(mapping, dataset, plan, runs)
    else:
        items, mapping = _create_items_and_mapping(
            dataset=dataset,
            plan=plan,
            execution_manifest_checksum=evidence.manifest.manifest_checksum,
            runs=runs,
            id_factory=id_factory,
        )
        mapping_store.write_model_once(mapping_path, mapping)
    _validate_mapping_tree(mapping_store, mapping_path)

    artifacts: list[BlindReviewArtifact] = []
    for item in sorted(items, key=lambda value: str(value.review_item_id)):
        path = _item_path(plan.experiment_id, item.review_item_id)
        content = canonical_model_bytes(item)
        review_store.write_bytes_once(path, content)
        artifacts.append(
            BlindReviewArtifact(
                review_item_id=item.review_item_id,
                path=path,
                byte_size=len(content),
                content_checksum=hashlib.sha256(content).hexdigest(),
            )
        )
    unsigned = BlindReviewPackageManifest(
        experiment_id=plan.experiment_id,
        execution_manifest_checksum=evidence.manifest.manifest_checksum,
        plan_checksum=plan.plan_checksum,
        mapping_checksum=mapping.mapping_checksum,
        item_count=len(items),
        files=tuple(artifacts),
        package_checksum="0" * 64,
    )
    package = unsigned.model_copy(
        update={"package_checksum": _package_checksum(unsigned)}
    )
    manifest_path = _package_path(plan.experiment_id)
    review_store.write_model_once(manifest_path, package)
    _validate_review_tree(review_store, package, manifest_path)
    return BlindReviewBuildResult(package=package, mapping=mapping)


def _create_items_and_mapping(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    execution_manifest_checksum: str,
    runs: dict[UUID, ExperimentRun],
    id_factory: Callable[[], UUID],
) -> tuple[tuple[BlindReviewItem, ...], BlindReviewMapping]:
    items: list[BlindReviewItem] = []
    records: list[BlindReviewMappingRecord] = []
    for coordinate in sorted(plan.items, key=lambda value: value.execution_order):
        run = runs[coordinate.run_id]
        review_id = id_factory()
        item = _build_item(review_id, run, dataset)
        items.append(item)
        records.append(
            BlindReviewMappingRecord(
                review_item_id=review_id,
                review_item_checksum=sha256_model(item),
                run_id=run.run_id,
                run_checksum=sha256_model(run),
                condition=run.condition,
                task_id=run.task_id,
                repetition=run.repetition,
                execution_order=run.execution_order,
            )
        )
    unsigned = BlindReviewMapping(
        experiment_id=plan.experiment_id,
        execution_manifest_checksum=execution_manifest_checksum,
        plan_checksum=plan.plan_checksum,
        records=tuple(records),
        mapping_checksum="0" * 64,
    )
    mapping = unsigned.model_copy(
        update={"mapping_checksum": _mapping_checksum(unsigned)}
    )
    return tuple(items), mapping


def _items_from_existing_mapping(
    mapping: BlindReviewMapping,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    runs: dict[UUID, ExperimentRun],
) -> tuple[BlindReviewItem, ...]:
    execution_manifest_checksums = {run.manifest_checksum for run in runs.values()}
    if (
        mapping.mapping_checksum != _mapping_checksum(mapping)
        or mapping.experiment_id != plan.experiment_id
        or mapping.plan_checksum != plan.plan_checksum
        or execution_manifest_checksums != {mapping.execution_manifest_checksum}
        or len(mapping.records) != len(plan.items)
    ):
        raise BlindReviewError("blind review mapping identity is stale")
    items: list[BlindReviewItem] = []
    for coordinate, record in zip(
        sorted(plan.items, key=lambda value: value.execution_order),
        mapping.records,
        strict=True,
    ):
        try:
            run = runs[record.run_id]
        except KeyError as exc:
            raise BlindReviewError(
                "blind review mapping references unknown run"
            ) from exc
        item = _build_item(record.review_item_id, run, dataset)
        if (
            record.run_id != coordinate.run_id
            or record.run_checksum != sha256_model(run)
            or record.condition is not run.condition
            or record.task_id != run.task_id
            or record.repetition != run.repetition
            or record.execution_order != run.execution_order
            or record.review_item_checksum != sha256_model(item)
        ):
            raise BlindReviewError("blind review mapping provenance is stale")
        items.append(item)
    return tuple(items)


def _build_item(
    review_item_id: UUID,
    run: ExperimentRun,
    dataset: LoadedExperimentDataset,
) -> BlindReviewItem:
    tasks = {item.task_id: item for item in dataset.tasks}
    rubrics = {item.rubric_id: item for item in dataset.rubrics}
    knowledge = {item.domain_id: item for item in dataset.knowledge}
    task = tasks[run.task_id]
    rubric = rubrics[task.rubric_id]
    facts_by_id = {fact.fact_id: fact for fact in knowledge[task.domain_id].facts}
    return BlindReviewItem(
        review_item_id=review_item_id,
        task_id=task.task_id,
        scenario=task.scenario,
        instruction=task.instruction,
        reader_profile=task.reader_profile,
        run_status=run.status,
        output_text=run.output_text,
        structured_output=run.structured_output,
        required_facts=tuple(
            facts_by_id[fact_id] for fact_id in rubric.required_fact_ids
        ),
        rubric=rubric,
    )


def _validate_separate_roots(review_root: Path, mapping_root: Path) -> None:
    review = review_root.resolve(strict=False)
    mapping = mapping_root.resolve(strict=False)
    if (
        review == mapping
        or review.is_relative_to(mapping)
        or mapping.is_relative_to(review)
    ):
        raise BlindReviewError(
            "review package and condition mapping require separate roots"
        )


def _validate_mapping_tree(store: ArtifactStore, mapping_path: str) -> None:
    if set(store.list_files("mappings")) != {mapping_path}:
        raise BlindReviewError("condition mapping root contains unexpected artifacts")


def _validate_review_tree(
    store: ArtifactStore,
    package: BlindReviewPackageManifest,
    manifest_path: str,
) -> None:
    expected = {manifest_path, *(item.path for item in package.files)}
    if set(store.list_files(f"reviews/{package.experiment_id}")) != expected:
        raise BlindReviewError("blind review package contains unexpected artifacts")
    try:
        loaded = store.read_model(manifest_path, BlindReviewPackageManifest)
    except ArtifactStoreError as exc:
        raise BlindReviewError("blind review package Manifest is invalid") from exc
    if loaded != package or package.package_checksum != _package_checksum(package):
        raise BlindReviewError("blind review package Manifest identity is stale")


def _mapping_checksum(mapping: BlindReviewMapping) -> str:
    return sha256_model(mapping, exclude={"mapping_checksum"})


def _package_checksum(package: BlindReviewPackageManifest) -> str:
    return sha256_model(package, exclude={"package_checksum"})


def _mapping_path(experiment_id: str) -> str:
    return f"mappings/{experiment_id}.json"


def _package_path(experiment_id: str) -> str:
    return f"reviews/{experiment_id}/manifest.json"


def _item_path(experiment_id: str, review_item_id: UUID) -> str:
    return f"reviews/{experiment_id}/items/{review_item_id}.json"
