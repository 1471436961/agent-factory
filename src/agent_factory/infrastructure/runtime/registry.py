"""Fixed in-memory registry for executable runtime tools."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import cast

from pydantic import Field

from agent_factory.application.tool_contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
)
from agent_factory.domain.common import FrozenModel, canonical_json_bytes
from agent_factory.domain.enums import InjectionMode, ToolPermission


class DocumentSearchInput(FrozenModel):
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)


class DocumentSearchHit(FrozenModel):
    slot_name: str = Field(min_length=1, max_length=64)
    knowledge_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    content: str = Field(max_length=2_000)
    score: float = Field(ge=0, le=1)


class DocumentSearchOutput(FrozenModel):
    results: tuple[DocumentSearchHit, ...] = ()


class InMemoryToolRegistry:
    """Immutable name/version lookup without runtime registration methods."""

    def __init__(self, tools: Iterable[RegisteredTool] = ()) -> None:
        materialized = tuple(tools)
        self._tools = {
            (tool.definition.name, tool.definition.version): tool
            for tool in materialized
        }
        if len(self._tools) != len(materialized):
            raise ValueError("tool registry contains duplicate name/version pairs")

    def get(self, name: str, version: str) -> RegisteredTool | None:
        return self._tools.get((name, version))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool.definition
            for _, tool in sorted(self._tools.items(), key=lambda item: item[0])
        )


async def _document_search(
    payload: FrozenModel,
    context: ToolExecutionContext,
) -> FrozenModel:
    tool_input = cast(DocumentSearchInput, payload)
    query_tokens = _tokens(tool_input.query)
    if not query_tokens:
        return DocumentSearchOutput()

    hits: list[DocumentSearchHit] = []
    for knowledge in context.knowledge:
        if knowledge.injection_mode is not InjectionMode.INLINE:
            continue
        content = _knowledge_text(knowledge.content)
        content_tokens = _tokens(content)
        score = len(query_tokens & content_tokens) / len(query_tokens)
        if score <= 0:
            continue
        hits.append(
            DocumentSearchHit(
                slot_name=knowledge.slot_name,
                knowledge_id=knowledge.knowledge_id,
                version=knowledge.version,
                content=content[:2_000],
                score=score,
            )
        )
    hits.sort(
        key=lambda item: (
            -item.score,
            item.slot_name,
            item.knowledge_id,
            item.version,
        )
    )
    return DocumentSearchOutput(results=tuple(hits[: tool_input.top_k]))


def default_tool_registry() -> InMemoryToolRegistry:
    """Build the Alpha registry containing one bounded, read-only tool."""

    input_schema = DocumentSearchInput.model_json_schema(mode="validation")
    output_schema = DocumentSearchOutput.model_json_schema(mode="validation")
    definition = ToolDefinition(
        name="document-search",
        version="1.0.0",
        description="Search explicitly supplied inline Agent knowledge.",
        input_schema=input_schema,
        output_schema=output_schema,
        permission_tags=frozenset({ToolPermission.READ_ONLY}),
        timeout_seconds=2.0,
    )
    return InMemoryToolRegistry(
        (
            RegisteredTool(
                definition=definition,
                input_model=DocumentSearchInput,
                output_model=DocumentSearchOutput,
                handler=_document_search,
            ),
        )
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _knowledge_text(value: str | Mapping[str, object]) -> str:
    if isinstance(value, str):
        return value
    return canonical_json_bytes(value).decode("utf-8")
