"""File-backed SQLite migration integration tests."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from agent_factory.infrastructure.sqlite import (
    MigrationChecksumError,
    SqliteMigrationRunner,
)


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_new_database_migrates_and_second_run_is_idempotent(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database_path = tmp_path / "factory.db"
    runner = SqliteMigrationRunner(database_path, migrations_dir, FrozenClock())

    first = await runner.migrate()
    second = await runner.migrate()

    assert first.applied_versions == (1,)
    assert first.current_version == 1
    assert second.applied_versions == ()
    assert second.current_version == 1

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        )
        history = list(await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {str(row[0]) for row in await cursor.fetchall()}

    assert len(history) == 1
    assert tuple(history[0]) == (1, "initial", "2026-07-17T12:00:00Z")
    assert {"prototypes", "knowledge_packages", "agent_specs"} <= tables


@pytest.mark.asyncio
async def test_modified_applied_migration_is_rejected(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    copied_migrations = tmp_path / "migrations"
    shutil.copytree(migrations_dir, copied_migrations)
    runner = SqliteMigrationRunner(
        tmp_path / "factory.db",
        copied_migrations,
        FrozenClock(),
    )
    await runner.migrate()

    migration_path = copied_migrations / "001_initial.sql"
    migration_path.write_text(
        migration_path.read_text(encoding="utf-8") + "\n-- modified\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationChecksumError, match="checksum changed"):
        await runner.migrate()
