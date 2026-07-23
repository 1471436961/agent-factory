"""Behavioral tests for Factory Tool authorization and invocation."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    PromoteAgentCommand,
)
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.application.security import (
    AuthorizationPolicy,
    FactoryRole,
    Principal,
)
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import (
    AuditEntityType,
    AuditEventType,
    InstanceStatus,
    PrototypeStatus,
)
from agent_factory.domain.errors import PrototypeNotFoundError
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    PrototypeRef,
)
from agent_factory.infrastructure.system import ContextVarCorrelationContext
from agent_factory.interfaces.factory_tools import (
    FactoryToolAdapter,
    FactoryToolCallContext,
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000911")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000912")
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000913")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000914")


class _RecordingController:
    def __init__(self, correlation_context: ContextVarCorrelationContext) -> None:
        definition = AgentDefinition(
            agent_type="engineer-agent",
            role="Software Engineer",
            system_prompt="Produce verifiable work.",
        )
        self.prototype = AgentPrototype(
            prototype_id="engineer-agent",
            version="1.0.0",
            status=PrototypeStatus.PUBLISHED,
            definition=definition,
            checksum="a" * 64,
            created_at=NOW,
            created_by="owner",
            published_at=NOW,
        )
        self.instance = AgentInstance(
            instance_id=INSTANCE_ID,
            prototype=PrototypeRef(
                prototype_id="engineer-agent",
                version="1.0.0",
                checksum="a" * 64,
            ),
            revision=1,
            status=InstanceStatus.CREATED,
            configuration=definition,
            created_at=NOW,
            updated_at=NOW,
            created_by="owner",
        )
        self.audit_event = AuditEvent(
            event_id=UUID("00000000-0000-0000-0000-000000000915"),
            event_type=AuditEventType.INSTANCE_CLONED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(INSTANCE_ID),
            entity_revision=1,
            actor="owner",
            correlation_id=CORRELATION_ID,
            payload={},
            created_at=NOW,
        )
        self.correlation_context = correlation_context
        self.seen_correlation: str | None = None
        self.clone_commands: list[CloneAgentCommand] = []
        self.bind_commands: list[BindKnowledgeCommand] = []
        self.promotion_commands: list[PromoteAgentCommand] = []
        self.prototype_queries: list[PrototypeListQuery] = []
        self.audit_queries: list[AuditQuery] = []
        self.clone_error: Exception | None = None
        self.cancel_clone = False
        self.return_invalid_clone_output = False

    async def list_prototypes(
        self,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]:
        self.prototype_queries.append(query)
        self.seen_correlation = self.correlation_context.get()
        return Page(items=(self.prototype,), page=1, page_size=20, total=1)

    async def clone_agent(self, command: CloneAgentCommand) -> AgentInstance:
        self.clone_commands.append(command)
        self.seen_correlation = self.correlation_context.get()
        if self.cancel_clone:
            raise asyncio.CancelledError
        if self.clone_error is not None:
            raise self.clone_error
        if self.return_invalid_clone_output:
            return cast(AgentInstance, self.prototype)
        return self.instance

    async def bind_knowledge(self, command: BindKnowledgeCommand) -> AgentInstance:
        self.bind_commands.append(command)
        self.seen_correlation = self.correlation_context.get()
        return self.instance

    async def promote_agent(self, command: PromoteAgentCommand) -> AgentInstance:
        self.promotion_commands.append(command)
        self.seen_correlation = self.correlation_context.get()
        return self.instance

    async def query_audit(self, query: AuditQuery) -> Page[AuditEvent]:
        self.audit_queries.append(query)
        self.seen_correlation = self.correlation_context.get()
        return Page(items=(self.audit_event,), page=1, page_size=20, total=1)


def _principal(role: FactoryRole = FactoryRole.ADMIN) -> Principal:
    return Principal(subject="tool-owner", roles=frozenset({role}))


def _context(
    *,
    role: FactoryRole = FactoryRole.ADMIN,
    idempotency_key: str | None = None,
) -> FactoryToolCallContext:
    return FactoryToolCallContext(
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        principal=_principal(role),
        idempotency_key=idempotency_key,
    )


def _adapter() -> tuple[
    FactoryToolAdapter,
    _RecordingController,
    ContextVarCorrelationContext,
]:
    correlation_context = ContextVarCorrelationContext()
    controller = _RecordingController(correlation_context)
    adapter = FactoryToolAdapter(
        controller=controller,
        authorization_policy=AuthorizationPolicy(),
        correlation_context=correlation_context,
    )
    return adapter, controller, correlation_context


@pytest.mark.asyncio
async def test_invoke_maps_commands_and_restores_nested_correlation_context() -> None:
    adapter, controller, correlation_context = _adapter()
    outer_token = correlation_context.set("outer-correlation")
    try:
        clone = await adapter.invoke(
            "clone_agent",
            {
                "prototype_id": "engineer-agent",
                "version": "1.0.0",
                "runtime_target": "local-runtime",
            },
            _context(),
        )

        assert clone.ok is True
        assert clone.output is not None
        assert clone.output["instance_id"] == str(INSTANCE_ID)
        assert controller.seen_correlation == str(CORRELATION_ID)
        assert correlation_context.get() == "outer-correlation"
        assert controller.clone_commands == [
            CloneAgentCommand(
                prototype_id="engineer-agent",
                prototype_version="1.0.0",
                runtime_target="local-runtime",
                actor="tool-owner",
                idempotency_key=f"tool:clone_agent:{REQUEST_ID}",
            )
        ]

        await adapter.invoke(
            "bind_knowledge",
            {
                "instance_id": str(INSTANCE_ID),
                "expected_revision": 1,
                "selections": [
                    {
                        "slot_name": "product-docs",
                        "knowledge_id": "agent-factory-docs",
                        "version": "1.0.0",
                    }
                ],
            },
            _context(idempotency_key="shared-bind-key"),
        )
        assert controller.bind_commands[0].actor == "tool-owner"
        assert controller.bind_commands[0].idempotency_key == "shared-bind-key"

        await adapter.invoke(
            "apply_promotion",
            {
                "instance_id": str(INSTANCE_ID),
                "expected_revision": 1,
                "target_node_id": "junior-engineer",
                "evaluation_report_id": str(REPORT_ID),
            },
            _context(),
        )
        assert controller.promotion_commands[0].idempotency_key == (
            f"tool:apply_promotion:{REQUEST_ID}"
        )
    finally:
        correlation_context.reset(outer_token)


@pytest.mark.asyncio
async def test_read_tools_map_filters_and_return_validated_pages() -> None:
    adapter, controller, _ = _adapter()

    prototypes = await adapter.invoke(
        "list_prototypes",
        {"status": "published", "page": 1, "page_size": 5},
        _context(),
    )
    audit = await adapter.invoke(
        "query_audit_log",
        {"event_types": ["instance.cloned"], "page_size": 10},
        _context(),
    )

    assert prototypes.ok is True
    assert audit.ok is True
    assert controller.prototype_queries[0].page_size == 5
    assert controller.audit_queries[0].event_types == frozenset(
        {AuditEventType.INSTANCE_CLONED}
    )


@pytest.mark.asyncio
async def test_authorization_runs_before_input_validation() -> None:
    adapter, controller, _ = _adapter()

    result = await adapter.invoke(
        "clone_agent",
        {"secret": "must-not-be-validated-or-returned"},
        _context(role=FactoryRole.VIEWER),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "AUTHORIZATION_DENIED"
    assert result.error.details["required_permission"] == "factory:write"
    assert controller.clone_commands == []
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_unknown_and_invalid_inputs_use_safe_error_envelopes() -> None:
    adapter, _, _ = _adapter()

    unknown = await adapter.invoke("missing_tool", {}, _context())
    invalid = await adapter.invoke(
        "clone_agent",
        {
            "prototype_id": "x",
            "version": "not-semver",
            "api_key": "top-secret-value",
        },
        _context(),
    )

    assert unknown.error is not None
    assert unknown.error.code == "FACTORY_TOOL_NOT_FOUND"
    assert invalid.error is not None
    assert invalid.error.code == "TOOL_INPUT_VALIDATION_FAILED"
    dumped = invalid.model_dump_json()
    assert "top-secret-value" not in dumped
    errors = invalid.error.details["errors"]
    assert isinstance(errors, tuple)
    first_error = errors[0]
    assert isinstance(first_error, Mapping)
    assert set(first_error) == {
        "location",
        "message",
        "type",
    }


@pytest.mark.asyncio
async def test_domain_and_unexpected_errors_preserve_only_safe_information(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, controller, _ = _adapter()
    controller.clone_error = PrototypeNotFoundError(
        details={"prototype_id": "engineer-agent", "version": "1.0.0"}
    )

    domain_result = await adapter.invoke(
        "clone_agent",
        {"prototype_id": "engineer-agent", "version": "1.0.0"},
        _context(),
    )
    assert domain_result.error is not None
    assert domain_result.error.code == "PROTOTYPE_NOT_FOUND"
    assert domain_result.error.details["version"] == "1.0.0"

    controller.clone_error = RuntimeError("do-not-log-or-return-this-secret")
    unexpected = await adapter.invoke(
        "clone_agent",
        {"prototype_id": "engineer-agent", "version": "1.0.0"},
        _context(),
    )
    assert unexpected.error is not None
    assert unexpected.error.code == "INTERNAL_ERROR"
    assert "secret" not in unexpected.model_dump_json()
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_invalid_controller_output_is_rejected() -> None:
    adapter, controller, _ = _adapter()
    controller.return_invalid_clone_output = True

    result = await adapter.invoke(
        "clone_agent",
        {"prototype_id": "engineer-agent", "version": "1.0.0"},
        _context(),
    )

    assert result.error is not None
    assert result.error.code == "TOOL_OUTPUT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_cancellation_propagates_and_restores_correlation_context() -> None:
    adapter, controller, correlation_context = _adapter()
    controller.cancel_clone = True
    outer_token = correlation_context.set("outer-correlation")
    try:
        with pytest.raises(asyncio.CancelledError):
            await adapter.invoke(
                "clone_agent",
                {"prototype_id": "engineer-agent", "version": "1.0.0"},
                _context(),
            )
        assert correlation_context.get() == "outer-correlation"
    finally:
        correlation_context.reset(outer_token)
