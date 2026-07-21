"""File-backed SQLite migration integration tests."""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite
import pytest

from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import InstanceStatus, PrototypeStatus
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    PrototypeRef,
)
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import TaskOutcome
from agent_factory.infrastructure.sqlite import (
    MigrationChecksumError,
    MigrationExecutionError,
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)
from agent_factory.infrastructure.sqlite.codec import encode_model
from agent_factory.infrastructure.sqlite.repository_base import format_datetime


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

    assert first.applied_versions == (1, 2, 3, 4, 5)
    assert first.current_version == 5
    assert second.applied_versions == ()
    assert second.current_version == 5

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        )
        history = list(await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {str(row[0]) for row in await cursor.fetchall()}

    assert len(history) == 5
    assert tuple(history[0]) == (1, "initial", "2026-07-17T12:00:00Z")
    assert tuple(history[1]) == (
        2,
        "persistence_contracts",
        "2026-07-17T12:00:00Z",
    )
    assert tuple(history[2]) == (
        3,
        "skill_governance",
        "2026-07-17T12:00:00Z",
    )
    assert tuple(history[3]) == (
        4,
        "instance_configuration_checksum",
        "2026-07-17T12:00:00Z",
    )
    assert tuple(history[4]) == (
        5,
        "task_outcome_integrity",
        "2026-07-17T12:00:00Z",
    )
    assert {
        "prototypes",
        "knowledge_packages",
        "agent_specs",
        "skill_trees",
        "evaluation_suites",
        "evaluation_reports",
        "evaluation_reviews",
        "task_outcomes",
    } <= tables


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
async def test_task_outcome_integrity_migration_rejects_replayed_reports_atomically(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    copied_migrations = tmp_path / "migrations"
    copied_migrations.mkdir()
    for name in (
        "001_initial.sql",
        "002_persistence_contracts.sql",
        "003_skill_governance.sql",
        "004_instance_configuration_checksum.sql",
    ):
        shutil.copy2(migrations_dir / name, copied_migrations / name)
    database_path = tmp_path / "factory.db"
    runner = SqliteMigrationRunner(database_path, copied_migrations, FrozenClock())
    assert (await runner.migrate()).current_version == 4

    report_id = UUID("00000000-0000-0000-0000-000000000801")
    async with aiosqlite.connect(database_path) as connection:
        for number in (802, 803):
            outcome = TaskOutcome(
                task_id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
                skill_node_id="junior-engineer",
                passed=False,
                evaluation_report_id=report_id,
                recorded_at=FrozenClock().now(),
            )
            await connection.execute(
                """
                INSERT INTO task_outcomes (
                    task_id, instance_id, instance_revision, skill_node_id,
                    passed, evaluation_report_id, payload_json,
                    payload_checksum, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(outcome.task_id),
                    "00000000-0000-0000-0000-000000000804",
                    1,
                    outcome.skill_node_id,
                    int(outcome.passed),
                    str(outcome.evaluation_report_id),
                    encode_model(outcome),
                    sha256_model(outcome),
                    format_datetime(outcome.recorded_at),
                ),
            )
        await connection.commit()

    shutil.copy2(
        migrations_dir / "005_task_outcome_integrity.sql",
        copied_migrations / "005_task_outcome_integrity.sql",
    )
    with pytest.raises(MigrationExecutionError, match="005_task_outcome_integrity"):
        await runner.migrate()

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        versions = tuple(int(row[0]) for row in await cursor.fetchall())
        cursor = await connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index' AND name = 'uq_task_outcomes_evaluation_report'
            """
        )
        unique_index = await cursor.fetchone()

    assert versions == (1, 2, 3, 4)
    assert unique_index is None


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


@pytest.mark.asyncio
async def test_existing_m1_snapshots_upgrade_from_v2_to_v5_and_remain_readable(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    copied_migrations = tmp_path / "migrations"
    copied_migrations.mkdir()
    for name in ("001_initial.sql", "002_persistence_contracts.sql"):
        shutil.copy2(migrations_dir / name, copied_migrations / name)
    database_path = tmp_path / "factory.db"
    runner = SqliteMigrationRunner(database_path, copied_migrations, FrozenClock())
    assert (await runner.migrate()).current_version == 2

    definition = AgentDefinition(
        agent_type="engineer-agent",
        role="Engineer",
        system_prompt="Implement and test.",
    )
    prototype = AgentPrototype(
        prototype_id="engineer-agent",
        version="1.0.0",
        status=PrototypeStatus.PUBLISHED,
        definition=definition,
        checksum=sha256_model(definition),
        created_at=FrozenClock().now(),
        created_by="owner",
        published_at=FrozenClock().now(),
    )
    instance = AgentInstance(
        instance_id=UUID("00000000-0000-0000-0000-000000000701"),
        prototype=PrototypeRef(
            prototype_id=prototype.prototype_id,
            version=prototype.version,
            checksum=prototype.checksum,
        ),
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=definition,
        created_at=FrozenClock().now(),
        updated_at=FrozenClock().now(),
        created_by="owner",
    )
    spec = AgentSpecBuilder().build(
        instance=instance,
        tools=(),
        generated_at=FrozenClock().now(),
    )
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(
            """
            INSERT INTO prototypes (
                prototype_id, version, status, payload_json, checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prototype.prototype_id,
                prototype.version,
                prototype.status.value,
                encode_model(prototype),
                prototype.checksum,
                format_datetime(prototype.created_at),
            ),
        )
        await connection.execute(
            """
            INSERT INTO instance_snapshots (
                instance_id, revision, status, prototype_id,
                prototype_version, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(instance.instance_id),
                instance.revision,
                instance.status.value,
                prototype.prototype_id,
                prototype.version,
                encode_model(instance),
                format_datetime(instance.updated_at),
            ),
        )
        await connection.execute(
            """
            INSERT INTO instance_heads (instance_id, current_revision, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                str(instance.instance_id),
                instance.revision,
                format_datetime(instance.updated_at),
            ),
        )
        await connection.execute(
            """
            INSERT INTO agent_specs (
                instance_id, revision, payload_json, checksum, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(spec.instance_id),
                spec.revision,
                encode_model(spec),
                spec.spec_checksum,
                format_datetime(spec.generated_at),
            ),
        )
        await connection.commit()

    for name in (
        "003_skill_governance.sql",
        "004_instance_configuration_checksum.sql",
        "005_task_outcome_integrity.sql",
    ):
        shutil.copy2(migrations_dir / name, copied_migrations / name)
    upgrade = await runner.migrate()
    assert upgrade.applied_versions == (3, 4, 5)
    assert upgrade.current_version == 5

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            """
            SELECT configuration_checksum
            FROM instance_snapshots
            WHERE instance_id = ? AND revision = ?
            """,
            (str(instance.instance_id), instance.revision),
        )
        configuration_checksum_row = await cursor.fetchone()
    assert configuration_checksum_row is not None
    assert str(configuration_checksum_row[0]) == prototype.checksum

    factory = SqliteUnitOfWorkFactory(database_path)
    async with factory(read_only=True) as uow:
        assert await uow.prototypes.get(prototype.prototype_id, prototype.version) == (
            prototype
        )
        assert await uow.instances.get(instance.instance_id) == instance
        assert await uow.specs.get(instance.instance_id, instance.revision) == spec
