"""SQLite transaction, concurrency, and recovery tests for M3.2 lifecycle."""

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    KnowledgeSelection,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    TransitionInstanceCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.enums import AuditEventType, InstanceStatus
from agent_factory.domain.errors import (
    IdempotencyKeyReusedError,
    MissingKnowledgeBindingError,
    RepositoryUnavailableError,
    RevisionConflictError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    DomainKnowledgeDraft,
)
from agent_factory.settings import Settings


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    database_name: str = "factory.db",
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


async def _clone_writer(
    container: Container,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
    *,
    bind_knowledge: bool = True,
) -> AgentInstance:
    controller = container.controller
    prototype = await controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=writer_definition,
            publish=True,
            actor="owner",
        )
    )
    knowledge = await controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=product_knowledge_draft,
            actor="owner",
        )
    )
    instance = await controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            runtime_target="demo-runtime",
            actor="owner",
        )
    )
    if not bind_knowledge:
        return instance
    return await controller.bind_knowledge(
        BindKnowledgeCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            selections=(
                KnowledgeSelection(
                    slot_name="product-docs",
                    knowledge_id=knowledge.knowledge_id,
                    version=knowledge.version,
                ),
            ),
            actor="owner",
        )
    )


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


@pytest.mark.asyncio
async def test_transition_is_replayable_audited_and_requires_new_spec_export(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(_settings(tmp_path, migrations_dir))
    try:
        bound = await _clone_writer(
            container,
            writer_definition,
            product_knowledge_draft,
        )
        command = TransitionInstanceCommand(
            instance_id=bound.instance_id,
            expected_revision=bound.revision,
            target_status=InstanceStatus.RUNNING,
            reason="start deterministic runtime",
            actor="owner",
            idempotency_key="transition-running-1",
        )

        running = await container.controller.transition_instance(command)
        replay = await container.controller.transition_instance(command)

        assert replay == running
        assert running.revision == bound.revision + 1
        assert running.status is InstanceStatus.RUNNING
        async with container.uow_factory(read_only=True) as uow:
            assert await uow.specs.get(running.instance_id, running.revision) is None
        spec = await container.controller.export_spec(
            running.instance_id,
            revision=running.revision,
            actor="owner",
        )
        assert spec.revision == running.revision

        audit = await container.controller.query_audit(AuditQuery(page_size=100))
        transitions = tuple(
            event
            for event in audit.items
            if event.event_type is AuditEventType.INSTANCE_TRANSITIONED
        )
        assert len(transitions) == 1
        assert transitions[0].entity_revision == running.revision
        assert transitions[0].payload == {
            "from_status": "created",
            "to_status": "running",
            "from_revision": bound.revision,
            "to_revision": running.revision,
            "reason": "start deterministic runtime",
            "retry": False,
        }

        with pytest.raises(IdempotencyKeyReusedError):
            await container.controller.transition_instance(
                command.model_copy(update={"reason": "different request"})
            )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_running_readiness_failure_has_no_state_or_idempotency_side_effect(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(_settings(tmp_path, migrations_dir))
    try:
        instance = await _clone_writer(
            container,
            writer_definition,
            product_knowledge_draft,
            bind_knowledge=False,
        )
        command = TransitionInstanceCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            target_status=InstanceStatus.RUNNING,
            reason="must fail without required knowledge",
            actor="owner",
            idempotency_key="transition-unready-1",
        )

        with pytest.raises(MissingKnowledgeBindingError):
            await container.controller.transition_instance(command)

        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(instance.instance_id)
            record = await uow.idempotency.get("transition-unready-1")
        assert current == instance
        assert record is None
        audit = await container.controller.query_audit(AuditQuery(page_size=100))
        assert all(
            event.event_type is not AuditEventType.INSTANCE_TRANSITIONED
            for event in audit.items
        )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_stale_transition_revision_is_rejected_without_extra_snapshot(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(_settings(tmp_path, migrations_dir))
    try:
        instance = await _clone_minimal_agent(container)
        terminated = await container.controller.transition_instance(
            TransitionInstanceCommand(
                instance_id=instance.instance_id,
                expected_revision=instance.revision,
                target_status=InstanceStatus.TERMINATED,
                reason="administrative stop",
                actor="owner",
            )
        )

        with pytest.raises(RevisionConflictError):
            await container.controller.transition_instance(
                TransitionInstanceCommand(
                    instance_id=instance.instance_id,
                    expected_revision=instance.revision,
                    target_status=InstanceStatus.RUNNING,
                    reason="stale start",
                    actor="owner",
                )
            )

        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(instance.instance_id)
            nonexistent = await uow.instances.get(
                instance.instance_id,
                revision=terminated.revision + 1,
            )
        assert current == terminated
        assert nonexistent is None
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_concurrent_transitions_from_same_revision_have_one_winner(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir, database_name="concurrent.db")
    first = await _container(settings)
    second = await _container(settings)
    try:
        instance = await _clone_minimal_agent(first)
        results = await asyncio.gather(
            first.controller.transition_instance(
                TransitionInstanceCommand(
                    instance_id=instance.instance_id,
                    expected_revision=instance.revision,
                    target_status=InstanceStatus.RUNNING,
                    reason="start runtime",
                    actor="first-owner",
                    idempotency_key="transition-concurrent-running",
                )
            ),
            second.controller.transition_instance(
                TransitionInstanceCommand(
                    instance_id=instance.instance_id,
                    expected_revision=instance.revision,
                    target_status=InstanceStatus.TERMINATED,
                    reason="terminate runtime",
                    actor="second-owner",
                    idempotency_key="transition-concurrent-terminated",
                )
            ),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, AgentInstance)]
        conflicts = [
            item for item in results if isinstance(item, RevisionConflictError)
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0].revision == instance.revision + 1

        async with first.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(instance.instance_id)
        assert current == successes[0]
        audit = await first.controller.query_audit(AuditQuery(page_size=100))
        assert (
            sum(
                event.event_type is AuditEventType.INSTANCE_TRANSITIONED
                for event in audit.items
            )
            == 1
        )
    finally:
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_inserted_snapshot(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir, database_name="rollback.db")
    container = await _container(settings)
    database_path = tmp_path / "rollback.db"
    try:
        instance = await _clone_minimal_agent(container)
        async with aiosqlite.connect(database_path) as connection:
            await connection.executescript(
                """
                CREATE TRIGGER fail_instance_transition_audit
                BEFORE INSERT ON audit_events
                WHEN NEW.event_type = 'instance.transitioned'
                BEGIN
                    SELECT RAISE(ABORT, 'injected transition audit failure');
                END;
                """
            )
            await connection.commit()

        with pytest.raises(RepositoryUnavailableError):
            await container.controller.transition_instance(
                TransitionInstanceCommand(
                    instance_id=instance.instance_id,
                    expected_revision=instance.revision,
                    target_status=InstanceStatus.TERMINATED,
                    reason="exercise rollback",
                    actor="owner",
                    idempotency_key="transition-rollback-1",
                )
            )

        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(instance.instance_id)
            rolled_back = await uow.instances.get(
                instance.instance_id,
                revision=instance.revision + 1,
            )
            idempotency = await uow.idempotency.get("transition-rollback-1")
        assert current == instance
        assert rolled_back is None
        assert idempotency is None
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_transition_and_idempotency_replay_survive_container_restart(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir, database_name="restart.db")
    first = await _container(settings)
    instance = await _clone_minimal_agent(first)
    command = TransitionInstanceCommand(
        instance_id=instance.instance_id,
        expected_revision=instance.revision,
        target_status=InstanceStatus.RUNNING,
        reason="start before restart",
        actor="owner",
        idempotency_key="transition-restart-1",
    )
    running = await first.controller.transition_instance(command)
    await first.close()

    second = await _container(settings)
    try:
        replay = await second.controller.transition_instance(command)
        async with second.uow_factory(read_only=True) as uow:
            restored = await uow.instances.get(instance.instance_id)
        audit = await second.controller.query_audit(AuditQuery(page_size=100))

        assert replay == running
        assert restored == running
        assert (
            sum(
                event.event_type is AuditEventType.INSTANCE_TRANSITIONED
                for event in audit.items
            )
            == 1
        )
    finally:
        await second.close()
