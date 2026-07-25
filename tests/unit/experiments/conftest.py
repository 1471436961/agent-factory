"""Shared frozen dataset fixtures for M5 experiment unit tests."""

from pathlib import Path

import pytest
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
