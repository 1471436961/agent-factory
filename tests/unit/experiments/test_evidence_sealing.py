"""Pilot evidence seal tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.cli import main
from experiments.contracts import ExecutionManifest, PilotEvidenceSeal
from experiments.evidence_sealing import (
    PilotEvidenceSealError,
    build_pilot_evidence_seal,
    calculate_pilot_evidence_seal_checksum,
    load_pilot_evidence_seal,
    publish_pilot_evidence_seal,
    verify_pilot_evidence_seal,
)
from experiments.freezing import (
    FreezeError,
    calculate_freeze_manifest_checksum,
    load_frozen_experiment_manifest,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import load_execution_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"


def _build(
    dataset: LoadedExperimentDataset,
    evidence_root: Path,
) -> PilotEvidenceSeal:
    plan = load_execution_plan(PLAN_PATH, dataset)
    execution = ArtifactStore(evidence_root).read_model(
        f"{plan.experiment_id}/execution-manifest.json",
        ExecutionManifest,
    )
    return build_pilot_evidence_seal(
        dataset=dataset,
        plan=plan,
        evidence_root=evidence_root,
        evidence_root_label="writer-fake-evidence",
        freeze_manifest_checksum="a" * 64,
        expected_execution_manifest_checksum=execution.manifest_checksum,
    )


def test_build_publish_load_and_reverify_complete_evidence(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    seal = _build(dataset, completed_fake_run_root)

    assert seal.run_count == 240
    assert seal.attempt_count == 240
    assert sum(item.count for item in seal.status_counts) == 240
    assert seal.total_bytes == sum(item.byte_size for item in seal.files)
    assert seal.seal_checksum == calculate_pilot_evidence_seal_checksum(seal)

    output = tmp_path / "pilot-evidence-seal.json"
    assert publish_pilot_evidence_seal(seal, output) is True
    assert publish_pilot_evidence_seal(seal, output) is False
    assert load_pilot_evidence_seal(output) == seal
    verify_pilot_evidence_seal(
        seal,
        dataset=dataset,
        plan=plan,
        evidence_root=completed_fake_run_root,
    )


def test_changed_journal_and_forged_seal_are_rejected(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    copied = tmp_path / "copied-evidence"
    shutil.copytree(completed_fake_run_root, copied)
    seal = _build(dataset, copied)
    terminal = next(copied.rglob("terminal/*.json"))
    terminal.write_bytes(terminal.read_bytes() + b"\n")

    with pytest.raises(PilotEvidenceSealError, match="journal is invalid"):
        verify_pilot_evidence_seal(
            seal,
            dataset=dataset,
            plan=load_execution_plan(PLAN_PATH, dataset),
            evidence_root=copied,
        )

    forged = seal.model_copy(update={"seal_checksum": "f" * 64})
    forged_path = tmp_path / "forged.json"
    forged_path.write_bytes(canonical_model_bytes(forged))
    with pytest.raises(PilotEvidenceSealError, match="checksum mismatch"):
        load_pilot_evidence_seal(forged_path)


def test_noncanonical_and_missing_seals_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(PilotEvidenceSealError, match="cannot be read"):
        load_pilot_evidence_seal(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}\n", encoding="utf-8")
    with pytest.raises(PilotEvidenceSealError, match="is invalid"):
        load_pilot_evidence_seal(invalid)

    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(PilotEvidenceSealError, match="size is invalid"):
        load_pilot_evidence_seal(empty)


def test_missing_evidence_root_and_manifest_identity_mismatch_are_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    with pytest.raises(PilotEvidenceSealError, match="cannot be resolved"):
        build_pilot_evidence_seal(
            dataset=dataset,
            plan=plan,
            evidence_root=tmp_path / "missing-evidence",
            evidence_root_label="missing",
            freeze_manifest_checksum="a" * 64,
            expected_execution_manifest_checksum="b" * 64,
        )

    pilot_manifest = (
        REPOSITORY_ROOT
        / "experiments"
        / "evidence"
        / "writer-pilot-v1"
        / "freeze-manifest.json"
    )
    with pytest.raises(FreezeError, match="does not match definition root"):
        main(
            [
                "seal-pilot-evidence",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(PLAN_PATH),
                "--manifest",
                str(pilot_manifest),
                "--evidence-root",
                str(tmp_path),
                "--root-label",
                "unreachable",
                "--output",
                str(tmp_path / "unreachable.json"),
            ]
        )


def test_execution_identity_noncanonical_bytes_and_seal_drift_are_rejected(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    seal = _build(dataset, completed_fake_run_root)

    with pytest.raises(PilotEvidenceSealError, match="does not match"):
        build_pilot_evidence_seal(
            dataset=dataset,
            plan=plan,
            evidence_root=completed_fake_run_root,
            evidence_root_label="writer-fake-evidence",
            freeze_manifest_checksum="a" * 64,
            expected_execution_manifest_checksum="f" * 64,
        )

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(canonical_model_bytes(seal) + b"\n")
    with pytest.raises(PilotEvidenceSealError, match="not canonical"):
        load_pilot_evidence_seal(noncanonical)

    drifted = seal.model_copy(update={"total_bytes": seal.total_bytes + 1})
    drifted = drifted.model_copy(
        update={
            "seal_checksum": calculate_pilot_evidence_seal_checksum(drifted),
        }
    )
    with pytest.raises(PilotEvidenceSealError, match="differs"):
        verify_pilot_evidence_seal(
            drifted,
            dataset=dataset,
            plan=plan,
            evidence_root=completed_fake_run_root,
        )

    not_directory = tmp_path / "not-directory"
    not_directory.write_text("content", encoding="utf-8")
    with pytest.raises(PilotEvidenceSealError, match="must be a directory"):
        build_pilot_evidence_seal(
            dataset=dataset,
            plan=plan,
            evidence_root=not_directory,
            evidence_root_label="writer-fake-evidence",
            freeze_manifest_checksum="a" * 64,
            expected_execution_manifest_checksum=seal.execution_manifest_checksum,
        )


def test_seal_pilot_evidence_cli_publishes_and_replays_canonical_seal(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    execution = ArtifactStore(completed_fake_run_root).read_model(
        f"{plan.experiment_id}/execution-manifest.json",
        ExecutionManifest,
    )
    pilot_manifest = load_frozen_experiment_manifest(
        REPOSITORY_ROOT
        / "experiments"
        / "evidence"
        / "writer-pilot-v1"
        / "freeze-manifest.json"
    )
    generation = execution.generation
    provider = pilot_manifest.provider.model_copy(
        update={
            "provider": generation.provider,
            "model": generation.model,
            "sdk_version": generation.sdk_version,
        }
    )
    pricing = pilot_manifest.pricing.model_copy(
        update={
            "provider": generation.provider,
            "model": generation.model,
        }
    )
    unsigned = pilot_manifest.model_copy(
        update={
            "experiment_id": dataset.definition.experiment_id,
            "definition_checksum": plan.definition_checksum,
            "execution_manifest": execution,
            "provider": provider,
            "pricing": pricing,
            "manifest_checksum": "0" * 64,
        }
    )
    manifest = unsigned.model_copy(
        update={
            "manifest_checksum": calculate_freeze_manifest_checksum(unsigned),
        }
    )
    manifest_path = tmp_path / "pilot-manifest.json"
    manifest_path.write_bytes(canonical_model_bytes(manifest))
    output = tmp_path / "seal.json"
    arguments = [
        "seal-pilot-evidence",
        "--definition-root",
        str(DEFINITION_ROOT),
        "--plan",
        str(PLAN_PATH),
        "--manifest",
        str(manifest_path),
        "--evidence-root",
        str(completed_fake_run_root),
        "--root-label",
        "formal-fixture-as-pilot-cli-test",
        "--output",
        str(output),
    ]

    assert main(arguments) == 0
    assert main(arguments) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("created Pilot evidence seal")
    assert lines[1].startswith("verified Pilot evidence seal")
    assert all("runs=240 attempts=240" in line for line in lines)
    assert load_pilot_evidence_seal(output).run_count == 240
