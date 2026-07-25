"""Default Runtime capability and offline execution security invariants."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from agent_factory.application.tool_contracts import ToolExecutionContext
from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.models import AgentSpec, PrototypeRef
from agent_factory.infrastructure.runtime import (
    DocumentSearchInput,
    DocumentSearchOutput,
    default_tool_registry,
)


def _empty_context() -> ToolExecutionContext:
    definition = default_tool_registry().definitions()[0]
    spec = AgentSpec(
        instance_id=UUID("00000000-0000-0000-0000-000000004302"),
        revision=1,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum="a" * 64,
        ),
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Use only supplied knowledge.",
        tools=(definition.resolved_spec(),),
        knowledge=(),
        output_schema={"type": "object"},
        runtime_target="demo-runtime",
        generated_at=datetime(2026, 7, 25, tzinfo=UTC),
        spec_checksum="b" * 64,
    )
    return ToolExecutionContext(
        spec=spec,
        knowledge=(),
        actor="offline-runtime",
        correlation_id=UUID("00000000-0000-0000-0000-000000004303"),
    )


def test_default_registry_has_one_fixed_read_only_capability() -> None:
    registry = default_tool_registry()
    definitions = registry.definitions()

    assert tuple(
        (
            item.name,
            item.version,
            item.enabled,
            item.permission_tags,
        )
        for item in definitions
    ) == (
        (
            "document-search",
            "1.0.0",
            True,
            frozenset({ToolPermission.READ_ONLY}),
        ),
    )
    assert not hasattr(registry, "register")
    assert not hasattr(registry, "remove")
    assert not hasattr(registry, "enable")


@pytest.mark.asyncio
async def test_default_document_search_does_not_open_network_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[object] = []

    def reject_socket_connect(
        self: socket.socket,
        address: object,
    ) -> None:
        del self
        attempts.append(address)
        raise AssertionError("default tool attempted socket.connect")

    def reject_create_connection(
        address: tuple[str, int],
        timeout: object = None,
        source_address: tuple[str, int] | None = None,
        all_errors: bool = False,
    ) -> socket.socket:
        del timeout, source_address, all_errors
        attempts.append(address)
        raise AssertionError("default tool attempted socket.create_connection")

    async def reject_async_connection(*args: Any, **kwargs: Any) -> None:
        attempts.append((args, kwargs))
        raise AssertionError("default tool attempted asyncio.open_connection")

    monkeypatch.setattr(socket.socket, "connect", reject_socket_connect)
    monkeypatch.setattr(socket, "create_connection", reject_create_connection)
    monkeypatch.setattr(asyncio, "open_connection", reject_async_connection)

    tool = default_tool_registry().get("document-search", "1.0.0")
    assert tool is not None
    result = DocumentSearchOutput.model_validate(
        await tool.handler(
            DocumentSearchInput(query="offline security check"),
            _empty_context(),
        )
    )

    assert result.results == ()
    assert attempts == []
