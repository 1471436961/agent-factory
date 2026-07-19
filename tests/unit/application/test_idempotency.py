"""Unit tests for typed idempotency replay and conflict detection."""

from datetime import datetime, timedelta

import pytest

from agent_factory.application.commands import RegisterPrototypeCommand
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.domain.errors import (
    IdempotencyKeyReusedError,
    RepositoryUnavailableError,
)
from agent_factory.domain.models import AgentDefinition, AgentPrototype


class MemoryIdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}

    async def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        return self.records.get(idempotency_key)

    async def add(self, record: IdempotencyRecord) -> None:
        self.records[record.idempotency_key] = record

    async def delete_expired(self, expired_at: datetime) -> int:
        expired = [
            key
            for key, record in self.records.items()
            if record.expires_at <= expired_at
        ]
        for key in expired:
            del self.records[key]
        return len(expired)


def _command(definition: AgentDefinition) -> RegisterPrototypeCommand:
    return RegisterPrototypeCommand(
        prototype_id="writer-agent",
        version="1.0.0",
        definition=definition,
        actor="owner",
        idempotency_key="register-prototype-1",
    )


@pytest.mark.asyncio
async def test_idempotency_replays_typed_response_and_excludes_key_from_hash(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    service = IdempotencyService(ttl_seconds=3_600)
    repository = MemoryIdempotencyRepository()
    command = _command(writer_definition)
    response = AgentPrototype(
        prototype_id=command.prototype_id,
        version=command.version,
        definition=command.definition,
        checksum="a" * 64,
        created_at=fixed_now,
        created_by=command.actor,
    )

    await service.store(
        repository=repository,
        command=command,
        operation="register-prototype",
        response=response,
        now=fixed_now,
    )
    replay = await service.replay(
        repository=repository,
        command=command,
        operation="register-prototype",
        response_type=AgentPrototype,
        now=fixed_now,
    )

    changed_key = command.model_copy(update={"idempotency_key": "another-key-1"})
    assert replay == response
    assert service.request_hash(command) == service.request_hash(changed_key)


@pytest.mark.asyncio
async def test_idempotency_rejects_key_reuse_and_removes_expired_records(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    service = IdempotencyService(ttl_seconds=3_600)
    repository = MemoryIdempotencyRepository()
    command = _command(writer_definition)
    repository.records[command.idempotency_key or ""] = IdempotencyRecord(
        idempotency_key=command.idempotency_key or "missing-key",
        operation="clone-agent",
        request_hash=service.request_hash(command),
        response={"instance_id": "00000000-0000-0000-0000-000000000001"},
        created_at=fixed_now,
        expires_at=fixed_now + timedelta(hours=1),
    )

    with pytest.raises(IdempotencyKeyReusedError):
        await service.replay(
            repository=repository,
            command=command,
            operation="register-prototype",
            response_type=AgentPrototype,
            now=fixed_now,
        )

    expired = next(iter(repository.records.values())).model_copy(
        update={"expires_at": fixed_now - timedelta(seconds=1)}
    )
    repository.records[expired.idempotency_key] = expired
    assert (
        await service.replay(
            repository=repository,
            command=command,
            operation="register-prototype",
            response_type=AgentPrototype,
            now=fixed_now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_idempotency_rejects_corrupt_cached_response(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    service = IdempotencyService(ttl_seconds=3_600)
    repository = MemoryIdempotencyRepository()
    command = _command(writer_definition)
    repository.records[command.idempotency_key or ""] = IdempotencyRecord(
        idempotency_key=command.idempotency_key or "missing-key",
        operation="register-prototype",
        request_hash=service.request_hash(command),
        response={"prototype_id": "writer-agent"},
        created_at=fixed_now,
        expires_at=fixed_now + timedelta(hours=1),
    )

    with pytest.raises(RepositoryUnavailableError) as caught:
        await service.replay(
            repository=repository,
            command=command,
            operation="register-prototype",
            response_type=AgentPrototype,
            now=fixed_now,
        )

    assert caught.value.details["reason"] == "invalid-cached-response"


def test_idempotency_requires_positive_ttl() -> None:
    with pytest.raises(ValueError, match="positive"):
        IdempotencyService(ttl_seconds=0)
