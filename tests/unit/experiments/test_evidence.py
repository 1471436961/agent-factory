"""Read-only completeness and provenance checks for execution evidence."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import AttemptIntent
from experiments.evidence import ExperimentEvidenceError, ExperimentEvidenceLoader
from experiments.loader import LoadedExperimentDataset
from experiments.planning import load_execution_plan

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3] / "experiments" / "definitions" / "writer-v1"
)


def _copy_runs(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def _loader(
    dataset: LoadedExperimentDataset,
    root: Path,
) -> ExperimentEvidenceLoader:
    plan = load_execution_plan(FIXTURE_ROOT / "execution-plan.json", dataset)
    return ExperimentEvidenceLoader(
        dataset=dataset,
        plan=plan,
        store=ArtifactStore(root),
    )


def test_complete_evidence_load_is_plan_ordered_and_read_only(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
) -> None:
    store = ArtifactStore(completed_fake_run_root)
    paths = store.list_files(dataset.definition.experiment_id)
    before = {
        path: hashlib.sha256(store.read_bytes(path)).hexdigest() for path in paths
    }

    evidence = _loader(dataset, completed_fake_run_root).load()

    assert len(evidence.runs) == 240
    assert tuple(run.execution_order for run in evidence.runs) == tuple(range(1, 241))
    assert store.list_files(dataset.definition.experiment_id) == paths
    assert {
        path: hashlib.sha256(store.read_bytes(path)).hexdigest() for path in paths
    } == before


def test_evidence_rejects_missing_and_unexpected_plan_files(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    missing_root = _copy_runs(completed_fake_run_root, tmp_path / "missing")
    plan = load_execution_plan(FIXTURE_ROOT / "execution-plan.json", dataset)
    missing = (
        missing_root
        / dataset.definition.experiment_id
        / "terminal"
        / f"{plan.items[0].run_id}.json"
    )
    missing.unlink()
    with pytest.raises(ExperimentEvidenceError, match=r"required.*missing"):
        _loader(dataset, missing_root).load()

    extra_root = _copy_runs(completed_fake_run_root, tmp_path / "extra")
    extra = extra_root / dataset.definition.experiment_id / "terminal" / "extra.json"
    extra.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ExperimentEvidenceError, match="unexpected=1"):
        _loader(dataset, extra_root).load()


def test_evidence_rejects_attempt_that_no_longer_matches_request(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    root = _copy_runs(completed_fake_run_root, tmp_path / "tampered")
    plan = load_execution_plan(FIXTURE_ROOT / "execution-plan.json", dataset)
    item = plan.items[0]
    store = ArtifactStore(root)
    relative_path = (
        f"{dataset.definition.experiment_id}/attempts/{item.run_id}/001-started.json"
    )
    intent = store.read_model(relative_path, AttemptIntent)
    tampered = intent.model_copy(update={"prompt_hash": "0" * 64})
    (store.root / Path(relative_path)).write_bytes(canonical_model_bytes(tampered))

    with pytest.raises(ExperimentEvidenceError, match="intent provenance"):
        _loader(dataset, root).load()
