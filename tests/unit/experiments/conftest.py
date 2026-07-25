"""Shared frozen dataset fixtures for M5 experiment unit tests."""

from pathlib import Path

import pytest

from experiments.cli import main
from experiments.contracts import ExperimentDefinition
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3] / "experiments" / "definitions" / "writer-v1"
)


@pytest.fixture(scope="session")
def dataset() -> LoadedExperimentDataset:
    return load_experiment_dataset(FIXTURE_ROOT)


@pytest.fixture(scope="session")
def experiment_root(dataset: LoadedExperimentDataset) -> ExperimentDefinition:
    return dataset.definition


@pytest.fixture(scope="session")
def completed_fake_run_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build one complete offline journal reused by read-only pipeline tests."""

    root = tmp_path_factory.mktemp("completed-fake-runs")
    plan = FIXTURE_ROOT / "execution-plan.json"
    assert (
        main(
            [
                "run-fake",
                "--definition-root",
                str(FIXTURE_ROOT),
                "--plan",
                str(plan),
                "--output-root",
                str(root),
                "--max-items",
                "240",
            ]
        )
        == 0
    )
    return root
