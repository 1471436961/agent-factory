"""Pure domain validation and error contract tests."""

from typing import cast

import pytest

from agent_factory.domain.common import FrozenJsonObject
from agent_factory.domain.errors import InvalidOutputSchemaError
from agent_factory.domain.validation import validate_output_schema


def test_validate_output_schema_accepts_draft_2020_12_schema() -> None:
    validate_output_schema(
        FrozenJsonObject(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            }
        )
    )


def test_validate_output_schema_returns_stable_structured_error() -> None:
    with pytest.raises(InvalidOutputSchemaError) as exc_info:
        validate_output_schema(
            FrozenJsonObject(
                {
                    "type": "not-a-json-schema-type",
                }
            )
        )

    error = exc_info.value
    assert error.code == "INVALID_OUTPUT_SCHEMA"
    assert "reason" in error.details
    assert not hasattr(error, "status_code")
    with pytest.raises(TypeError):
        cast(dict[str, object], error.details)["reason"] = "changed"
