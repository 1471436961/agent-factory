"""File-backed SQLite migration integration tests."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from agent_factory.infrastructure.sqlite import (
    MigrationChecksumError,
    MigrationExecutionError,
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

    assert first.applied_versions == (1, 2)
    assert first.current_version == 2
    assert second.applied_versions == ()
    assert second.current_version == 2

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        )
        history = list(await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {str(row[0]) for row in await cursor.fetchall()}

    assert len(history) == 2
    assert tuple(history[0]) == (1, "initial", "2026-07-17T12:00:00Z")
    assert tuple(history[1]) == (
        2,
        "persistence_contracts",
        "2026-07-17T12:00:00Z",
    )
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


@pytest.mark.asyncio
async def test_transport_neutral_migration_rejects_unknown_legacy_records_atomically(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    copied_migrations = tmp_path / "migrations"
    copied_migrations.mkdir()
    shutil.copy2(
        migrations_dir / "001_initial.sql",
        copied_migrations / "001_initial.sql",
    )
    database_path = tmp_path / "factory.db"
    runner = SqliteMigrationRunner(database_path, copied_migrations, FrozenClock())
    await runner.migrate()

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """
            INSERT INTO idempotency_records (
                idempotency_key, request_hash, response_status,
                response_json, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-key", "a" * 64, 201, "{}", "2026-07-18T12:00:00Z"),
        )
        await connection.commit()

    shutil.copy2(
        migrations_dir / "002_persistence_contracts.sql",
        copied_migrations / "002_persistence_contracts.sql",
    )
    with pytest.raises(MigrationExecutionError, match="002_persistence_contracts"):
        await runner.migrate()

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute("PRAGMA table_info(audit_events)")
        audit_columns = {str(row[1]) for row in await cursor.fetchall()}
        cursor = await connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        versions = tuple(int(row[0]) for row in await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT response_status FROM idempotency_records"
        )
        legacy_row = await cursor.fetchone()

    assert "causation_id" not in audit_columns
    assert versions == (1,)
    assert legacy_row is not None
    assert int(legacy_row[0]) == 201
