"""Persistence ports consumed by deterministic application services."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import PrototypeStatus
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
)


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
