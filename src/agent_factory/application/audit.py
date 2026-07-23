"""Allowlisted audit event construction for production operations."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from agent_factory.application.ports import IdGenerator
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import AuditEntityType, AuditEventType
from agent_factory.domain.evaluation import (
    EvaluationReport,
    EvaluationReview,
    EvaluationSuite,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    KnowledgeBinding,
)
from agent_factory.domain.skills import (
    DegradationDecision,
    SkillTree,
    TaskOutcome,
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
                "skill_tree": (
                    None
                    if prototype.skill_tree is None
                    else prototype.skill_tree.model_dump(mode="json")
                ),
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
                "skill_tree": (
                    None
                    if instance.skill_tree is None
                    else instance.skill_tree.model_dump(mode="json")
                ),
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

    def instance_transitioned(
        self,
        previous: AgentInstance,
        current: AgentInstance,
        *,
        reason: str,
        retry: bool,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.INSTANCE_TRANSITIONED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(current.instance_id),
            entity_revision=current.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "from_status": previous.status.value,
                "to_status": current.status.value,
                "from_revision": previous.revision,
                "to_revision": current.revision,
                "reason": reason,
                "retry": retry,
            },
            at=at,
        )

    def evaluation_suite_registered(
        self,
        suite: EvaluationSuite,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.EVALUATION_SUITE_REGISTERED,
            entity_type=AuditEntityType.EVALUATION,
            entity_id=suite.suite_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "version": suite.version,
                "checksum": suite.checksum,
                "rule_count": len(suite.rules),
                "case_count": len(suite.cases),
                "require_manual_review": suite.require_manual_review,
            },
            at=at,
        )

    def skill_tree_registered(
        self,
        tree: SkillTree,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.SKILL_TREE_REGISTERED,
            entity_type=AuditEntityType.SKILL,
            entity_id=tree.tree_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "version": tree.version,
                "checksum": tree.checksum,
                "node_ids": tuple(node.node_id for node in tree.nodes),
            },
            at=at,
        )

    def evaluation_completed(
        self,
        report: EvaluationReport,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.EVALUATION_COMPLETED,
            entity_type=AuditEntityType.EVALUATION,
            entity_id=str(report.report_id),
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "instance_id": str(report.instance_id),
                "instance_revision": report.instance_revision,
                "agent_spec_checksum": report.agent_spec_checksum,
                "skill_tree": report.skill_tree.model_dump(mode="json"),
                "suite": report.suite.model_dump(mode="json"),
                "decision": report.decision.value,
                "hard_rules_passed": report.hard_rules_passed,
                "soft_score": report.soft_score,
            },
            at=at,
        )

    def evaluation_reviewed(
        self,
        review: EvaluationReview,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.EVALUATION_REVIEWED,
            entity_type=AuditEntityType.EVALUATION,
            entity_id=str(review.report_id),
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "review_id": str(review.review_id),
                "decision": review.decision.value,
            },
            at=at,
        )

    def skill_promoted(
        self,
        previous: AgentInstance,
        promoted: AgentInstance,
        *,
        node_id: str,
        report_id: UUID,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.SKILL_PROMOTED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(promoted.instance_id),
            entity_revision=promoted.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "from_revision": previous.revision,
                "to_revision": promoted.revision,
                "node_id": node_id,
                "report_id": str(report_id),
            },
            at=at,
        )

    def task_outcome_recorded(
        self,
        instance: AgentInstance,
        outcome: TaskOutcome,
        decision: DegradationDecision,
        *,
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.TASK_OUTCOME_RECORDED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(instance.instance_id),
            entity_revision=instance.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "task_id": str(outcome.task_id),
                "node_id": outcome.skill_node_id,
                "passed": outcome.passed,
                "report_id": str(outcome.evaluation_report_id),
                "sample_count": decision.sample_count,
                "trailing_failures": decision.trailing_failures,
                "failure_rate": decision.failure_rate,
                "threshold_reached": decision.should_degrade,
            },
            at=at,
        )

    def skill_degraded(
        self,
        previous: AgentInstance,
        degraded: AgentInstance,
        decision: DegradationDecision,
        *,
        node_id: str,
        removed_nodes: frozenset[str],
        removed_binding_slots: frozenset[str],
        actor: str,
        correlation_id: UUID,
        at: datetime,
    ) -> AuditEvent:
        return self._event(
            event_type=AuditEventType.SKILL_DEGRADED,
            entity_type=AuditEntityType.INSTANCE,
            entity_id=str(degraded.instance_id),
            entity_revision=degraded.revision,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "from_revision": previous.revision,
                "to_revision": degraded.revision,
                "node_id": node_id,
                "sample_count": decision.sample_count,
                "trailing_failures": decision.trailing_failures,
                "failure_rate": decision.failure_rate,
                "removed_nodes": tuple(sorted(removed_nodes)),
                "removed_binding_slots": tuple(sorted(removed_binding_slots)),
                "configuration_checksum": sha256_model(degraded.configuration),
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
