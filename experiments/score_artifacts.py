"""Manifest-last publication of deterministic M5 score evidence."""

from __future__ import annotations

from collections.abc import Iterable

from agent_factory.domain.common import sha256_model
from experiments.analysis import calculate_score_set_checksum
from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import (
    ExecutionManifest,
    RunScoreRecord,
    ScoreArtifactManifest,
    ScoreArtifactRecord,
)

_MANIFEST_PATH = "score-manifest.json"


class ScoreArtifactCorruptionError(RuntimeError):
    """A score package is incomplete or inconsistent with its commit marker."""


class ScoreArtifactPublisher:
    """Publish and verify source-bound scores before statistical analysis."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def publish(
        self,
        *,
        execution_manifest: ExecutionManifest,
        dataset_checksum: str,
        scores: Iterable[RunScoreRecord],
    ) -> ScoreArtifactManifest:
        """Publish every score first and the score manifest as the commit marker."""

        ordered = _ordered_scores(tuple(scores))
        if dataset_checksum != execution_manifest.dataset_checksum:
            raise ValueError("dataset checksum does not match execution manifest")
        if any(
            score.experiment_id != execution_manifest.experiment_id
            or score.plan_checksum != execution_manifest.plan_checksum
            for score in ordered
        ):
            raise ValueError("score provenance does not match execution manifest")
        references = tuple(
            ScoreArtifactRecord(
                run_id=score.run_id,
                execution_order=score.execution_order,
                path=_record_path(score),
                run_checksum=score.run_checksum,
                score_checksum=sha256_model(score),
                byte_size=len(canonical_model_bytes(score)),
            )
            for score in ordered
        )
        manifest = ScoreArtifactManifest(
            experiment_id=execution_manifest.experiment_id,
            dataset_checksum=dataset_checksum,
            plan_checksum=execution_manifest.plan_checksum,
            execution_manifest_checksum=execution_manifest.manifest_checksum,
            score_set_checksum=calculate_score_set_checksum(ordered),
            run_count=len(ordered),
            records=references,
        )
        package_root = _package_root(
            execution_manifest.experiment_id,
            execution_manifest.manifest_checksum,
        )
        for score, reference in zip(ordered, references, strict=True):
            self._store.write_model_once(
                f"{package_root}/{reference.path}",
                score,
            )
        self._store.write_model_once(
            f"{package_root}/{_MANIFEST_PATH}",
            manifest,
        )
        self.verify(
            execution_manifest.experiment_id,
            execution_manifest.manifest_checksum,
        )
        return manifest

    def verify(
        self,
        experiment_id: str,
        execution_manifest_checksum: str,
    ) -> tuple[RunScoreRecord, ...]:
        """Verify the commit marker and each canonical score record."""

        package_root = _package_root(experiment_id, execution_manifest_checksum)
        manifest = self._store.read_model(
            f"{package_root}/{_MANIFEST_PATH}",
            ScoreArtifactManifest,
        )
        if (
            manifest.experiment_id != experiment_id
            or manifest.execution_manifest_checksum != execution_manifest_checksum
        ):
            raise ScoreArtifactCorruptionError(
                "score manifest identity does not match package path"
            )
        expected_paths = {
            f"{package_root}/{_MANIFEST_PATH}",
            *(f"{package_root}/{reference.path}" for reference in manifest.records),
        }
        if set(self._store.list_files(package_root)) != expected_paths:
            raise ScoreArtifactCorruptionError(
                "score artifact set does not match manifest"
            )
        scores: list[RunScoreRecord] = []
        for reference in manifest.records:
            relative_path = f"{package_root}/{reference.path}"
            content = self._store.read_bytes(relative_path)
            score = self._store.read_model(relative_path, RunScoreRecord)
            if (
                len(content) != reference.byte_size
                or score.run_id != reference.run_id
                or score.execution_order != reference.execution_order
                or score.run_checksum != reference.run_checksum
                or sha256_model(score) != reference.score_checksum
                or score.experiment_id != manifest.experiment_id
                or score.plan_checksum != manifest.plan_checksum
            ):
                raise ScoreArtifactCorruptionError(
                    f"score artifact mismatch: {reference.path}"
                )
            scores.append(score)
        ordered = tuple(scores)
        if calculate_score_set_checksum(ordered) != manifest.score_set_checksum:
            raise ScoreArtifactCorruptionError("score set checksum mismatch")
        return ordered


def _ordered_scores(scores: tuple[RunScoreRecord, ...]) -> tuple[RunScoreRecord, ...]:
    if not scores:
        raise ValueError("score package cannot be empty")
    ordered = tuple(sorted(scores, key=lambda item: item.execution_order))
    run_ids = [score.run_id for score in ordered]
    orders = [score.execution_order for score in ordered]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("score package contains duplicate run IDs")
    if orders != list(range(1, len(ordered) + 1)):
        raise ValueError("score package execution order is incomplete")
    return ordered


def _record_path(score: RunScoreRecord) -> str:
    return f"records/{score.run_id}.json"


def _package_root(experiment_id: str, execution_manifest_checksum: str) -> str:
    return f"scores/{experiment_id}/{execution_manifest_checksum}"
