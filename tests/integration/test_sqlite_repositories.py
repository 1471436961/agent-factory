"""Real SQLite tests for repository, transaction, and revision guarantees."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiosqlite
import pytest

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, PrototypeListQuery
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import (
    AuditEntityType,
    AuditEventType,
    InstanceStatus,
    PrototypeStatus,
)
from agent_factory.domain.errors import (
    KnowledgeAlreadyExistsError,
    PrototypeAlreadyExistsError,
    RepositoryUnavailableError,
    RevisionConflictError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    DomainKnowledgeDraft,
    PrototypeRef,
)
from agent_factory.domain.services.spec import checksum_agent_spec
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)

NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000101")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000201")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000301")
CAUSATION_ID = UUID("00000000-0000-0000-0000-000000000401")


class FrozenClock:
    def now(self) -> datetime:
        return NOW


async def _factory(
    tmp_path: Path,
    migrations_dir: Path,
) -> tuple[Path, SqliteUnitOfWorkFactory]:
    database_path = tmp_path / "factory.db"
    await SqliteMigrationRunner(
        database_path,
        migrations_dir,
        FrozenClock(),
    ).migrate()
    return database_path, SqliteUnitOfWorkFactory(database_path)


def _prototype(
    definition: AgentDefinition,
    *,
    version: str = "1.0.0",
    status: PrototypeStatus = PrototypeStatus.DRAFT,
) -> AgentPrototype:
    published_at = NOW if status is not PrototypeStatus.DRAFT else None
    return AgentPrototype(
        prototype_id="writer-agent",
        version=version,
        status=status,
        definition=definition,
        checksum=sha256_model(definition),
        created_at=NOW,
        created_by="owner",
        published_at=published_at,
    )


def _knowledge(draft: DomainKnowledgeDraft) -> DomainKnowledge:
    return DomainKnowledge.model_validate(
        {
            **draft.model_dump(mode="python"),
            "created_at": NOW,
            "created_by": "owner",
        }
    )


def _instance(prototype: AgentPrototype, *, revision: int = 1) -> AgentInstance:
    return AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id=prototype.prototype_id,
            version=prototype.version,
            checksum=prototype.checksum,
        ),
        revision=revision,
        status=(InstanceStatus.CREATED if revision == 1 else InstanceStatus.WAITING),
        configuration=prototype.definition,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=revision - 1),
        created_by="owner",
    )


def _spec(instance: AgentInstance) -> AgentSpec:
    unsigned = AgentSpec(
        instance_id=instance.instance_id,
        revision=instance.revision,
        prototype=instance.prototype,
        agent_type=instance.configuration.agent_type,
        role=instance.configuration.role,
        system_prompt=instance.configuration.system_prompt,
        tools=(),
        knowledge=(),
        output_schema=instance.configuration.output_schema,
        runtime_target=instance.runtime_target,
        generated_at=NOW,
        spec_checksum="0" * 64,
        metadata=instance.configuration.metadata,
    )
    return unsigned.model_copy(update={"spec_checksum": checksum_agent_spec(unsigned)})


def _audit_event(prototype: AgentPrototype) -> AuditEvent:
    return AuditEvent(
        event_id=EVENT_ID,
        event_type=AuditEventType.PROTOTYPE_REGISTERED,
        entity_type=AuditEntityType.PROTOTYPE,
        entity_id=prototype.prototype_id,
        actor="owner",
        correlation_id=CORRELATION_ID,
        causation_id=CAUSATION_ID,
        payload={
            "prototype_id": prototype.prototype_id,
            "version": prototype.version,
            "checksum": prototype.checksum,
            "status": prototype.status.value,
        },
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_all_m1_repositories_round_trip_canonical_snapshots(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition, status=PrototypeStatus.PUBLISHED)
    knowledge = _knowledge(product_knowledge_draft)
    instance = _instance(prototype)
    spec = _spec(instance)
    event = _audit_event(prototype)
    idempotency = IdempotencyRecord(
        idempotency_key="register-prototype-1",
        operation="register-prototype",
        request_hash="a" * 64,
        response={"prototype_id": prototype.prototype_id},
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )

    async with factory() as uow:
        await uow.prototypes.add(prototype)
        await uow.knowledge.add(knowledge)
        await uow.instances.add(instance)
        assert await uow.specs.add_if_absent(spec) is True
        assert await uow.specs.add_if_absent(spec) is False
        await uow.audit.append(event)
        await uow.idempotency.add(idempotency)
        await uow.commit()

    async with factory(read_only=True) as uow:
        assert await uow.prototypes.get(prototype.prototype_id, prototype.version) == (
            prototype
        )
        assert await uow.knowledge.get(knowledge.knowledge_id, knowledge.version) == (
            knowledge
        )
        assert await uow.knowledge.get_many(
            ((knowledge.knowledge_id, knowledge.version), ("missing", "1.0.0"))
        ) == (knowledge,)
        assert await uow.instances.get(instance.instance_id) == instance
        assert await uow.specs.get(instance.instance_id, instance.revision) == spec
        assert await uow.idempotency.get(idempotency.idempotency_key) == idempotency
        audit_page = await uow.audit.query(
            AuditQuery(
                entity_type=AuditEntityType.PROTOTYPE,
                entity_id=prototype.prototype_id,
            )
        )

    assert audit_page.items == (event,)
    assert audit_page.total == 1

    async with factory() as uow:
        with pytest.raises(KnowledgeAlreadyExistsError):
            await uow.knowledge.add(knowledge)


@pytest.mark.asyncio
async def test_prototype_replace_uses_status_cas_and_semver_listing(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    version_2 = _prototype(writer_definition, version="1.2.0")
    version_10 = _prototype(writer_definition, version="1.10.0")

    async with factory() as uow:
        await uow.prototypes.add(version_2)
        await uow.prototypes.add(version_10)
        with pytest.raises(PrototypeAlreadyExistsError):
            await uow.prototypes.add(version_10)

    async with factory() as uow:
        await uow.prototypes.add(version_2)
        await uow.prototypes.add(version_10)
        await uow.commit()

    published = _prototype(
        writer_definition,
        version="1.10.0",
        status=PrototypeStatus.PUBLISHED,
    )
    async with factory() as uow:
        assert await uow.prototypes.replace(
            published,
            PrototypeStatus.DRAFT,
        )
        assert not await uow.prototypes.replace(
            published,
            PrototypeStatus.DRAFT,
        )
        await uow.commit()

    async with factory(read_only=True) as uow:
        page = await uow.prototypes.list(PrototypeListQuery())

    assert tuple(item.version for item in page.items) == ("1.10.0", "1.2.0")
    assert page.items[0] == published


@pytest.mark.asyncio
async def test_instance_history_and_concurrent_revision_conflict(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition, status=PrototypeStatus.PUBLISHED)
    revision_1 = _instance(prototype)
    revision_2 = _instance(prototype, revision=2)

    async with factory() as uow:
        await uow.prototypes.add(prototype)
        await uow.instances.add(revision_1)
        await uow.commit()

    async def save_revision() -> str:
        try:
            async with factory() as uow:
                await uow.instances.save_snapshot(revision_2, expected_revision=1)
                await uow.commit()
            return "committed"
        except RevisionConflictError:
            return "conflict"

    outcomes = await asyncio.gather(save_revision(), save_revision())

    async with factory(read_only=True) as uow:
        current = await uow.instances.get(INSTANCE_ID)
        historical = await uow.instances.get(INSTANCE_ID, revision=1)

    assert sorted(outcomes) == ["committed", "conflict"]
    assert current == revision_2
    assert historical == revision_1

    revision_3 = _instance(prototype, revision=3)
    async with factory() as uow:
        with pytest.raises(RevisionConflictError):
            await uow.instances.save_snapshot(revision_3, expected_revision=1)


@pytest.mark.asyncio
async def test_uncommitted_business_and_audit_writes_are_rolled_back_together(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition)
    event = _audit_event(prototype)

    with pytest.raises(RuntimeError, match="injected failure"):
        async with factory() as uow:
            await uow.prototypes.add(prototype)
            await uow.audit.append(event)
            raise RuntimeError("injected failure")

    async with factory(read_only=True) as uow:
        assert (
            await uow.prototypes.get(prototype.prototype_id, prototype.version) is None
        )
        assert (await uow.audit.query(AuditQuery())).total == 0


@pytest.mark.asyncio
async def test_read_only_uow_and_corrupt_projection_fail_safely(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    database_path, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition)

    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as read_only_error:
            await uow.prototypes.add(prototype)
    assert "readonly" not in str(read_only_error.value).lower()

    async with factory() as uow:
        await uow.prototypes.add(prototype)
        await uow.commit()

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            "UPDATE prototypes SET status = 'published' WHERE prototype_id = ?",
            (prototype.prototype_id,),
        )
        await connection.commit()

    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as corrupt_error:
            await uow.prototypes.get(prototype.prototype_id, prototype.version)

    assert corrupt_error.value.code == "REPOSITORY_UNAVAILABLE"
    assert corrupt_error.value.details["reason"] == "projection-mismatch:status"

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """
            UPDATE prototypes
            SET status = 'draft', payload_json = '{'
            WHERE prototype_id = ?
            """,
            (prototype.prototype_id,),
        )
        await connection.commit()

    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as payload_error:
            await uow.prototypes.get(prototype.prototype_id, prototype.version)

    assert payload_error.value.details["reason"] == "invalid-payload"


@pytest.mark.asyncio
async def test_corrupt_instance_configuration_checksum_is_detected(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    database_path, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition, status=PrototypeStatus.PUBLISHED)
    instance = _instance(prototype)
    async with factory() as uow:
        await uow.prototypes.add(prototype)
        await uow.instances.add(instance)
        await uow.commit()

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """
            UPDATE instance_snapshots
            SET configuration_checksum = ?
            WHERE instance_id = ? AND revision = ?
            """,
            ("f" * 64, str(instance.instance_id), instance.revision),
        )
        await connection.commit()

    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as corrupt_error:
            await uow.instances.get(instance.instance_id)

    assert corrupt_error.value.details["reason"] == (
        "projection-mismatch:configuration_checksum"
    )


@pytest.mark.asyncio
async def test_idempotency_expiry_cleanup_is_transactional(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    expired = IdempotencyRecord(
        idempotency_key="expired-command-1",
        operation="clone-agent",
        request_hash="b" * 64,
        response={"instance_id": str(INSTANCE_ID)},
        created_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(days=1),
    )

    async with factory() as uow:
        await uow.idempotency.add(expired)
        await uow.commit()
    async with factory() as uow:
        assert await uow.idempotency.delete_expired(NOW) == 1
        await uow.commit()
    async with factory(read_only=True) as uow:
        assert await uow.idempotency.get(expired.idempotency_key) is None


@pytest.mark.asyncio
async def test_uow_explicit_rollback_and_lifecycle_guards(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    prototype = _prototype(writer_definition)
    uow = factory()

    with pytest.raises(RuntimeError, match="not active"):
        await uow.commit()

    async with uow:
        await uow.prototypes.add(prototype)
        with pytest.raises(RuntimeError, match="more than once"):
            await uow.__aenter__()
        await uow.rollback()
        with pytest.raises(RuntimeError, match="already completed"):
            await uow.rollback()

    with pytest.raises(RuntimeError, match="not active"):
        await uow.commit()

    async with factory(read_only=True) as read_uow:
        stored = await read_uow.prototypes.get(
            prototype.prototype_id,
            prototype.version,
        )
    assert stored is None


def test_uow_factory_rejects_non_positive_busy_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        SqliteUnitOfWorkFactory(tmp_path / "factory.db", busy_timeout_ms=0)
