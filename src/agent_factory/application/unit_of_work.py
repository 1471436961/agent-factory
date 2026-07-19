"""Transaction boundary shared by business writes and audit events."""

from types import TracebackType
from typing import Protocol, Self

from agent_factory.application.repositories import (
    AgentSpecRepository,
    AuditRepository,
    IdempotencyRepository,
    InstanceRepository,
    KnowledgeRepository,
    PrototypeRepository,
)


class UnitOfWork(Protocol):
    prototypes: PrototypeRepository
    knowledge: KnowledgeRepository
    instances: InstanceRepository
    specs: AgentSpecRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, *, read_only: bool = False) -> UnitOfWork: ...
