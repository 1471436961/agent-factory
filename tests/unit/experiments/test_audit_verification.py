"""Deterministic H5 lineage verification tests."""

from pathlib import Path

import aiosqlite
import pytest

from agent_factory.infrastructure.sqlite import SqliteUnitOfWorkFactory
from experiments.artifacts import ArtifactStore
from experiments.audit_verification import (
    AuditLineageVerificationError,
    audit_verification_checksum,
    prepare_audit_lineage_fixture,
    publish_audit_verification,
    run_audit_lineage_verification,
    verify_audit_lineage,
)
from experiments.cli import main
from experiments.contracts import AuditVerificationRecord

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"
EXPERIMENT_ID = "writer-validation-v1"


@pytest.mark.asyncio
async def test_h5_verification_reproduces_across_isolated_databases(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    first = await run_audit_lineage_verification(
        database_path=tmp_path / "first.db",
        migrations_dir=migrations_dir,
        experiment_id=EXPERIMENT_ID,
    )
    second = await run_audit_lineage_verification(
        database_path=tmp_path / "second.db",
        migrations_dir=migrations_dir,
        experiment_id=EXPERIMENT_ID,
    )

    assert first == second
    assert first.passed is True
    assert first.completeness == 1.0
    assert tuple(step.step_id for step in first.steps) == (
        "prototype-source",
        "instance-source",
        "knowledge-source",
        "agent-spec-source",
        "evaluation-source",
        "promotion-source",
    )
    assert all(step.matched_event_id is not None for step in first.steps)

    output = tmp_path / "h5-audit-verification.json"
    assert publish_audit_verification(first, output) is True
    assert publish_audit_verification(second, output) is False
    assert (
        ArtifactStore(tmp_path).read_model(
            output.name,
            AuditVerificationRecord,
        )
        == first
    )
    assert audit_verification_checksum(first) == audit_verification_checksum(second)


@pytest.mark.asyncio
async def test_h5_verification_reports_missing_event_without_false_pass(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database = tmp_path / "tampered.db"
    identity = await prepare_audit_lineage_fixture(
        database_path=database,
        migrations_dir=migrations_dir,
        experiment_id=EXPERIMENT_ID,
    )
    async with aiosqlite.connect(database) as connection:
        await connection.execute(
            "DELETE FROM audit_events WHERE event_type = ?",
            ("skill.promoted",),
        )
        await connection.commit()

    record = await verify_audit_lineage(
        uow_factory=SqliteUnitOfWorkFactory(database),
        identity=identity,
    )

    assert record.passed is False
    assert record.completeness == pytest.approx(5 / 6)
    assert record.steps[-1].passed is False
    assert record.steps[-1].reason == "expected exactly one event, found 0"


@pytest.mark.asyncio
async def test_h5_verification_refuses_existing_database(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database = tmp_path / "existing.db"
    database.touch()

    with pytest.raises(AuditLineageVerificationError, match="must not already exist"):
        await run_audit_lineage_verification(
            database_path=database,
            migrations_dir=migrations_dir,
            experiment_id=EXPERIMENT_ID,
        )


def test_verify_audit_lineage_cli_publishes_reproducible_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "h5.json"

    for database in (tmp_path / "cli-first.db", tmp_path / "cli-second.db"):
        assert (
            main(
                [
                    "verify-audit-lineage",
                    "--definition-root",
                    str(DEFINITION_ROOT),
                    "--plan",
                    str(PLAN_PATH),
                    "--database-path",
                    str(database),
                    "--output",
                    str(output),
                ]
            )
            == 0
        )

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("created H5 audit verification")
    assert lines[1].startswith("verified H5 audit verification")
    assert all("steps=6 completeness=1.000000 passed=true" in line for line in lines)
