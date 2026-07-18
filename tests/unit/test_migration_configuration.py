"""Migration discovery and configuration tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_factory.infrastructure.sqlite import (
    MigrationConfigurationError,
    MigrationDefinitionError,
    SqliteMigrationRunner,
)


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, tzinfo=UTC)


def test_runner_rejects_non_sqlite_database(migrations_dir: Path) -> None:
    with pytest.raises(MigrationConfigurationError, match="supports only sqlite"):
        SqliteMigrationRunner.from_database_url(
            "postgresql+asyncpg://localhost/agent_factory",
            migrations_dir,
            FrozenClock(),
        )


def test_runner_requires_contiguous_versions(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_gap.sql").write_text("SELECT 1;\n", encoding="utf-8")
    runner = SqliteMigrationRunner(
        tmp_path / "factory.db",
        migrations_dir,
        FrozenClock(),
    )

    with pytest.raises(MigrationDefinitionError, match="contiguous"):
        runner.discover()
