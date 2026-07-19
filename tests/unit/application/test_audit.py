"""Unit tests for allowlisted M1 audit payloads."""

from datetime import datetime
from uuid import UUID

from agent_factory.application.audit import AuditEventFactory
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import AuditEventType, KnowledgeKind
from agent_factory.domain.models import DomainKnowledge

EVENT_ID = UUID("00000000-0000-0000-0000-000000000201")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000301")


class FixedIdGenerator:
    def new(self) -> UUID:
        return EVENT_ID


def test_knowledge_audit_payload_excludes_content(fixed_now: datetime) -> None:
    content = "sensitive product instructions"
    knowledge = DomainKnowledge(
        knowledge_id="agent-factory-docs",
        version="1.0.0",
        name="Product Docs",
        kind=KnowledgeKind.DOCUMENT,
        content=content,
        checksum=checksum_knowledge_content(content),
        created_at=fixed_now,
        created_by="owner",
    )

    event = AuditEventFactory(FixedIdGenerator()).knowledge_registered(
        knowledge,
        actor="owner",
        correlation_id=CORRELATION_ID,
        at=fixed_now,
    )

    assert event.event_type is AuditEventType.KNOWLEDGE_REGISTERED
    assert event.event_id == EVENT_ID
    assert "content" not in event.payload
    assert content not in str(event.model_dump(mode="json")["payload"])
