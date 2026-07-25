"""End-to-end offline replay from complete journals to report packages."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from experiments.artifacts import (
    ArtifactConflictError,
    ArtifactStore,
    canonical_model_bytes,
)
from experiments.cli import main
from experiments.contracts import (
    AnalysisConfig,
    AttemptCompletion,
    ExperimentRun,
    RunScoreRecord,
)
from experiments.loader import LoadedExperimentDataset
from experiments.pipeline import OfflineAnalysisPipeline
from experiments.planning import load_execution_plan
from experiments.score_artifacts import (
    ScoreArtifactCorruptionError,
    ScoreArtifactPublisher,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3] / "experiments" / "definitions" / "writer-v1"
)


def _pipeline(
    dataset: LoadedExperimentDataset,
    runs_root: Path,
    output_store: ArtifactStore,
) -> OfflineAnalysisPipeline:
    plan = load_execution_plan(FIXTURE_ROOT / "execution-plan.json", dataset)
    return OfflineAnalysisPipeline(
        dataset=dataset,
        plan=plan,
        run_store=ArtifactStore(runs_root),
        output_store=output_store,
        config=AnalysisConfig(
            bootstrap_seed=dataset.definition.randomization_seed,
            bootstrap_iterations=100,
        ),
    )


def test_pipeline_publishes_complete_packages_and_replays_identically(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    output = ArtifactStore(tmp_path / "derived")

    first = _pipeline(dataset, completed_fake_run_root, output).run()
    paths = output.list_files("scores") + output.list_files("analysis")
    first_bytes = {path: output.read_bytes(path) for path in paths}
    replay = _pipeline(dataset, completed_fake_run_root, output).run()

    assert first == replay
    assert first.score_manifest.run_count == 240
    assert len(first.score_manifest.records) == 240
    assert len(first.analysis_summary.aggregates) == 96
    assert len(first.analysis_summary.hypotheses) == 6
    assert first.analysis_summary.score_set_checksum == (
        first.score_manifest.score_set_checksum
    )
    assert {path: output.read_bytes(path) for path in paths} == first_bytes
    assert len(output.list_files("scores")) == 241
    assert len(output.list_files("analysis")) == 4


def test_pipeline_recovers_when_score_manifest_publication_is_interrupted(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "interrupted"

    def interrupt_manifest(_temporary: Path, target: Path) -> None:
        if target.name == "score-manifest.json":
            raise RuntimeError("injected score manifest interruption")

    interrupted = ArtifactStore(output_root, before_publish=interrupt_manifest)
    with pytest.raises(RuntimeError, match="score manifest interruption"):
        _pipeline(dataset, completed_fake_run_root, interrupted).run()
    assert len(interrupted.list_files("scores")) == 240
    assert interrupted.list_files("analysis") == ()

    result = _pipeline(
        dataset,
        completed_fake_run_root,
        ArtifactStore(output_root),
    ).run()
    assert result.score_manifest.run_count == 240
    assert len(ArtifactStore(output_root).list_files("scores")) == 241


def test_score_verifier_rejects_record_tampering(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    output = ArtifactStore(tmp_path / "tampered-score")
    result = _pipeline(dataset, completed_fake_run_root, output).run()
    reference = result.score_manifest.records[0]
    package_root = (
        f"scores/{result.execution_manifest.experiment_id}/"
        f"{result.execution_manifest.manifest_checksum}"
    )
    relative_path = f"{package_root}/{reference.path}"
    score = output.read_model(relative_path, RunScoreRecord)
    changed = score.model_copy(update={"run_checksum": "0" * 64})
    (output.root / Path(relative_path)).write_bytes(canonical_model_bytes(changed))

    with pytest.raises(ScoreArtifactCorruptionError, match="artifact mismatch"):
        ScoreArtifactPublisher(output).verify(
            result.execution_manifest.experiment_id,
            result.execution_manifest.manifest_checksum,
        )


def test_changed_terminal_evidence_conflicts_with_published_scores(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    runs_root = tmp_path / "changed-runs"
    shutil.copytree(completed_fake_run_root, runs_root)
    output = ArtifactStore(tmp_path / "derived")
    result = _pipeline(dataset, runs_root, output).run()
    item = load_execution_plan(
        FIXTURE_ROOT / "execution-plan.json",
        dataset,
    ).items[0]
    run_store = ArtifactStore(runs_root)
    base = dataset.definition.experiment_id
    completion_path = f"{base}/attempts/{item.run_id}/001-completed.json"
    terminal_path = f"{base}/terminal/{item.run_id}.json"
    completion = run_store.read_model(completion_path, AttemptCompletion)
    terminal = run_store.read_model(terminal_path, ExperimentRun)
    changed_output = {
        "title": "Changed result",
        "summary": "Changed after the original score package was published.",
        "key_points": ["changed", "evidence"],
        "next_action": "Reject immutable score replay.",
    }
    changed_attempt = completion.attempt.model_copy(
        update={
            "output_text": "changed output",
            "structured_output": changed_output,
        }
    )
    changed_completion = completion.model_copy(update={"attempt": changed_attempt})
    changed_terminal = terminal.model_copy(
        update={
            "attempts": (changed_attempt,),
            "output_text": "changed output",
            "structured_output": changed_output,
        }
    )
    (run_store.root / Path(completion_path)).write_bytes(
        canonical_model_bytes(changed_completion)
    )
    (run_store.root / Path(terminal_path)).write_bytes(
        canonical_model_bytes(changed_terminal)
    )

    with pytest.raises(ArtifactConflictError, match="other bytes"):
        _pipeline(dataset, runs_root, output).run()
    assert result.score_manifest.run_count == 240


def test_analyze_cli_publishes_offline_result(
    completed_fake_run_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "cli-derived"

    assert (
        main(
            [
                "analyze",
                "--definition-root",
                str(FIXTURE_ROOT),
                "--plan",
                str(FIXTURE_ROOT / "execution-plan.json"),
                "--runs-root",
                str(completed_fake_run_root),
                "--output-root",
                str(output_root),
                "--bootstrap-iterations",
                "100",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "offline analysis published: runs=240" in captured.out
    assert len(ArtifactStore(output_root).list_files("scores")) == 241
    assert len(ArtifactStore(output_root).list_files("analysis")) == 4
