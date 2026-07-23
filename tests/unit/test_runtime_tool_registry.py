"""Unit tests for the fixed executable tool registry."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from agent_factory.application.runtime import ResolvedRuntimeKnowledge
from agent_factory.application.tool_contracts import ToolExecutionContext
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.models import AgentSpec, KnowledgeRef, PrototypeRef
from agent_factory.infrastructure.runtime import (
    DocumentSearchInput,
    DocumentSearchOutput,
    InMemoryToolRegistry,
    default_tool_registry,
)
from agent_factory.infrastructure.tool_catalog import default_tool_catalog

NOW = datetime(2026, 7, 23, tzinfo=UTC)


def _context() -> ToolExecutionContext:
    inline_content = "Alpha Agent Factory documentation."
    retrieval_content = "Alpha content from an unavailable vector index."
    inline_checksum = checksum_knowledge_content(inline_content)
    retrieval_checksum = checksum_knowledge_content(retrieval_content)
    registry = default_tool_registry()
    definition = registry.definitions()[0]
    spec = AgentSpec(
        instance_id=UUID("00000000-0000-0000-0000-000000001011"),
        revision=3,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum="a" * 64,
        ),
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write using verified knowledge.",
        tools=(definition.resolved_spec(),),
        knowledge=(
            KnowledgeRef(
                slot_name="inline-docs",
                knowledge_id="inline-docs",
                version="1.0.0",
                checksum=inline_checksum,
                injection_mode=InjectionMode.INLINE,
            ),
            KnowledgeRef(
                slot_name="retrieval-docs",
                knowledge_id="retrieval-docs",
                version="1.0.0",
                checksum=retrieval_checksum,
                injection_mode=InjectionMode.RETRIEVAL,
            ),
        ),
        output_schema={"type": "object"},
        runtime_target="demo-runtime",
        generated_at=NOW,
        spec_checksum="b" * 64,
    )
    return ToolExecutionContext(
        spec=spec,
        knowledge=(
            ResolvedRuntimeKnowledge(
                slot_name="inline-docs",
                knowledge_id="inline-docs",
                version="1.0.0",
                checksum=inline_checksum,
                injection_mode=InjectionMode.INLINE,
                mime_type="text/plain",
                content=inline_content,
            ),
            ResolvedRuntimeKnowledge(
                slot_name="retrieval-docs",
                knowledge_id="retrieval-docs",
                version="1.0.0",
                checksum=retrieval_checksum,
                injection_mode=InjectionMode.RETRIEVAL,
                mime_type="text/plain",
                content=retrieval_content,
            ),
        ),
        actor="demo-runtime",
        correlation_id=UUID("00000000-0000-0000-0000-000000001012"),
    )


def test_catalog_metadata_is_derived_from_the_executable_definition() -> None:
    definition = default_tool_registry().definitions()[0]
    resolved = default_tool_catalog().get("document-search")

    assert resolved == definition.resolved_spec()
    assert resolved is not None
    assert resolved.input_schema["additionalProperties"] is False


def test_registry_rejects_duplicate_name_and_version() -> None:
    tool = default_tool_registry().get("document-search", "1.0.0")
    assert tool is not None

    with pytest.raises(ValueError, match="duplicate name/version"):
        InMemoryToolRegistry((tool, tool))


@pytest.mark.asyncio
async def test_document_search_is_deterministic_and_ignores_retrieval_knowledge() -> (
    None
):
    tool = default_tool_registry().get("document-search", "1.0.0")
    assert tool is not None
    context = _context()
    payload = DocumentSearchInput(query="Alpha", top_k=20)

    first = DocumentSearchOutput.model_validate(await tool.handler(payload, context))
    second = DocumentSearchOutput.model_validate(await tool.handler(payload, context))

    assert first == second
    assert len(first.results) == 1
    assert first.results[0].knowledge_id == "inline-docs"
    assert "vector index" not in first.model_dump_json()


@pytest.mark.asyncio
async def test_document_search_returns_empty_for_a_punctuation_only_query() -> None:
    tool = default_tool_registry().get("document-search", "1.0.0")
    assert tool is not None

    result = DocumentSearchOutput.model_validate(
        await tool.handler(DocumentSearchInput(query="..."), _context())
    )

    assert result.results == ()
