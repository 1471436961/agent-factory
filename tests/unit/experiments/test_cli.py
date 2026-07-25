"""Offline M5.3 command line behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.cli import main

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
COMMITTED_PLAN = DEFINITION_ROOT / "execution-plan.json"


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
