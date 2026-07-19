"""Immutable JSON and canonical serialization tests."""

from typing import cast

import pytest
from pydantic import Field, ValidationError

from agent_factory.domain.common import (
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    canonical_json_bytes,
    checksum_knowledge_content,
    semver_tuple,
    sha256_model,
)


class Payload(FrozenModel):
    name: str
    document: JsonObject = Field(default_factory=FrozenJsonObject)


def test_frozen_json_recursively_blocks_mutation_and_serializes_to_json() -> None:
    payload = Payload(
        name="example",
        document={"nested": {"value": 1}, "items": [1, 2]},
    )

    nested = payload.document["nested"]
    items = payload.document["items"]
    assert isinstance(nested, FrozenJsonObject)
    assert isinstance(items, tuple)

    with pytest.raises(TypeError):
        cast(dict[str, object], payload.document)["new"] = True
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["value"] = 2
    with pytest.raises(AttributeError):
        cast(list[object], items).append(3)

    assert payload.model_dump(mode="json")["document"] == {
        "nested": {"value": 1},
        "items": [1, 2],
    }


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_frozen_json_rejects_non_json_values(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Payload(name="invalid", document={"value": invalid})


def test_canonical_json_and_checksum_are_order_independent() -> None:
    left = {"b": [True, None], "a": 1}
    right = {"a": 1, "b": [True, None]}

    assert canonical_json_bytes(left) == b'{"a":1,"b":[true,null]}'
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert checksum_knowledge_content(left) == (
        "1cc69c7fa23616ca2ec3ee70d24390a6225c8832db8a4c814c7e0e7f942f8668"
    )


def test_string_knowledge_checksum_uses_original_utf8_bytes() -> None:
    assert checksum_knowledge_content(" a ") != checksum_knowledge_content("a")


def test_sha256_model_can_exclude_self_checksum_field() -> None:
    first = Payload(name="one", document={"value": 1})
    second = Payload(name="two", document={"value": 1})

    assert sha256_model(first) != sha256_model(second)
    assert sha256_model(first, exclude={"name"}) == sha256_model(
        second,
        exclude={"name"},
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.0.0", (0, 0, 0)), ("2.10.3", (2, 10, 3))],
)
def test_semver_tuple_compares_numeric_components(
    version: str,
    expected: tuple[int, int, int],
) -> None:
    assert semver_tuple(version) == expected


@pytest.mark.parametrize("version", ["v1.0.0", "1.0", "01.0.0", "1.0.0-rc1"])
def test_semver_tuple_rejects_unsupported_forms(version: str) -> None:
    with pytest.raises(ValueError):
        semver_tuple(version)


def test_frozen_model_rejects_assignment_and_extra_fields() -> None:
    payload = Payload(name="example")

    with pytest.raises(ValidationError, match="frozen"):
        payload.name = "changed"
    with pytest.raises(ValidationError, match="extra"):
        Payload.model_validate({"name": "example", "unknown": True})
