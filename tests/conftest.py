"""Shared deterministic fixtures for Agent Factory tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import Capability, InjectionMode, KnowledgeKind
from agent_factory.domain.models import (
    AgentDefinition,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest temporary files inside the workspace on restricted hosts."""

    (config.rootpath / ".tmp").mkdir(exist_ok=True)


@pytest.fixture
def migrations_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_factory"
        / "infrastructure"
        / "sqlite"
        / "sql"
    )


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 7, 15, tzinfo=UTC)


@pytest.fixture
def product_slot() -> KnowledgeSlot:
    return KnowledgeSlot(
        name="product-docs",
        required=True,
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        min_version="1.0.0",
        injection_mode=InjectionMode.RETRIEVAL,
    )


@pytest.fixture
def writer_definition(product_slot: KnowledgeSlot) -> AgentDefinition:
    return AgentDefinition(
        agent_type="writer-agent",
        role="Technical Writer",
        system_prompt="Write using the bound product documentation.",
        tools=("document-search",),
        capabilities=frozenset({Capability.WRITE}),
        output_schema={
            "type": "object",
            "required": ["title", "body"],
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "additionalProperties": False,
        },
        knowledge_slots=(product_slot,),
    )


@pytest.fixture
def product_knowledge_draft() -> DomainKnowledgeDraft:
    content = "# Agent Factory\nProduction governance."
    return DomainKnowledgeDraft(
        knowledge_id="agent-factory-docs",
        version="1.0.0",
        name="Agent Factory Product Docs",
        kind=KnowledgeKind.DOCUMENT,
        content=content,
        checksum=checksum_knowledge_content(content),
    )
