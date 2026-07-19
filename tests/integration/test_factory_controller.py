"""Real SQLite integration tests for M1.3 application orchestration."""

from pathlib import Path

import pytest

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    DeprecatePrototypeCommand,
    KnowledgeSelection,
    PublishPrototypeCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.application.queries import AuditQuery, PrototypeListQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import (
    AuditEventType,
    InjectionMode,
    KnowledgeKind,
    PrototypeStatus,
)
from agent_factory.domain.errors import (
    IdempotencyKeyReusedError,
    InvalidPrototypeStatusError,
    KnowledgeAlreadyBoundError,
    KnowledgeChecksumMismatchError,
    KnowledgeNotFoundError,
    MissingKnowledgeBindingError,
    PrototypeNotPublishedError,
    RevisionConflictError,
    UnknownToolError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.settings import Settings


async def _container(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    max_inline_knowledge_bytes: int = 262_144,
) -> Container:
    settings = Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "max_inline_knowledge_bytes": max_inline_knowledge_bytes,
        }
    )
    container = build_container(settings)
    await container.start()
    return container


@pytest.mark.asyncio
async def test_controller_runs_replayable_audited_production_chain(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller
    register = RegisterPrototypeCommand(
        prototype_id="writer-agent",
        version="1.0.0",
        definition=writer_definition,
        publish=True,
        actor="owner",
        idempotency_key="register-prototype-1",
    )
    knowledge_command = RegisterKnowledgeCommand(
        knowledge=product_knowledge_draft,
        actor="owner",
        idempotency_key="register-knowledge-1",
    )

    try:
        prototype = await controller.register_prototype(register)
        assert await controller.register_prototype(register) == prototype
        page = await controller.list_prototypes(
            PrototypeListQuery(status=PrototypeStatus.PUBLISHED)
        )
        assert page.items == (prototype,)

        knowledge = await controller.register_knowledge(knowledge_command)
        clone_command = CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            runtime_target="local-runtime",
            actor="owner",
            idempotency_key="clone-writer-agent-1",
        )
        instance = await controller.clone_agent(clone_command)
        assert await controller.clone_agent(clone_command) == instance

        bind_command = BindKnowledgeCommand(
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
            idempotency_key="bind-product-docs-1",
        )
        bound = await controller.bind_knowledge(bind_command)
        assert await controller.bind_knowledge(bind_command) == bound
        assert bound.revision == 2

        first_spec = await controller.export_spec(
            bound.instance_id,
            actor="owner",
        )
        second_spec = await controller.export_spec(
            bound.instance_id,
            actor="owner",
        )
        assert first_spec == second_spec
        assert first_spec.tools[0].name == "document-search"
        assert first_spec.knowledge[0].knowledge_id == knowledge.knowledge_id

        audit = await controller.query_audit(AuditQuery(page_size=100))
        assert audit.total == 6
        assert (
            sum(
                event.event_type is AuditEventType.SPEC_EXPORTED
                for event in audit.items
            )
            == 1
        )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_controller_rolls_back_failed_validation_and_detects_key_reuse(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller
    unknown_tool_definition = writer_definition.model_copy(
        update={"tools": ("unknown-tool",)}
    )
    invalid_register = RegisterPrototypeCommand(
        prototype_id="invalid-agent",
        version="1.0.0",
        definition=unknown_tool_definition,
        actor="owner",
        idempotency_key="invalid-prototype-1",
    )

    try:
        with pytest.raises(UnknownToolError):
            await controller.register_prototype(invalid_register)
        assert (await controller.query_audit(AuditQuery())).total == 0

        draft_register = RegisterPrototypeCommand(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=writer_definition,
            actor="owner",
            idempotency_key="register-prototype-1",
        )
        await controller.register_prototype(draft_register)
        with pytest.raises(IdempotencyKeyReusedError):
            await controller.register_prototype(
                draft_register.model_copy(update={"actor": "reviewer"})
            )
        with pytest.raises(PrototypeNotPublishedError):
            await controller.clone_agent(
                CloneAgentCommand(
                    prototype_id="writer-agent",
                    prototype_version="1.0.0",
                    actor="owner",
                )
            )

        bad_knowledge = product_knowledge_draft.model_copy(
            update={"checksum": "0" * 64}
        )
        with pytest.raises(KnowledgeChecksumMismatchError):
            await controller.register_knowledge(
                RegisterKnowledgeCommand(
                    knowledge=bad_knowledge,
                    actor="owner",
                )
            )
        audit = await controller.query_audit(AuditQuery(page_size=100))
        assert audit.total == 1
        assert audit.items[0].event_type is AuditEventType.PROTOTYPE_REGISTERED
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_controller_enforces_binding_revision_and_replacement_rules(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        prototype = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="writer-agent",
                version="1.0.0",
                definition=writer_definition,
                publish=True,
                actor="owner",
            )
        )
        instance = await controller.clone_agent(
            CloneAgentCommand(
                prototype_id=prototype.prototype_id,
                prototype_version=prototype.version,
                actor="owner",
            )
        )
        with pytest.raises(MissingKnowledgeBindingError):
            await controller.export_spec(instance.instance_id, actor="owner")

        knowledge = await controller.register_knowledge(
            RegisterKnowledgeCommand(
                knowledge=product_knowledge_draft,
                actor="owner",
            )
        )
        selection = KnowledgeSelection(
            slot_name="product-docs",
            knowledge_id=knowledge.knowledge_id,
            version=knowledge.version,
        )
        with pytest.raises(RevisionConflictError):
            await controller.bind_knowledge(
                BindKnowledgeCommand(
                    instance_id=instance.instance_id,
                    expected_revision=2,
                    selections=(selection,),
                    actor="owner",
                )
            )
        bound = await controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=instance.instance_id,
                expected_revision=1,
                selections=(selection,),
                actor="owner",
            )
        )
        with pytest.raises(KnowledgeAlreadyBoundError):
            await controller.bind_knowledge(
                BindKnowledgeCommand(
                    instance_id=instance.instance_id,
                    expected_revision=bound.revision,
                    selections=(selection,),
                    actor="owner",
                )
            )
        replaced = await controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=instance.instance_id,
                expected_revision=bound.revision,
                selections=(selection,),
                replace_existing=True,
                actor="owner",
            )
        )
        assert replaced.revision == 3
        assert replaced.knowledge_bindings[0].knowledge_id == knowledge.knowledge_id

        with pytest.raises(KnowledgeNotFoundError):
            await controller.bind_knowledge(
                BindKnowledgeCommand(
                    instance_id=instance.instance_id,
                    expected_revision=replaced.revision,
                    selections=(
                        KnowledgeSelection(
                            slot_name="product-docs",
                            knowledge_id="missing-knowledge",
                            version="1.0.0",
                        ),
                    ),
                    replace_existing=True,
                    actor="owner",
                )
            )

        audit = await controller.query_audit(AuditQuery(page_size=100))
        bound_events = tuple(
            event
            for event in audit.items
            if event.event_type is AuditEventType.KNOWLEDGE_BOUND
        )
        assert len(bound_events) == 2
        assert {event.payload["replaced"] for event in bound_events} == {
            False,
            True,
        }
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_binding_preserves_untouched_slot_provenance(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller
    definition = writer_definition.model_copy(
        update={
            "knowledge_slots": (
                *writer_definition.knowledge_slots,
                KnowledgeSlot(
                    name="style-guide",
                    required=False,
                    accepted_kinds=frozenset({KnowledgeKind.POLICY}),
                    injection_mode=InjectionMode.INLINE,
                ),
            )
        }
    )
    style_content = "Use concise technical language."
    style_draft = DomainKnowledgeDraft(
        knowledge_id="technical-style-guide",
        version="1.0.0",
        name="Technical Style Guide",
        kind=KnowledgeKind.POLICY,
        content=style_content,
        checksum=checksum_knowledge_content(style_content),
    )

    try:
        prototype = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="writer-agent",
                version="1.0.0",
                definition=definition,
                publish=True,
                actor="owner",
            )
        )
        product = await controller.register_knowledge(
            RegisterKnowledgeCommand(
                knowledge=product_knowledge_draft,
                actor="owner",
            )
        )
        style = await controller.register_knowledge(
            RegisterKnowledgeCommand(knowledge=style_draft, actor="reviewer")
        )
        instance = await controller.clone_agent(
            CloneAgentCommand(
                prototype_id=prototype.prototype_id,
                prototype_version=prototype.version,
                actor="owner",
            )
        )
        product_bound = await controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=instance.instance_id,
                expected_revision=1,
                selections=(
                    KnowledgeSelection(
                        slot_name="product-docs",
                        knowledge_id=product.knowledge_id,
                        version=product.version,
                    ),
                ),
                actor="owner",
            )
        )
        original_product_binding = product_bound.knowledge_bindings[0]

        both_bound = await controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=instance.instance_id,
                expected_revision=2,
                selections=(
                    KnowledgeSelection(
                        slot_name="style-guide",
                        knowledge_id=style.knowledge_id,
                        version=style.version,
                    ),
                ),
                actor="reviewer",
            )
        )

        preserved = next(
            item
            for item in both_bound.knowledge_bindings
            if item.slot_name == "product-docs"
        )
        assert preserved == original_product_binding
        assert preserved.bound_by == "owner"
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_controller_publishes_deprecates_and_replays_status_changes(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        draft = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="writer-agent",
                version="1.0.0",
                definition=writer_definition,
                actor="owner",
            )
        )
        publish = PublishPrototypeCommand(
            prototype_id=draft.prototype_id,
            version=draft.version,
            actor="owner",
            idempotency_key="publish-prototype-1",
        )
        published = await controller.publish_prototype(publish)
        assert await controller.publish_prototype(publish) == published
        assert published.status is PrototypeStatus.PUBLISHED

        deprecate = DeprecatePrototypeCommand(
            prototype_id=published.prototype_id,
            version=published.version,
            reason="Replaced by version 2.",
            actor="owner",
            idempotency_key="deprecate-prototype-1",
        )
        deprecated = await controller.deprecate_prototype(deprecate)
        assert await controller.deprecate_prototype(deprecate) == deprecated
        assert deprecated.status is PrototypeStatus.DEPRECATED
        assert deprecated.deprecation_reason == deprecate.reason

        with pytest.raises(InvalidPrototypeStatusError):
            await controller.publish_prototype(
                PublishPrototypeCommand(
                    prototype_id=deprecated.prototype_id,
                    version=deprecated.version,
                    actor="owner",
                )
            )
        with pytest.raises(PrototypeNotPublishedError):
            await controller.clone_agent(
                CloneAgentCommand(
                    prototype_id=deprecated.prototype_id,
                    prototype_version=deprecated.version,
                    actor="owner",
                )
            )

        audit = await controller.query_audit(AuditQuery(page_size=100))
        assert {event.event_type for event in audit.items} == {
            AuditEventType.PROTOTYPE_REGISTERED,
            AuditEventType.PROTOTYPE_PUBLISHED,
            AuditEventType.PROTOTYPE_DEPRECATED,
        }
        assert audit.total == 3
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_invalid_correlation_rolls_back_staged_business_write(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    token = container.correlation_context.set("not-a-uuid")

    try:
        with pytest.raises(RuntimeError, match="must contain a UUID"):
            await container.controller.register_prototype(
                RegisterPrototypeCommand(
                    prototype_id="writer-agent",
                    version="1.0.0",
                    definition=writer_definition,
                    actor="owner",
                )
            )
    finally:
        container.correlation_context.reset(token)

    try:
        prototypes = await container.controller.list_prototypes(PrototypeListQuery())
        audit = await container.controller.query_audit(AuditQuery())
        assert prototypes.total == 0
        assert audit.total == 0
    finally:
        await container.close()
