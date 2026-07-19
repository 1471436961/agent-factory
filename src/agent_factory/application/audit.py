"""Allowlisted audit event construction for M1 production operations."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from agent_factory.application.ports import IdGenerator
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import AuditEntityType, AuditEventType
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    KnowledgeBinding,
)


class AuditEventFactory:
    """Construct events from operation-specific, non-sensitive payloads."""

    def __init__(self, id_generator: IdGenerator) -> None:
        self._id_generator = id_generator

    def prototype_registered(
        self,
        prototype: AgentPrototype,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.PROTOTYPE_REGISTERED,
            entity_type=AuditEntityType.PROTOTYPE,
            entity_id=prototype.prototype_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "version": prototype.version,
                "checksum": prototype.checksum,
                "status": prototype.status.value,
            },
            at=at,
        )

    def prototype_published(
        self,
        prototype: AgentPrototype,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._prototype_status_event(
            AuditEventType.PROTOTYPE_PUBLISHED,
            prototype,
            actor=actor,
            correlation_id=correlation_id,
            at=at,
        )

    def prototype_deprecated(
        self,
        prototype: AgentPrototype,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._prototype_status_event(
            AuditEventType.PROTOTYPE_DEPRECATED,
            prototype,
            actor=actor,
            correlation_id=correlation_id,
            at=at,
        )

    def knowledge_registered(
        self,
        knowledge: DomainKnowledge,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.KNOWLEDGE_REGISTERED,
            entity_type=AuditEntityType.KNOWLEDGE,
            entity_id=knowledge.knowledge_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "version": knowledge.version,
                "kind": knowledge.kind.value,
                "checksum": knowledge.checksum,
                "source": "inline" if knowledge.content is not None else "uri",
            },
            at=at,
        )

    def instance_cloned(
        self,
        instance: AgentInstance,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.INSTANCE_CLONED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(instance.instance_id),
            entity_revision=instance.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "prototype_id": instance.prototype.prototype_id,
                "prototype_version": instance.prototype.version,
                "prototype_checksum": instance.prototype.checksum,
                "runtime_target": instance.runtime_target,
            },
            at=at,
        )

    def knowledge_bound(
        self,
        instance: AgentInstance,
        binding: KnowledgeBinding,
        *,
        replaced: bool,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.KNOWLEDGE_BOUND,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(instance.instance_id),
            entity_revision=instance.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "slot_name": binding.slot_name,
                "knowledge_id": binding.knowledge_id,
                "knowledge_version": binding.knowledge_version,
                "knowledge_checksum": binding.knowledge_checksum,
                "injection_mode": binding.injection_mode.value,
                "replaced": replaced,
            },
            at=at,
        )

    def spec_exported(
        self,
        spec: AgentSpec,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.SPEC_EXPORTED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(spec.instance_id),
            entity_revision=spec.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "schema_version": spec.schema_version,
                "spec_checksum": spec.spec_checksum,
                "runtime_target": spec.runtime_target,
            },
            at=at,
        )

    def _prototype_status_event(
        self,
        event_type: AuditEventType,
        prototype: AgentPrototype,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=event_type,
            entity_type=AuditEntityType.PROTOTYPE,
            entity_id=prototype.prototype_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "version": prototype.version,
                "status": prototype.status.value,
            },
            at=at,
        )

    def _event(
        self,
        *,
        event_type: AuditEventType,
        entity_type: AuditEntityType,
        entity_id: str,
        actor: str,
        correlation_id: UUID,
        payload: Mapping[str, object],
        at: datetime,
        entity_revision: int | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_id=self._id_generator.new(),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_revision=entity_revision,
            actor=actor,
            correlation_id=correlation_id,
            payload=payload,
            created_at=at,
        )
