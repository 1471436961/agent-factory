"""Unit tests for immutable runtime tool contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.tool_contracts import (
    RegisteredTool,
    ToolCallRecord,
    ToolCallStatus,
    ToolDefinition,
    ToolExecutionContext,
)
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import ToolPermission
from agent_factory.infrastructure.runtime import (
    DocumentSearchInput,
    DocumentSearchOutput,
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)


async def _handler(
    payload: object,
    context: object,
) -> DocumentSearchOutput:
    del payload, context
    return DocumentSearchOutput()


def _definition() -> ToolDefinition:
    return ToolDefinition(
        name="document-search",
        version="1.0.0",
        description="Search inline documents.",
        input_schema=DocumentSearchInput.model_json_schema(mode="validation"),
        output_schema=DocumentSearchOutput.model_json_schema(mode="validation"),
        permission_tags=frozenset({ToolPermission.READ_ONLY}),
    )


def _record(**updates: object) -> ToolCallRecord:
    payload: dict[str, object] = {
        "call_id": UUID("00000000-0000-0000-0000-000000001001"),
        "task_id": UUID("00000000-0000-0000-0000-000000001002"),
        "instance_id": UUID("00000000-0000-0000-0000-000000001003"),
        "instance_revision": 3,
        "agent_spec_checksum": "a" * 64,
        "tool_name": "document-search",
        "tool_version": "1.0.0",
        "status": "succeeded",
        "arguments_hash": "b" * 64,
        "result_hash": "c" * 64,
        "error_code": None,
        "duration_ms": 4,
        "actor": "demo-runtime",
        "correlation_id": UUID("00000000-0000-0000-0000-000000001004"),
        "started_at": NOW,
        "completed_at": NOW,
    }
    payload.update(updates)
    return ToolCallRecord.model_validate(payload)


def test_tool_definition_resolves_only_agent_visible_metadata() -> None:
    definition = _definition()

    resolved = definition.resolved_spec()

    assert resolved.name == definition.name
    assert resolved.input_schema == definition.input_schema
    assert "timeout_seconds" not in resolved.model_dump(mode="json")
    assert "enabled" not in resolved.model_dump(mode="json")


def test_registered_tool_rejects_schema_drift() -> None:
    definition = _definition().model_copy(update={"input_schema": {"type": "object"}})

    with pytest.raises(ValueError, match="input schema does not match"):
        RegisteredTool(
            definition=definition,
            input_model=DocumentSearchInput,
            output_model=DocumentSearchOutput,
            handler=_handler,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"result_hash": None},
        {"error_code": "TOOL_EXECUTION_FAILED"},
        {
            "status": ToolCallStatus.FAILED,
            "result_hash": "c" * 64,
            "error_code": "TOOL_EXECUTION_FAILED",
        },
        {"completed_at": NOW - timedelta(seconds=1)},
    ],
)
def test_tool_call_record_rejects_inconsistent_terminal_state(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _record(**updates)


def test_failed_record_requires_only_a_stable_error_code() -> None:
    record = _record(
        status=ToolCallStatus.FAILED,
        result_hash=None,
        error_code="TOOL_EXECUTION_FAILED",
    )

    assert record.status is ToolCallStatus.FAILED
    assert record.error_code == "TOOL_EXECUTION_FAILED"


def test_tool_context_rejects_knowledge_not_declared_by_spec() -> None:
    from agent_factory.domain.models import AgentSpec, PrototypeRef

    spec = AgentSpec(
        instance_id=UUID("00000000-0000-0000-0000-000000001005"),
        revision=1,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum="d" * 64,
        ),
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write.",
        tools=(),
        knowledge=(),
        output_schema={"type": "object"},
        generated_at=NOW,
        spec_checksum="e" * 64,
    )

    mismatched_content = "mismatched"
    with pytest.raises(ValidationError, match="does not match AgentSpec"):
        ToolExecutionContext.model_validate(
            {
                "spec": spec,
                "knowledge": [
                    {
                        "slot_name": "product-docs",
                        "knowledge_id": "agent-factory-docs",
                        "version": "1.0.0",
                        "checksum": checksum_knowledge_content(mismatched_content),
                        "injection_mode": "inline",
                        "mime_type": "text/plain",
                        "content": mismatched_content,
                    }
                ],
                "actor": "demo-runtime",
                "correlation_id": UUID("00000000-0000-0000-0000-000000001006"),
            }
        )
