"""Immutable primitives and canonical serialization for domain snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Annotated, Any, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonBuiltin: TypeAlias = JsonScalar | list["JsonBuiltin"] | dict[str, "JsonBuiltin"]


class FrozenJsonObject(Mapping[str, object]):
    """Recursively immutable JSON object with deterministic serialization."""

    __slots__ = ("_data",)

    def __init__(self, value: Mapping[str, object] | None = None) -> None:
        source = value or {}
        frozen: dict[str, object] = {}
        for key, item in source.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            frozen[key] = _freeze_json_value(item, path=f"$.{key}")
        self._data: Mapping[str, object] = MappingProxyType(frozen)

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self.to_builtin()!r})"

    def __hash__(self) -> int:
        return hash(canonical_json_bytes(self))

    def to_builtin(self) -> dict[str, JsonBuiltin]:
        """Return a detached JSON-compatible dictionary."""

        return {key: _thaw_json_value(value) for key, value in self._data.items()}


def _freeze_json_object(value: object) -> FrozenJsonObject:
    if isinstance(value, FrozenJsonObject):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("value must be a JSON object")
    return FrozenJsonObject(value)


def _serialize_json_object(value: Mapping[str, object]) -> dict[str, JsonBuiltin]:
    if isinstance(value, FrozenJsonObject):
        return value.to_builtin()
    return FrozenJsonObject(value).to_builtin()


def _freeze_json_value(value: object, *, path: str) -> object:
    if isinstance(value, FrozenJsonObject):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(value)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain a finite JSON number")
        return value
    raise ValueError(f"{path} contains unsupported JSON type {type(value).__name__}")


def _thaw_json_value(value: object) -> JsonBuiltin:
    if isinstance(value, FrozenJsonObject):
        return value.to_builtin()
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"unsupported frozen JSON type {type(value).__name__}")


JsonObject = Annotated[
    Mapping[str, object],
    AfterValidator(_freeze_json_object),
    PlainSerializer(
        _serialize_json_object,
        return_type=dict[str, Any],
        when_used="always",
    ),
]

Slug = Annotated[
    str,
    Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
    ),
]
SemVer = Annotated[
    str,
    Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Actor = Annotated[str, Field(min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=128)]


class FrozenModel(BaseModel):
    """Strict base model for immutable domain snapshots and commands."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


def semver_tuple(version: str) -> tuple[int, int, int]:
    """Convert an already validated semantic version for numeric comparison."""

    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("version must use MAJOR.MINOR.PATCH")
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        raise ValueError("version components must not contain leading zeros")
    major, minor, patch = parts
    return int(major), int(minor), int(patch)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON value using the project's canonical byte representation."""

    frozen = _freeze_json_value(value, path="$")
    builtin = _thaw_json_value(frozen)
    return json.dumps(
        builtin,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_model(model: BaseModel, *, exclude: set[str] | None = None) -> str:
    """Hash a Pydantic model after canonical JSON serialization."""

    payload = model.model_dump(
        mode="json",
        exclude=exclude or set(),
        exclude_none=False,
    )
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def checksum_knowledge_content(
    content: str | FrozenJsonObject | Mapping[str, object],
) -> str:
    """Hash inline knowledge according to its string or JSON representation."""

    if isinstance(content, str):
        encoded = content.encode("utf-8")
    else:
        encoded = canonical_json_bytes(content)
    return hashlib.sha256(encoded).hexdigest()
