"""Shared M0 test fixtures."""

from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest temporary files inside the workspace on restricted hosts."""

    (config.rootpath / ".tmp").mkdir(exist_ok=True)


@pytest.fixture
def migrations_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_factory"
        / "infrastructure"
        / "sqlite"
        / "sql"
    )
