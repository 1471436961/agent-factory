"""Formal candidate derivation and Pilot evidence binding tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from experiments.cli import main
from experiments.evidence_sealing import (
    calculate_pilot_evidence_seal_checksum,
    load_pilot_evidence_seal,
    publish_pilot_evidence_seal,
)
from experiments.formal_freezing import (
    FormalCandidateDraftError,
    FormalCandidateDraftRequest,
    build_formal_freeze_candidate,
    publish_formal_freeze_candidate,
)
from experiments.freezing import (
    EnvironmentReader,
    FreezeCandidateBuilder,
    FreezeError,
    GitSnapshot,
    GitSnapshotReader,
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
)
from experiments.loader import load_experiment_dataset
from experiments.planning import load_execution_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"
PILOT_MANIFEST = (
    REPOSITORY_ROOT
    / "experiments"
    / "evidence"
    / "writer-pilot-v1"
    / "freeze-manifest.json"
)
PILOT_SEAL = PILOT_MANIFEST.with_name("evidence-seal-mfjs-20260728.json")
PILOT_REPORT = REPOSITORY_ROOT / "docs" / "reports" / "m5.5-moonshot-pilot-review.md"
FORMAL_MANIFEST = (
    REPOSITORY_ROOT / "experiments" / "evidence" / "writer-v1" / "freeze-manifest.json"
)
NOW = datetime.fromisoformat("2026-07-28T15:53:55+08:00")


class _CleanGitReader(GitSnapshotReader):
    def snapshot(self, repository_root: Path) -> GitSnapshot:
        return GitSnapshot(
            repository_root=repository_root,
            source_commit="a" * 40,
            working_tree_clean=True,
        )


class _FrozenEnvironment(EnvironmentReader):
    def python_implementation(self) -> str:
        return "CPython"

    def python_version(self) -> str:
        return "3.11.15"

    def distribution_version(self, distribution_name: str) -> str:
        assert distribution_name == "openai"
        return "2.46.0"


def _request(candidate_path: Path) -> FormalCandidateDraftRequest:
    return FormalCandidateDraftRequest(
        repository_root=REPOSITORY_ROOT,
        candidate_path=candidate_path,
        pilot_manifest_path=PILOT_MANIFEST,
        pilot_report_path=PILOT_REPORT,
        pilot_evidence_seal_path=PILOT_SEAL,
        pricing_source_url="https://platform.kimi.com/docs/pricing/chat-k26",
        pricing_captured_at=NOW,
        created_at=NOW,
    )


def test_formal_candidate_builds_and_freezes_with_pilot_evidence(
    tmp_path: Path,
) -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    candidate_path = tmp_path / "formal-candidate.json"
    candidate = build_formal_freeze_candidate(
        request=_request(candidate_path),
        dataset=dataset,
        plan=plan,
    )

    assert candidate.pilot_evidence is not None
    assert candidate.pilot_evidence.evidence_seal_checksum == (
        "9cb5965cbd76cdec0728b29ec91f45da2a178a52d633030bbae51cc4e073114d"
    )
    assert candidate.cost_budget.estimated_cost_micros == 12_875_520
    assert candidate.cost_budget.hard_cost_limit_micros == 25_751_040
    assert publish_formal_freeze_candidate(candidate, candidate_path) is True
    assert publish_formal_freeze_candidate(candidate, candidate_path) is False
    assert load_freeze_candidate_spec(candidate_path) == candidate

    manifest = FreezeCandidateBuilder(
        REPOSITORY_ROOT,
        git_reader=_CleanGitReader(),
        environment_reader=_FrozenEnvironment(),
    ).build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )
    assert manifest.pilot_evidence == candidate.pilot_evidence
    assert len(manifest.files) == len(candidate.inventory_paths)


def test_formal_freezer_rejects_forged_pilot_report_reference(tmp_path: Path) -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    candidate_path = tmp_path / "formal-candidate.json"
    candidate = build_formal_freeze_candidate(
        request=_request(candidate_path),
        dataset=dataset,
        plan=plan,
    )
    assert candidate.pilot_evidence is not None
    forged_evidence = candidate.pilot_evidence.model_copy(
        update={"report_checksum": "f" * 64}
    )
    forged = candidate.model_copy(update={"pilot_evidence": forged_evidence})
    publish_formal_freeze_candidate(forged, candidate_path)

    with pytest.raises(FreezeError, match="review report checksum"):
        FreezeCandidateBuilder(
            REPOSITORY_ROOT,
            git_reader=_CleanGitReader(),
            environment_reader=_FrozenEnvironment(),
        ).build(
            candidate=forged,
            candidate_spec_path=candidate_path,
            plan_path=PLAN_PATH,
            dataset=dataset,
            plan=plan,
        )


def test_formal_candidate_cli_is_write_once_and_reports_reviewed_bounds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "formal-candidate.json"
    arguments = [
        "draft-formal-candidate",
        "--definition-root",
        str(DEFINITION_ROOT),
        "--plan",
        str(PLAN_PATH),
        "--output",
        str(output),
        "--pricing-captured-at",
        NOW.isoformat(),
        "--created-at",
        NOW.isoformat(),
    ]

    assert main(arguments) == 0
    assert main(arguments) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("created formal candidate")
    assert lines[1].startswith("verified formal candidate")
    assert all("requests=240/480" in line for line in lines)
    assert all("cost_micros=12875520/25751040" in line for line in lines)


def test_formal_candidate_rejects_empty_report_and_pilot_seal_drift(
    tmp_path: Path,
) -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    empty_report = tmp_path / "empty-report.md"
    empty_report.write_bytes(b"")
    empty_request = _request(tmp_path / "empty-report-candidate.json")
    empty_request = replace(empty_request, pilot_report_path=empty_report)
    with pytest.raises(FormalCandidateDraftError, match="cannot be empty"):
        build_formal_freeze_candidate(
            request=empty_request,
            dataset=dataset,
            plan=plan,
        )

    seal = load_pilot_evidence_seal(PILOT_SEAL)
    changed = seal.model_copy(update={"freeze_manifest_checksum": "f" * 64})
    changed = changed.model_copy(
        update={
            "seal_checksum": calculate_pilot_evidence_seal_checksum(changed),
        }
    )
    changed_path = tmp_path / "changed-seal.json"
    publish_pilot_evidence_seal(changed, changed_path)
    changed_request = _request(tmp_path / "changed-seal-candidate.json")
    changed_request = replace(
        changed_request,
        pilot_evidence_seal_path=changed_path,
    )
    with pytest.raises(FormalCandidateDraftError, match="do not match"):
        build_formal_freeze_candidate(
            request=changed_request,
            dataset=dataset,
            plan=plan,
        )


def test_archived_formal_manifest_retains_reviewed_identity() -> None:
    manifest_bytes = FORMAL_MANIFEST.read_bytes()
    manifest = load_frozen_experiment_manifest(FORMAL_MANIFEST)

    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "d4d2d390467f47097db67540bcafaffc51c98152cd176b730855ebd8f1277ff1"
    )
    assert manifest.manifest_checksum == (
        "211275d9312207fef02a8f15ee3f3a86bfe6f31c52337361b9f2666260fb7e1f"
    )
    assert manifest.source.source_commit == ("f0c75655bd3f8ccd1ce4e662e687fe0d50edc026")
    assert len(manifest.files) == 152
    assert manifest.pilot_evidence is not None
    assert manifest.pilot_evidence.evidence_seal_checksum == (
        "9cb5965cbd76cdec0728b29ec91f45da2a178a52d633030bbae51cc4e073114d"
    )
    assert manifest.cost_budget.currency == "CNY"
    assert manifest.cost_budget.estimated_cost_micros == 12_875_520
    assert manifest.cost_budget.hard_cost_limit_micros == 25_751_040
