"""Score package identity, ordering, and commit-marker validation tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import (
    ExecutionLimits,
    ExecutionManifest,
    ExperimentCondition,
    ExperimentScenario,
    GenerationConfig,
    MetricRecord,
    RunScoreRecord,
    RunStatus,
    ScoreArtifactManifest,
)
from experiments.score_artifacts import (
    ScoreArtifactCorruptionError,
    ScoreArtifactPublisher,
)

RUN_ID = UUID("7e000000-0000-0000-0000-000000000001")


def _execution_manifest() -> ExecutionManifest:
    return ExecutionManifest(
        experiment_id="score-package-test",
        dataset_checksum="a" * 64,
        plan_checksum="b" * 64,
        condition_bundle_checksum="c" * 64,
        generation=GenerationConfig(
            provider="fake-provider",
            model="fake-model",
            sdk_version="0.0.0",
            temperature=0,
            max_output_tokens=128,
            request_timeout_seconds=10,
        ),
        limits=ExecutionLimits(
            max_provider_requests=1,
            max_prompt_tokens=100,
            max_completion_tokens=128,
            prompt_tokens_per_attempt_upper_bound=100,
        ),
        manifest_checksum="d" * 64,
    )


def _score() -> RunScoreRecord:
    return RunScoreRecord(
        run_id=RUN_ID,
        run_checksum="e" * 64,
        experiment_id="score-package-test",
        plan_checksum="b" * 64,
        condition=ExperimentCondition.MANUAL,
        task_id="score-task",
        scenario=ExperimentScenario.CONSISTENCY,
        repetition=1,
        execution_order=1,
        run_status=RunStatus.PROVIDER_FAILED,
        rubric_id="score-rubric",
        rubric_checksum="f" * 64,
        metric=MetricRecord(
            run_id=RUN_ID,
            run_status=RunStatus.PROVIDER_FAILED,
        ),
    )


def test_score_publisher_rejects_empty_duplicate_and_wrong_source(
    tmp_path: Path,
) -> None:
    publisher = ScoreArtifactPublisher(ArtifactStore(tmp_path / "scores"))
    manifest = _execution_manifest()
    score = _score()

    with pytest.raises(ValueError, match="cannot be empty"):
        publisher.publish(
            execution_manifest=manifest,
            dataset_checksum=manifest.dataset_checksum,
            scores=(),
        )
    with pytest.raises(ValueError, match="dataset checksum"):
        publisher.publish(
            execution_manifest=manifest,
            dataset_checksum="0" * 64,
            scores=(score,),
        )
    with pytest.raises(ValueError, match="duplicate run IDs"):
        publisher.publish(
            execution_manifest=manifest,
            dataset_checksum=manifest.dataset_checksum,
            scores=(score, score),
        )
    with pytest.raises(ValueError, match="provenance"):
        publisher.publish(
            execution_manifest=manifest,
            dataset_checksum=manifest.dataset_checksum,
            scores=(score.model_copy(update={"experiment_id": "other-experiment"}),),
        )


def test_score_verifier_rejects_manifest_identity_and_set_checksum_tampering(
    tmp_path: Path,
) -> None:
    manifest = _execution_manifest()
    score = _score()

    identity_store = ArtifactStore(tmp_path / "identity")
    ScoreArtifactPublisher(identity_store).publish(
        execution_manifest=manifest,
        dataset_checksum=manifest.dataset_checksum,
        scores=(score,),
    )
    relative_path = (
        f"scores/{manifest.experiment_id}/{manifest.manifest_checksum}/"
        "score-manifest.json"
    )
    published = identity_store.read_model(relative_path, ScoreArtifactManifest)
    wrong_identity = published.model_copy(
        update={"execution_manifest_checksum": "9" * 64}
    )
    (identity_store.root / Path(relative_path)).write_bytes(
        canonical_model_bytes(wrong_identity)
    )
    with pytest.raises(ScoreArtifactCorruptionError, match="identity"):
        ScoreArtifactPublisher(identity_store).verify(
            manifest.experiment_id,
            manifest.manifest_checksum,
        )

    checksum_store = ArtifactStore(tmp_path / "checksum")
    ScoreArtifactPublisher(checksum_store).publish(
        execution_manifest=manifest,
        dataset_checksum=manifest.dataset_checksum,
        scores=(score,),
    )
    published = checksum_store.read_model(relative_path, ScoreArtifactManifest)
    wrong_checksum = published.model_copy(update={"score_set_checksum": "8" * 64})
    (checksum_store.root / Path(relative_path)).write_bytes(
        canonical_model_bytes(wrong_checksum)
    )
    with pytest.raises(ScoreArtifactCorruptionError, match="set checksum"):
        ScoreArtifactPublisher(checksum_store).verify(
            manifest.experiment_id,
            manifest.manifest_checksum,
        )


def test_score_verifier_rejects_unreferenced_record(tmp_path: Path) -> None:
    manifest = _execution_manifest()
    store = ArtifactStore(tmp_path / "extra")
    publisher = ScoreArtifactPublisher(store)
    publisher.publish(
        execution_manifest=manifest,
        dataset_checksum=manifest.dataset_checksum,
        scores=(_score(),),
    )
    package_root = f"scores/{manifest.experiment_id}/{manifest.manifest_checksum}"
    store.write_bytes_once(f"{package_root}/records/extra.json", b"{}\n")

    with pytest.raises(ScoreArtifactCorruptionError, match="artifact set"):
        publisher.verify(manifest.experiment_id, manifest.manifest_checksum)
