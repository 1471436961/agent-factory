"""Request hashing and typed replay for idempotent write operations."""

from datetime import datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.repositories import IdempotencyRepository
from agent_factory.domain.common import sha256_model
from agent_factory.domain.errors import (
    IdempotencyKeyReusedError,
    RepositoryUnavailableError,
)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class IdempotencyService:
    """Replay successful command responses while detecting key reuse."""

    def __init__(self, *, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = timedelta(seconds=ttl_seconds)

    async def replay(
        self,
        *,
        repository: IdempotencyRepository,
        command: BaseModel,
        operation: str,
        response_type: type[ResponseT],
        now: datetime,
    ) -> ResponseT | None:
        key = getattr(command, "idempotency_key", None)
        if key is None:
            return None

        await repository.delete_expired(now)
        request_hash = self.request_hash(command)
        record = await repository.get(key)
        if record is None:
            return None
        if record.operation != operation or record.request_hash != request_hash:
            raise IdempotencyKeyReusedError(
                details={
                    "idempotency_key": key,
                    "original_operation": record.operation,
                    "requested_operation": operation,
                }
            )
        try:
            return response_type.model_validate(record.response)
        except (ValidationError, ValueError, TypeError) as exc:
            error = RepositoryUnavailableError(
                details={
                    "repository": "idempotency",
                    "reason": "invalid-cached-response",
                }
            )
            raise error from exc

    async def store(
        self,
        *,
        repository: IdempotencyRepository,
        command: BaseModel,
        operation: str,
        response: BaseModel,
        now: datetime,
    ) -> None:
        key = getattr(command, "idempotency_key", None)
        if key is None:
            return
        await repository.add(
            IdempotencyRecord(
                idempotency_key=key,
                operation=operation,
                request_hash=self.request_hash(command),
                response=response.model_dump(mode="json"),
                created_at=now,
                expires_at=now + self._ttl,
            )
        )

    @staticmethod
    def request_hash(command: BaseModel) -> str:
        return sha256_model(command, exclude={"idempotency_key"})
