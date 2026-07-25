"""Prove FACTORY experiment input originates from the real production chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    KnowledgeSelection,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import build_container
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    InjectionMode,
    KnowledgeKind,
)
from agent_factory.domain.models import (
    AgentDefinition,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.settings import Settings
from experiments.loader import load_experiment_dataset
from experiments.rendering import (
    load_manual_system_prompt,
    render_factory_invocation,
    render_manual_invocation,
    validate_condition_pair,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"


@pytest.mark.asyncio
async def test_factory_renderer_consumes_controller_exported_spec(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    task = dataset.tasks[0]
    knowledge_bytes = dataset.knowledge_bytes[
        (task.knowledge.knowledge_id, task.knowledge.version)
    ]
    definition = AgentDefinition(
        agent_type="experiment-writer",
        role="Technical Writer",
        system_prompt="Produce accurate documentation from supplied knowledge.",
        capabilities=frozenset({Capability.WRITE}),
        output_schema=task.output_schema,
        knowledge_slots=(
            KnowledgeSlot(
                name="domain-knowledge",
                accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
                min_version=task.knowledge.version,
                injection_mode=InjectionMode.INLINE,
            ),
        ),
    )
    knowledge = DomainKnowledgeDraft(
        knowledge_id=task.knowledge.knowledge_id,
        version=task.knowledge.version,
        name=f"Synthetic fixture for {task.domain_id}",
        kind=KnowledgeKind.DOCUMENT,
        source_uri=AnyHttpUrl(
            f"https://fixtures.invalid/{task.knowledge.knowledge_id}/"
            f"{task.knowledge.version}.md"
        ),
        checksum=task.knowledge.checksum,
    )
    settings = Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
        }
    )
    container = build_container(settings)
    await container.start()
    try:
        prototype = await container.controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="experiment-writer",
                version="1.0.0",
                definition=definition,
                publish=True,
                actor="experiment-owner",
                idempotency_key="m53-register-prototype",
            )
        )
        registered = await container.controller.register_knowledge(
            RegisterKnowledgeCommand(
                knowledge=knowledge,
                actor="experiment-owner",
                idempotency_key="m53-register-knowledge",
            )
        )
        instance = await container.controller.clone_agent(
            CloneAgentCommand(
                prototype_id=prototype.prototype_id,
                prototype_version=prototype.version,
                actor="experiment-owner",
                idempotency_key="m53-clone-instance",
            )
        )
        bound = await container.controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=instance.instance_id,
                expected_revision=instance.revision,
                selections=(
                    KnowledgeSelection(
                        slot_name="domain-knowledge",
                        knowledge_id=registered.knowledge_id,
                        version=registered.version,
                    ),
                ),
                actor="experiment-owner",
                idempotency_key="m53-bind-knowledge",
            )
        )
        spec = await container.controller.export_spec(
            bound.instance_id,
            actor="experiment-owner",
        )

        manual_prompt, _prompt_bytes = load_manual_system_prompt(
            DEFINITION_ROOT / "conditions" / "manual-system.txt"
        )
        manual = render_manual_invocation(
            task=task,
            knowledge_bytes=knowledge_bytes,
            manual_system_prompt=manual_prompt,
        )
        factory = render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge_bytes,
            agent_spec=spec,
        )
        validate_condition_pair(manual, factory)

        audit = await container.controller.query_audit(AuditQuery(page_size=100))
        event_types = {event.event_type for event in audit.items}
        assert {
            AuditEventType.PROTOTYPE_REGISTERED,
            AuditEventType.KNOWLEDGE_REGISTERED,
            AuditEventType.INSTANCE_CLONED,
            AuditEventType.KNOWLEDGE_BOUND,
            AuditEventType.SPEC_EXPORTED,
        } <= event_types
        assert spec.spec_checksum == factory.agent_spec_checksum
        assert spec.knowledge[0].checksum == task.knowledge.checksum
    finally:
        await container.close()
