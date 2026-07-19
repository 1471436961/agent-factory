"""Application-level records used to make commands replayable."""

from typing import Self

from pydantic import AwareDatetime, model_validator

from agent_factory.domain.common import (
    FrozenModel,
    IdempotencyKey,
    JsonObject,
    Sha256,
    Slug,
)


class IdempotencyRecord(FrozenModel):
    idempotency_key: IdempotencyKey
    operation: Slug
    request_hash: Sha256
    response: JsonObject
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def expiry_must_follow_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self
