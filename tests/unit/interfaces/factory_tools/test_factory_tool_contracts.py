"""Contract tests for provider-neutral Factory Tool definitions."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.queries import Page
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
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    PrototypeRef,
)
from agent_factory.infrastructure.system import ContextVarCorrelationContext
from agent_factory.interfaces.factory_tools import (
    FactoryToolAdapter,
    FactoryToolError,
    FactoryToolResult,
    QueryAuditLogToolInput,
)

NOW = datetime(2026, 7, 23, tzinfo=UTC)
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000901")


class _StaticController:
    def __init__(self) -> None:
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
            event_id=UUID("00000000-0000-0000-0000-000000000902"),
            event_type=AuditEventType.INSTANCE_CLONED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(INSTANCE_ID),
            entity_revision=1,
            actor="owner",
            correlation_id=UUID("00000000-0000-0000-0000-000000000903"),
            payload={},
            created_at=NOW,
        )

    async def list_prototypes(self, query: object) -> Page[AgentPrototype]:
        del query
        return Page(items=(self.prototype,), page=1, page_size=20, total=1)

    async def clone_agent(self, command: object) -> AgentInstance:
        del command
        return self.instance

    async def bind_knowledge(self, command: object) -> AgentInstance:
        del command
        return self.instance

    async def promote_agent(self, command: object) -> AgentInstance:
        del command
        return self.instance

    async def query_audit(self, query: object) -> Page[AuditEvent]:
        del query
        return Page(items=(self.audit_event,), page=1, page_size=20, total=1)


def _adapter() -> FactoryToolAdapter:
    return FactoryToolAdapter(
        controller=_StaticController(),
        authorization_policy=AuthorizationPolicy(),
        correlation_context=ContextVarCorrelationContext(),
    )


def _principal(role: FactoryRole) -> Principal:
    return Principal(subject="owner", roles=frozenset({role}))


def test_definitions_are_generated_from_strict_models_without_trusted_context() -> None:
    definitions = _adapter().definitions(_principal(FactoryRole.ADMIN))

    assert tuple(item.name for item in definitions) == (
        "apply_promotion",
        "bind_knowledge",
        "clone_agent",
        "list_prototypes",
        "query_audit_log",
    )
    forbidden = {
        "actor",
        "principal",
        "request_id",
        "correlation_id",
        "idempotency_key",
    }
    for definition in definitions:
        schema = definition.input_schema
        assert schema["additionalProperties"] is False
        properties = schema["properties"]
        assert isinstance(properties, Mapping)
        assert forbidden.isdisjoint(properties)
        assert definition.output_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (FactoryRole.VIEWER, {"list_prototypes"}),
        (
            FactoryRole.OPERATOR,
            {
                "apply_promotion",
                "bind_knowledge",
                "clone_agent",
                "list_prototypes",
            },
        ),
        (FactoryRole.AUDITOR, {"list_prototypes", "query_audit_log"}),
        (
            FactoryRole.ADMIN,
            {
                "apply_promotion",
                "bind_knowledge",
                "clone_agent",
                "list_prototypes",
                "query_audit_log",
            },
        ),
    ],
)
def test_definitions_follow_the_application_permission_matrix(
    role: FactoryRole,
    expected: set[str],
) -> None:
    assert {item.name for item in _adapter().definitions(_principal(role))} == expected


def test_result_envelope_rejects_inconsistent_success_and_error_fields() -> None:
    with pytest.raises(ValidationError, match="successful result requires output"):
        FactoryToolResult(
            request_id=INSTANCE_ID,
            correlation_id=INSTANCE_ID,
            ok=True,
        )

    with pytest.raises(ValidationError, match="failed result requires error"):
        FactoryToolResult(
            request_id=INSTANCE_ID,
            correlation_id=INSTANCE_ID,
            ok=False,
            output={},
            error=FactoryToolError(code="INTERNAL_ERROR", message="failure"),
        )


def test_audit_tool_rejects_an_inverted_time_range() -> None:
    with pytest.raises(ValidationError, match="created_from must not exceed"):
        QueryAuditLogToolInput.model_validate(
            {
                "created_from": "2026-07-24T00:00:00Z",
                "created_to": "2026-07-23T00:00:00Z",
            }
        )
