"""Offline M5.3 command line behavior tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from experiments.cli import main

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
COMMITTED_PLAN = DEFINITION_ROOT / "execution-plan.json"


@dataclass(frozen=True, slots=True)
class _CliCandidate:
    freeze_id: str = "cli-pilot-freeze"


@dataclass(frozen=True, slots=True)
class _CliSource:
    source_commit: str = "a" * 40


@dataclass(frozen=True, slots=True)
class _CliBudget:
    hard_cost_limit_usd_micros: int = 1_000


@dataclass(frozen=True, slots=True)
class _CliManifest:
    manifest_checksum: str = "b" * 64
    source: _CliSource = _CliSource()
    cost_budget: _CliBudget = _CliBudget()
    files: tuple[str, ...] = ("uv.lock",)


class _CliFreezeBuilder:
    def __init__(self, repository_root: Path) -> None:
        assert repository_root == REPOSITORY_ROOT

    def build(self, **kwargs: object) -> _CliManifest:
        assert kwargs["candidate_spec_path"] == Path("candidate.json")
        assert kwargs["plan_path"] == COMMITTED_PLAN.resolve()
        return _CliManifest()


def test_plan_and_verify_commands_reproduce_committed_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "execution-plan.json"
    assert (
        main(
            [
                "plan",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == COMMITTED_PLAN.read_bytes()
    assert (
        main(
            [
                "verify-plan",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "runs=240" in captured.out


def test_run_fake_is_explicitly_non_evidentiary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "runs"
    assert (
        main(
            [
                "run-fake",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(COMMITTED_PLAN),
                "--output-root",
                str(output_root),
                "--max-items",
                "2",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "offline smoke only; not experiment evidence" in captured.out
    terminal = list(output_root.rglob("terminal/*.json"))
    assert len(terminal) == 2


def test_freeze_cli_wires_candidate_and_content_only_verification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import cli

    published: list[Path] = []
    verification_scopes: list[bool] = []

    def load_candidate(path: Path) -> _CliCandidate:
        assert path == (REPOSITORY_ROOT / "candidate.json")
        return _CliCandidate()

    def publish(
        manifest: object,
        *,
        repository_root: Path,
        output_path: Path,
    ) -> bool:
        assert isinstance(manifest, _CliManifest)
        assert repository_root == REPOSITORY_ROOT
        published.append(output_path)
        return True

    def load_manifest(path: Path) -> _CliManifest:
        assert path == (tmp_path / "manifest.json").resolve()
        return _CliManifest()

    def verify(manifest: object, **kwargs: object) -> None:
        assert isinstance(manifest, _CliManifest)
        verification_scopes.append(bool(kwargs["verify_environment"]))

    monkeypatch.setattr(cli, "FreezeCandidateBuilder", _CliFreezeBuilder)
    monkeypatch.setattr(cli, "load_freeze_candidate_spec", load_candidate)
    monkeypatch.setattr(cli, "publish_freeze_candidate", publish)
    monkeypatch.setattr(cli, "load_frozen_experiment_manifest", load_manifest)
    monkeypatch.setattr(cli, "verify_freeze_manifest", verify)

    assert (
        main(
            [
                "freeze-candidate",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(COMMITTED_PLAN),
                "--spec",
                "candidate.json",
            ]
        )
        == 0
    )
    assert published == [
        REPOSITORY_ROOT / ".tmp" / "m5-freeze" / "cli-pilot-freeze.json"
    ]

    manifest_path = tmp_path / "manifest.json"
    assert (
        main(
            [
                "verify-freeze",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(COMMITTED_PLAN),
                "--manifest",
                str(manifest_path),
                "--content-only",
            ]
        )
        == 0
    )
    assert verification_scopes == [False]
    output = capsys.readouterr().out
    assert "created freeze candidate" in output
    assert "scope=content-only" in output
