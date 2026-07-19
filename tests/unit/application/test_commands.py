"""M1 application command validation tests."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    KnowledgeSelection,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.domain.models import AgentDefinition, DomainKnowledgeDraft


def test_register_commands_accept_valid_domain_inputs(
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    prototype = RegisterPrototypeCommand(
        prototype_id="writer-agent",
        version="1.0.0",
        definition=writer_definition,
        publish=True,
        actor="owner",
        idempotency_key="register-prototype-1",
    )
    knowledge = RegisterKnowledgeCommand(
        knowledge=product_knowledge_draft,
        actor="owner",
        idempotency_key="register-knowledge-1",
    )

    assert prototype.publish is True
    assert knowledge.knowledge.knowledge_id == "agent-factory-docs"


def test_command_rejects_short_idempotency_key(
    writer_definition: AgentDefinition,
) -> None:
    with pytest.raises(ValidationError, match="at least 8"):
        RegisterPrototypeCommand(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=writer_definition,
            actor="owner",
            idempotency_key="short",
        )


def test_clone_command_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        CloneAgentCommand.model_validate(
            {
                "prototype_id": "writer-agent",
                "prototype_version": "1.0.0",
                "actor": "owner",
                "unexpected": True,
            }
        )


def test_bind_command_requires_positive_revision() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        BindKnowledgeCommand(
            instance_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_revision=0,
            selections=(
                KnowledgeSelection(
                    slot_name="product-docs",
                    knowledge_id="agent-factory-docs",
                    version="1.0.0",
                ),
            ),
            actor="owner",
        )
