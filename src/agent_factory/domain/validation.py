"""Pure validation functions shared by M1 application services."""

from collections.abc import Mapping

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from agent_factory.domain.common import FrozenJsonObject
from agent_factory.domain.errors import InvalidOutputSchemaError


def validate_output_schema(schema: Mapping[str, object]) -> None:
    """Require a structurally valid JSON Schema Draft 2020-12 document."""

    try:
        Draft202012Validator.check_schema(FrozenJsonObject(schema).to_builtin())
    except SchemaError as exc:
        raise InvalidOutputSchemaError(
            details={
                "path": list(exc.path),
                "reason": exc.message,
            }
        ) from exc
