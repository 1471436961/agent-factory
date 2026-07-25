"""M4.4 transaction fault and concurrent AgentSpec integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
from tests.support import (
    EntityWriteTarget,
    FaultInjectingUnitOfWorkFactory,
    FaultPoint,
    InjectedTransactionFailure,
)

from agent_factory.application.commands import (
    CloneAgentCommand,
    RegisterPrototypeCommand,
    TransitionInstanceCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.enums import AuditEventType, InstanceStatus
from agent_factory.domain.models import AgentDefinition, AgentInstance
from agent_factory.settings import Settings


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    database_name: str,
) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / database_name).as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
        }
    )


async def _container(settings: Settings) -> Container:
    container = build_container(settings)
    await container.start()
    return container


async def _clone_minimal_agent(container: Container) -> AgentInstance:
    definition = AgentDefinition(
        agent_type="minimal-agent",
        role="Minimal Agent",
        system_prompt="Return a deterministic response.",
    )
    prototype = await container.controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="minimal-agent",
            version="1.0.0",
            definition=definition,
            publish=True,
            actor="owner",
        )
    )
    return await container.controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            actor="owner",
        )
    )


@pytest.mark.parametrize("fault_point", tuple(FaultPoint))
@pytest.mark.asyncio
async def test_transition_faults_leave_no_partial_revision_audit_or_idempotency(
    fault_point: FaultPoint,
    tmp_path: Path,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        migrations_dir,
        database_name=f"transition-{fault_point.value}.db",
    )
    container = await _container(settings)
    try:
        instance = await _clone_minimal_agent(container)
        command = TransitionInstanceCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            target_status=InstanceStatus.TERMINATED,
            reason="exercise staged transaction rollback",
            actor="owner",
            idempotency_key=f"transition-fault-{fault_point.value}",
        )
        original_factory = container.uow_factory
        fault_factory = FaultInjectingUnitOfWorkFactory(
            original_factory,
            point=fault_point,
            entity_target=EntityWriteTarget.INSTANCE,
        )
        monkeypatch.setattr(container.controller, "_uow_factory", fault_factory)

        with pytest.raises(InjectedTransactionFailure) as captured:
            await container.controller.transition_instance(command)
        assert captured.value.point is fault_point

        async with original_factory(read_only=True) as uow:
            current = await uow.instances.get(instance.instance_id)
            staged_revision = await uow.instances.get(
                instance.instance_id,
                revision=instance.revision + 1,
            )
            idempotency = await uow.idempotency.get(command.idempotency_key or "")
            transition_audit = await uow.audit.query(
                AuditQuery(
                    entity_id=str(instance.instance_id),
                    event_types=frozenset({AuditEventType.INSTANCE_TRANSITIONED}),
                )
            )
        assert current == instance
        assert staged_revision is None
        assert idempotency is None
        assert transition_audit.total == 0

        monkeypatch.setattr(container.controller, "_uow_factory", original_factory)
        retried = await container.controller.transition_instance(command)
        assert retried.revision == instance.revision + 1
        assert retried.status is InstanceStatus.TERMINATED
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_concurrent_first_spec_export_persists_one_spec_and_audit(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir, database_name="spec-concurrent.db")
    first = await _container(settings)
    second = await _container(settings)
    try:
        instance = await _clone_minimal_agent(first)
        exported = await asyncio.gather(
            first.controller.export_spec(instance.instance_id, actor="first-owner"),
            second.controller.export_spec(instance.instance_id, actor="second-owner"),
        )

        assert exported[0] == exported[1]
        audit = await first.controller.query_audit(
            AuditQuery(
                entity_id=str(instance.instance_id),
                event_types=frozenset({AuditEventType.SPEC_EXPORTED}),
            )
        )
        assert audit.total == 1
        async with aiosqlite.connect(
            first.migration_runner.database_path
        ) as connection:
            cursor = await connection.execute(
                """
                SELECT COUNT(*)
                FROM agent_specs
                WHERE instance_id = ? AND revision = ?
                """,
                (str(instance.instance_id), instance.revision),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) == 1
    finally:
        await second.close()
        await first.close()
