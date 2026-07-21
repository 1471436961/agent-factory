"""Persistence ports consumed by deterministic application services."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import PrototypeStatus
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
)
from agent_factory.domain.skills import SkillTree, TaskOutcome


class PrototypeRepository(Protocol):
    async def add(self, prototype: AgentPrototype) -> None: ...

    async def get(
        self,
        prototype_id: str,
        version: str,
    ) -> AgentPrototype | None: ...

    async def list(
        self,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]: ...

    async def replace(
        self,
        prototype: AgentPrototype,
        expected_status: PrototypeStatus,
    ) -> bool: ...


class KnowledgeRepository(Protocol):
    async def add(self, knowledge: DomainKnowledge) -> None: ...

    async def get(
        self,
        knowledge_id: str,
        version: str,
    ) -> DomainKnowledge | None: ...

    async def get_many(
        self,
        refs: tuple[tuple[str, str], ...],
    ) -> tuple[DomainKnowledge, ...]: ...


class InstanceRepository(Protocol):
    async def add(self, instance: AgentInstance) -> None: ...

    async def get(
        self,
        instance_id: UUID,
        revision: int | None = None,
    ) -> AgentInstance | None: ...

    async def save_snapshot(
        self,
        instance: AgentInstance,
        expected_revision: int,
    ) -> None: ...


class AgentSpecRepository(Protocol):
    async def get(
        self,
        instance_id: UUID,
        revision: int,
    ) -> AgentSpec | None: ...

    async def add_if_absent(self, spec: AgentSpec) -> bool: ...


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...

    async def query(self, query: AuditQuery) -> Page[AuditEvent]: ...


class IdempotencyRepository(Protocol):
    async def get(self, idempotency_key: str) -> IdempotencyRecord | None: ...

    async def add(self, record: IdempotencyRecord) -> None: ...

    async def delete_expired(self, expired_at: datetime) -> int: ...


class SkillTreeRepository(Protocol):
    async def add(self, tree: SkillTree) -> None: ...

    async def get(self, tree_id: str, version: str) -> SkillTree | None: ...


class EvaluationSuiteRepository(Protocol):
    async def add(self, suite: EvaluationSuite) -> None: ...

    async def get(self, suite_id: str, version: str) -> EvaluationSuite | None: ...


class EvaluationReportRepository(Protocol):
    async def add(self, report: EvaluationReport) -> None: ...

    async def get(self, report_id: UUID) -> EvaluationReport | None: ...


class EvaluationReviewRepository(Protocol):
    async def add(self, review: EvaluationReview) -> None: ...

    async def get(self, review_id: UUID) -> EvaluationReview | None: ...

    async def get_for_report(self, report_id: UUID) -> EvaluationReview | None: ...


class TaskOutcomeRepository(Protocol):
    async def append(
        self,
        *,
        instance_id: UUID,
        instance_revision: int,
        outcome: TaskOutcome,
    ) -> None: ...

    async def list_for_node(
        self,
        *,
        instance_id: UUID,
        instance_revision: int,
        skill_node_id: str,
        limit: int,
    ) -> tuple[TaskOutcome, ...]: ...
