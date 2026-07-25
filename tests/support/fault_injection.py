"""Test-only UoW decorator for deterministic transaction fault injection.

Every repository delegate still writes to a real file-backed SQLite transaction.
The decorator raises only after a selected delegate call has completed, allowing
the underlying UoW context manager to prove that staged facts are rolled back.
It deliberately does not model process death, disk corruption, or WAL durability.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Self
from uuid import UUID

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, Page
from agent_factory.application.repositories import (
    AgentSpecRepository,
    AuditRepository,
    EvaluationReportRepository,
    EvaluationReviewRepository,
    EvaluationSuiteRepository,
    IdempotencyRepository,
    InstanceRepository,
    KnowledgeRepository,
    PrototypeRepository,
    SkillTreeRepository,
    TaskOutcomeRepository,
    ToolCallRepository,
)
from agent_factory.application.tool_contracts import ToolCallRecord
from agent_factory.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.models import AgentInstance


class FaultPoint(StrEnum):
    AFTER_ENTITY_WRITE = "after-entity-write"
    AFTER_AUDIT_WRITE = "after-audit-write"
    AFTER_IDEMPOTENCY_WRITE = "after-idempotency-write"
    BEFORE_COMMIT = "before-commit"


class EntityWriteTarget(StrEnum):
    INSTANCE = "instance"
    TOOL_CALL = "tool-call"


class InjectedTransactionFailure(RuntimeError):
    """Stable exception raised after a selected staged write."""

    def __init__(self, point: FaultPoint) -> None:
        self.point = point
        super().__init__(f"injected transaction failure at {point.value}")


class _FaultInjector:
    def __init__(
        self,
        point: FaultPoint,
        entity_target: EntityWriteTarget,
    ) -> None:
        self._point = point
        self._entity_target = entity_target

    def trip(
        self,
        point: FaultPoint,
        *,
        entity_target: EntityWriteTarget | None = None,
    ) -> None:
        if self._point is not point:
            return
        if entity_target is not None and self._entity_target is not entity_target:
            return
        raise InjectedTransactionFailure(point)


class _FaultingInstanceRepository:
    def __init__(
        self,
        delegate: InstanceRepository,
        injector: _FaultInjector,
    ) -> None:
        self._delegate = delegate
        self._injector = injector

    async def add(self, instance: AgentInstance) -> None:
        await self._delegate.add(instance)
        self._injector.trip(
            FaultPoint.AFTER_ENTITY_WRITE,
            entity_target=EntityWriteTarget.INSTANCE,
        )

    async def get(
        self,
        instance_id: UUID,
        revision: int | None = None,
    ) -> AgentInstance | None:
        return await self._delegate.get(instance_id, revision)

    async def save_snapshot(
        self,
        instance: AgentInstance,
        expected_revision: int,
    ) -> None:
        await self._delegate.save_snapshot(instance, expected_revision)
        self._injector.trip(
            FaultPoint.AFTER_ENTITY_WRITE,
            entity_target=EntityWriteTarget.INSTANCE,
        )


class _FaultingAuditRepository:
    def __init__(
        self,
        delegate: AuditRepository,
        injector: _FaultInjector,
    ) -> None:
        self._delegate = delegate
        self._injector = injector

    async def append(self, event: AuditEvent) -> None:
        await self._delegate.append(event)
        self._injector.trip(FaultPoint.AFTER_AUDIT_WRITE)

    async def query(self, query: AuditQuery) -> Page[AuditEvent]:
        return await self._delegate.query(query)


class _FaultingIdempotencyRepository:
    def __init__(
        self,
        delegate: IdempotencyRepository,
        injector: _FaultInjector,
    ) -> None:
        self._delegate = delegate
        self._injector = injector

    async def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        return await self._delegate.get(idempotency_key)

    async def add(self, record: IdempotencyRecord) -> None:
        await self._delegate.add(record)
        self._injector.trip(FaultPoint.AFTER_IDEMPOTENCY_WRITE)

    async def delete_expired(self, expired_at: datetime) -> int:
        return await self._delegate.delete_expired(expired_at)


class _FaultingToolCallRepository:
    def __init__(
        self,
        delegate: ToolCallRepository,
        injector: _FaultInjector,
    ) -> None:
        self._delegate = delegate
        self._injector = injector

    async def add(self, record: ToolCallRecord) -> None:
        await self._delegate.add(record)
        self._injector.trip(
            FaultPoint.AFTER_ENTITY_WRITE,
            entity_target=EntityWriteTarget.TOOL_CALL,
        )

    async def get(self, call_id: UUID) -> ToolCallRecord | None:
        return await self._delegate.get(call_id)


class _FaultInjectingUnitOfWork:
    prototypes: PrototypeRepository
    knowledge: KnowledgeRepository
    instances: InstanceRepository
    specs: AgentSpecRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository
    skill_trees: SkillTreeRepository
    evaluation_suites: EvaluationSuiteRepository
    evaluation_reports: EvaluationReportRepository
    evaluation_reviews: EvaluationReviewRepository
    task_outcomes: TaskOutcomeRepository
    tool_calls: ToolCallRepository

    def __init__(
        self,
        delegate: UnitOfWork,
        *,
        point: FaultPoint,
        entity_target: EntityWriteTarget,
    ) -> None:
        self._delegate = delegate
        self._injector = _FaultInjector(point, entity_target)

    async def __aenter__(self) -> Self:
        entered = await self._delegate.__aenter__()
        self.prototypes = entered.prototypes
        self.knowledge = entered.knowledge
        self.instances = _FaultingInstanceRepository(
            entered.instances,
            self._injector,
        )
        self.specs = entered.specs
        self.audit = _FaultingAuditRepository(entered.audit, self._injector)
        self.idempotency = _FaultingIdempotencyRepository(
            entered.idempotency,
            self._injector,
        )
        self.skill_trees = entered.skill_trees
        self.evaluation_suites = entered.evaluation_suites
        self.evaluation_reports = entered.evaluation_reports
        self.evaluation_reviews = entered.evaluation_reviews
        self.task_outcomes = entered.task_outcomes
        self.tool_calls = _FaultingToolCallRepository(
            entered.tool_calls,
            self._injector,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._delegate.__aexit__(exc_type, exc, traceback)

    async def commit(self) -> None:
        self._injector.trip(FaultPoint.BEFORE_COMMIT)
        await self._delegate.commit()

    async def rollback(self) -> None:
        await self._delegate.rollback()


class FaultInjectingUnitOfWorkFactory:
    """Wrap write UoWs while leaving verification reads untouched."""

    def __init__(
        self,
        delegate: UnitOfWorkFactory,
        *,
        point: FaultPoint,
        entity_target: EntityWriteTarget,
    ) -> None:
        self._delegate = delegate
        self._point = point
        self._entity_target = entity_target

    def __call__(self, *, read_only: bool = False) -> UnitOfWork:
        delegate = self._delegate(read_only=read_only)
        if read_only:
            return delegate
        return _FaultInjectingUnitOfWork(
            delegate,
            point=self._point,
            entity_target=self._entity_target,
        )
