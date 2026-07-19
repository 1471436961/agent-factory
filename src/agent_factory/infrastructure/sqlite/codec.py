"""Canonical JSON and projection checks for SQLite persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NoReturn, TypeVar

from pydantic import BaseModel, ValidationError

from agent_factory.domain.common import FrozenJsonObject, canonical_json_bytes
from agent_factory.domain.errors import RepositoryUnavailableError

ModelT = TypeVar("ModelT", bound=BaseModel)


def encode_model(model: BaseModel) -> str:
    """Serialize a model using the same canonical representation as checksums."""

    payload = model.model_dump(mode="json", exclude_none=False)
    return canonical_json_bytes(payload).decode("utf-8")


def encode_json_object(value: Mapping[str, object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def decode_model(
    payload_json: str,
    model_type: type[ModelT],
    *,
    repository: str,
) -> ModelT:
    try:
        return model_type.model_validate_json(payload_json)
    except (ValidationError, ValueError, TypeError) as exc:
        _raise_corrupt(repository, "invalid-payload", exc)


def decode_json_object(
    payload_json: str,
    *,
    repository: str,
) -> FrozenJsonObject:
    try:
        payload = json.loads(payload_json)
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a JSON object")
        return FrozenJsonObject(payload)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        _raise_corrupt(repository, "invalid-payload", exc)


def require_projection(
    condition: bool,
    *,
    repository: str,
    field: str,
) -> None:
    if not condition:
        _raise_corrupt(repository, f"projection-mismatch:{field}")


def raise_database_error(repository: str, exc: BaseException) -> NoReturn:
    error = RepositoryUnavailableError(
        details={"repository": repository, "reason": "database-error"}
    )
    raise error from exc


def _raise_corrupt(
    repository: str,
    reason: str,
    exc: BaseException | None = None,
) -> NoReturn:
    error = RepositoryUnavailableError(
        details={"repository": repository, "reason": reason}
    )
    if exc is None:
        raise error
    raise error from exc
