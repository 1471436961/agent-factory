"""Moonshot Chat Completions adapter for bounded M5 experiment evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.domain.common import (
    FrozenJsonObject,
    JsonBuiltin,
    canonical_json_bytes,
)
from experiments.gateway import (
    GatewayFailure,
    GatewayFailureKind,
    GatewayOutcome,
    GatewayRequest,
    GatewaySuccess,
)

MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
MOONSHOT_MODEL = "kimi-k2.6"
MOONSHOT_PROVIDER_OPTIONS = FrozenJsonObject(
    {
        "n": 1,
        "stream": True,
        "stream_options": {"include_usage": True},
        "thinking": {"type": "disabled"},
        "top_p": 0.95,
    }
)

_MAX_RAW_RESPONSE_BYTES = 1024 * 1024
_MAX_ERROR_RESPONSE_BYTES = 64 * 1024
_INVOCATION_KEYS = {"instructions", "task_input", "output_schema"}
_MFJS_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}
_MFJS_LOCAL_ONLY_CONSTRAINTS = {
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "pattern",
    "uniqueItems",
}
_MFJS_IGNORED_ANNOTATIONS = {
    "$comment",
    "$schema",
    "deprecated",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}


class _ChatCompletionsResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _ChatResource(Protocol):
    @property
    def completions(self) -> _ChatCompletionsResource: ...


class _MoonshotClient(Protocol):
    @property
    def chat(self) -> _ChatResource: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MoonshotExperimentGateway:
    """Execute one Kimi request with an explicit, frozen inference profile."""

    client: _MoonshotClient
    is_live: bool = field(default=True, init=False, repr=False)

    async def generate(self, request: GatewayRequest) -> GatewayOutcome:
        prepared = _prepare_request(request)
        if isinstance(prepared, GatewayFailure):
            return prepared
        try:
            stream = await self.client.chat.completions.create(**prepared)
            return await _normalize_stream(stream, request.expected_output_schema)
        except Exception as exc:
            return _classify_exception(exc)

    async def close(self) -> None:
        """Release the SDK transport owned by this experiment gateway."""

        await self.client.close()


def create_moonshot_experiment_gateway(*, api_key: str) -> MoonshotExperimentGateway:
    """Build the OpenAI-compatible Kimi client with SDK retries disabled."""

    if not api_key.strip():
        raise ValueError("api_key must not be empty")
    try:
        module = importlib.import_module("openai")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Moonshot experiment support requires the 'llm' optional dependency"
        ) from exc
    client_type = getattr(module, "AsyncOpenAI", None)
    if not callable(client_type):
        raise RuntimeError("installed OpenAI SDK does not provide AsyncOpenAI")
    client = client_type(
        api_key=api_key,
        base_url=MOONSHOT_BASE_URL,
        max_retries=0,
    )
    return MoonshotExperimentGateway(client=cast(_MoonshotClient, client))


def _to_moonshot_mfjs(schema: Mapping[str, object]) -> dict[str, object]:
    """Map a validated JSON Schema to Moonshot's supported provider subset."""

    return _normalize_mfjs_node(schema, at_root=True)


def _normalize_mfjs_node(
    schema: Mapping[str, object],
    *,
    at_root: bool,
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    local_constraints: list[tuple[str, object]] = []
    for key, value in schema.items():
        if key in _MFJS_IGNORED_ANNOTATIONS:
            continue
        if key in _MFJS_LOCAL_ONLY_CONSTRAINTS:
            local_constraints.append((key, value))
            continue
        if key == "type":
            if not isinstance(value, str) or value not in _MFJS_TYPES:
                raise ValueError("MFJS type is unsupported")
            normalized[key] = value
            continue
        if key == "description":
            if not isinstance(value, str) or not value:
                raise ValueError("MFJS description is invalid")
            normalized[key] = value
            continue
        if key == "default":
            normalized[key] = value
            continue
        if key == "enum":
            if not isinstance(value, list) or not value:
                raise ValueError("MFJS enum is invalid")
            enum_types = {type(item) for item in value if not isinstance(item, bool)}
            if (
                len(enum_types) != 1
                or not enum_types <= {str, int, float}
                or any(isinstance(item, bool) for item in value)
            ):
                raise ValueError("MFJS enum values are unsupported")
            normalized[key] = value
            continue
        if key == "required":
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item for item in value)
                or len(set(value)) != len(value)
            ):
                raise ValueError("MFJS required is invalid")
            normalized[key] = value
            continue
        if key == "properties":
            if not isinstance(value, Mapping):
                raise ValueError("MFJS properties is invalid")
            properties: dict[str, object] = {}
            for property_name, property_schema in value.items():
                if (
                    not isinstance(property_name, str)
                    or not property_name
                    or not isinstance(property_schema, Mapping)
                ):
                    raise ValueError("MFJS property schema is invalid")
                properties[property_name] = _normalize_mfjs_node(
                    property_schema,
                    at_root=False,
                )
            normalized[key] = properties
            continue
        if key == "additionalProperties":
            if isinstance(value, bool):
                normalized[key] = value
            elif isinstance(value, Mapping):
                normalized[key] = _normalize_mfjs_node(value, at_root=False)
            else:
                raise ValueError("MFJS additionalProperties is invalid")
            continue
        if key == "items":
            if not isinstance(value, Mapping):
                raise ValueError("MFJS items is invalid")
            normalized[key] = _normalize_mfjs_node(value, at_root=False)
            continue
        if key == "anyOf":
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, Mapping) for item in value)
            ):
                raise ValueError("MFJS anyOf is invalid")
            normalized[key] = [
                _normalize_mfjs_node(cast(Mapping[str, object], item), at_root=False)
                for item in value
            ]
            continue
        if key == "$defs":
            if not at_root or not isinstance(value, Mapping):
                raise ValueError("MFJS $defs is invalid")
            definitions: dict[str, object] = {}
            for definition_name, definition_schema in value.items():
                if (
                    not isinstance(definition_name, str)
                    or not definition_name
                    or not isinstance(definition_schema, Mapping)
                ):
                    raise ValueError("MFJS definition is invalid")
                definitions[definition_name] = _normalize_mfjs_node(
                    definition_schema,
                    at_root=False,
                )
            normalized[key] = definitions
            continue
        if key == "$ref":
            if not isinstance(value, str) or not (
                value == "#" or value.startswith("#/$defs/")
            ):
                raise ValueError("MFJS $ref is unsupported")
            normalized[key] = value
            continue
        raise ValueError(f"unsupported MFJS keyword: {key}")

    if local_constraints:
        encoded = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}"
            for key, value in sorted(local_constraints)
        )
        hint = f"Agent Factory post-validates these constraints: {encoded}."
        description = normalized.get("description")
        normalized["description"] = (
            hint if description is None else f"{description} {hint}"
        )
    return normalized


def _prepare_request(request: GatewayRequest) -> dict[str, object] | GatewayFailure:
    generation = request.generation
    if generation.provider != "moonshot":
        return _failure(GatewayFailureKind.CLIENT_ERROR, "PROVIDER_MISMATCH")
    if generation.seed is not None:
        return _failure(GatewayFailureKind.CLIENT_ERROR, "MOONSHOT_SEED_UNSUPPORTED")
    if generation.temperature != 0.6:
        return _failure(
            GatewayFailureKind.CLIENT_ERROR,
            "MOONSHOT_TEMPERATURE_UNSUPPORTED",
        )
    if FrozenJsonObject(generation.provider_options) != MOONSHOT_PROVIDER_OPTIONS:
        return _failure(
            GatewayFailureKind.CLIENT_ERROR,
            "MOONSHOT_INFERENCE_PROFILE_UNSUPPORTED",
        )

    invocation = FrozenJsonObject(request.invocation).to_builtin()
    if set(invocation) != _INVOCATION_KEYS:
        return _failure(GatewayFailureKind.CLIENT_ERROR, "INVOCATION_INVALID")
    instructions = invocation["instructions"]
    task_input = invocation["task_input"]
    output_schema = invocation["output_schema"]
    if (
        not isinstance(instructions, str)
        or not instructions
        or not isinstance(task_input, str)
        or not task_input
        or (output_schema is not None and not isinstance(output_schema, dict))
    ):
        return _failure(GatewayFailureKind.CLIENT_ERROR, "INVOCATION_INVALID")
    if (
        hashlib.sha256(canonical_json_bytes(invocation)).hexdigest()
        != request.prompt_hash
    ):
        return _failure(GatewayFailureKind.CLIENT_ERROR, "PROMPT_HASH_MISMATCH")

    expected_schema = FrozenJsonObject(request.expected_output_schema).to_builtin()
    if output_schema is not None and canonical_json_bytes(
        output_schema
    ) != canonical_json_bytes(expected_schema):
        return _failure(GatewayFailureKind.CLIENT_ERROR, "OUTPUT_SCHEMA_MISMATCH")
    if output_schema is None:
        response_format: dict[str, object] = {"type": "json_object"}
    else:
        try:
            provider_schema = _to_moonshot_mfjs(expected_schema)
        except ValueError:
            return _failure(
                GatewayFailureKind.CLIENT_ERROR,
                "MOONSHOT_OUTPUT_SCHEMA_UNSUPPORTED",
            )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "agent_factory_writer_output",
                "schema": provider_schema,
                "strict": True,
            },
        }
    options = MOONSHOT_PROVIDER_OPTIONS.to_builtin()
    return {
        "model": generation.model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": task_input},
        ],
        "temperature": generation.temperature,
        "top_p": options["top_p"],
        "n": options["n"],
        "max_completion_tokens": generation.max_output_tokens,
        "response_format": response_format,
        "stream": options["stream"],
        "stream_options": options["stream_options"],
        "extra_body": {"thinking": options["thinking"]},
        "timeout": generation.request_timeout_seconds,
    }


async def _normalize_stream(
    stream: object,
    expected_output_schema: Mapping[str, object],
) -> GatewayOutcome:
    iterator = _as_async_iterator(stream)
    if iterator is None:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_STREAM_INVALID",
        )
    chunks: list[dict[str, JsonBuiltin]] = []
    output_parts: list[str] = []
    request_id: str | None = None
    finish_reason: str | None = None
    usage: tuple[int, int] | None = None

    async for chunk in iterator:
        raw = _dump_mapping(chunk)
        if raw is None:
            return _failure(
                GatewayFailureKind.INVALID_RESPONSE,
                "MOONSHOT_CHUNK_INVALID",
            )
        builtin = raw.to_builtin()
        chunks.append(builtin)
        raw_response = {"chunks": chunks}
        if len(canonical_json_bytes(raw_response)) > _MAX_RAW_RESPONSE_BYTES:
            return _failure(
                GatewayFailureKind.INVALID_RESPONSE,
                "MOONSHOT_RESPONSE_TOO_LARGE",
                usage=usage,
            )

        chunk_id = builtin.get("id")
        if chunk_id is not None:
            if not isinstance(chunk_id, str) or not chunk_id:
                return _invalid_response("MOONSHOT_REQUEST_ID_INVALID", raw_response)
            if request_id is not None and request_id != chunk_id:
                return _invalid_response("MOONSHOT_REQUEST_ID_CHANGED", raw_response)
            request_id = chunk_id

        parsed_usage = _usage(builtin.get("usage"))
        if parsed_usage is not None:
            if usage is not None and usage != parsed_usage:
                return _invalid_response("MOONSHOT_USAGE_CHANGED", raw_response)
            usage = parsed_usage

        choices = builtin.get("choices")
        if not isinstance(choices, list):
            return _invalid_response("MOONSHOT_CHOICES_INVALID", raw_response)
        if not choices:
            continue
        if len(choices) != 1 or not isinstance(choices[0], dict):
            return _invalid_response("MOONSHOT_CHOICES_INVALID", raw_response)
        choice = choices[0]
        choice_usage = _usage(choice.get("usage"))
        if choice_usage is not None:
            if usage is not None and usage != choice_usage:
                return _invalid_response("MOONSHOT_USAGE_CHANGED", raw_response)
            usage = choice_usage
        if choice.get("index") != 0:
            return _invalid_response("MOONSHOT_CHOICE_INDEX_INVALID", raw_response)
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return _invalid_response("MOONSHOT_DELTA_INVALID", raw_response)
        if delta.get("reasoning_content") not in (None, ""):
            return _invalid_response(
                "MOONSHOT_THINKING_PROFILE_VIOLATION",
                raw_response,
            )
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                return _invalid_response("MOONSHOT_CONTENT_INVALID", raw_response)
            output_parts.append(content)
        candidate_reason = choice.get("finish_reason")
        if candidate_reason is not None:
            if not isinstance(candidate_reason, str):
                return _invalid_response("MOONSHOT_FINISH_REASON_INVALID", raw_response)
            if finish_reason is not None and finish_reason != candidate_reason:
                return _invalid_response("MOONSHOT_FINISH_REASON_CHANGED", raw_response)
            finish_reason = candidate_reason

    raw_response = {"chunks": chunks}
    if request_id is None:
        return _invalid_response("MOONSHOT_REQUEST_ID_MISSING", raw_response)
    if len(request_id) > 256:
        return _invalid_response("MOONSHOT_REQUEST_ID_INVALID", raw_response)
    if finish_reason == "content_filter":
        return _failure(
            GatewayFailureKind.FILTERED,
            "MOONSHOT_CONTENT_FILTERED",
            provider_request_id=request_id,
            raw_response=raw_response,
            usage=usage,
        )
    if finish_reason != "stop":
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_RESPONSE_INCOMPLETE",
            provider_request_id=request_id,
            raw_response=raw_response,
            usage=usage,
        )
    output_text = "".join(output_parts)
    if not output_text:
        return _invalid_response(
            "MOONSHOT_OUTPUT_MISSING",
            raw_response,
            provider_request_id=request_id,
        )
    try:
        parsed = json.loads(output_text)
        if not isinstance(parsed, Mapping):
            raise ValueError("structured output must be an object")
        structured = FrozenJsonObject(parsed)
    except (TypeError, ValueError):
        return _invalid_response(
            "MOONSHOT_OUTPUT_NOT_JSON_OBJECT",
            raw_response,
            provider_request_id=request_id,
        )
    if not Draft202012Validator(expected_output_schema).is_valid(
        structured.to_builtin()
    ):
        return _invalid_response(
            "MOONSHOT_OUTPUT_SCHEMA_INVALID",
            raw_response,
            provider_request_id=request_id,
        )
    if usage is None:
        return _invalid_response(
            "MOONSHOT_USAGE_INVALID",
            raw_response,
            provider_request_id=request_id,
        )
    try:
        return GatewaySuccess(
            provider_request_id=request_id,
            raw_response=raw_response,
            output_text=output_text,
            structured_output=structured,
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
        )
    except ValueError:
        return _invalid_response(
            "MOONSHOT_SUCCESS_CONTRACT_INVALID",
            raw_response,
            provider_request_id=request_id,
        )


def _as_async_iterator(value: object) -> AsyncIterator[object] | None:
    method = getattr(value, "__aiter__", None)
    if not callable(method):
        return None
    iterator = method()
    return cast(AsyncIterator[object], iterator)


def _usage(value: object) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    prompt_tokens = value.get("prompt_tokens")
    completion_tokens = value.get("completion_tokens")
    if not _is_nonnegative_int(prompt_tokens) or not _is_nonnegative_int(
        completion_tokens
    ):
        return None
    return cast(int, prompt_tokens), cast(int, completion_tokens)


def _stable_usage_from_raw_response(
    raw_response: Mapping[str, object],
) -> tuple[int, int] | None:
    chunks = raw_response.get("chunks")
    if not isinstance(chunks, list):
        return None
    observed: list[tuple[int, int]] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        for candidate in (chunk.get("usage"),):
            if candidate is None:
                continue
            parsed = _usage(candidate)
            if parsed is None:
                return None
            observed.append(parsed)
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            candidate = choice.get("usage")
            if candidate is None:
                continue
            parsed = _usage(candidate)
            if parsed is None:
                return None
            observed.append(parsed)
    if not observed or any(candidate != observed[0] for candidate in observed[1:]):
        return None
    return observed[0]


def _classify_exception(exc: Exception) -> GatewayFailure:
    name = type(exc).__name__
    request_id = _exception_request_id(exc)
    raw = _exception_body(exc)
    if isinstance(exc, TimeoutError) or name in {"APITimeoutError", "TimeoutException"}:
        return _failure(
            GatewayFailureKind.TIMED_OUT,
            "MOONSHOT_REQUEST_TIMED_OUT",
            provider_request_id=request_id,
            raw_response=raw,
        )
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        if status == 429:
            kind = GatewayFailureKind.RATE_LIMITED
            code = "MOONSHOT_RATE_LIMITED"
        elif status >= 500:
            kind = GatewayFailureKind.SERVER_ERROR
            code = "MOONSHOT_SERVER_ERROR"
        elif status >= 400:
            kind = GatewayFailureKind.CLIENT_ERROR
            code = "MOONSHOT_CLIENT_ERROR"
        else:
            kind = GatewayFailureKind.NETWORK
            code = "MOONSHOT_SDK_ERROR"
        return _failure(
            kind,
            code,
            provider_request_id=request_id,
            raw_response=raw,
        )
    if isinstance(exc, OSError) or name in {
        "APIConnectionError",
        "ConnectError",
        "NetworkError",
    }:
        return _failure(
            GatewayFailureKind.NETWORK,
            "MOONSHOT_NETWORK_ERROR",
            provider_request_id=request_id,
            raw_response=raw,
        )
    return _failure(
        GatewayFailureKind.CLIENT_ERROR,
        "MOONSHOT_SDK_ERROR",
        provider_request_id=request_id,
        raw_response=raw,
    )


def _invalid_response(
    code: str,
    raw_response: Mapping[str, object],
    *,
    provider_request_id: str | None = None,
) -> GatewayFailure:
    return _failure(
        GatewayFailureKind.INVALID_RESPONSE,
        code,
        provider_request_id=provider_request_id,
        raw_response=raw_response,
        usage=_stable_usage_from_raw_response(raw_response),
    )


def _failure(
    kind: GatewayFailureKind,
    code: str,
    *,
    provider_request_id: str | None = None,
    raw_response: Mapping[str, object] | None = None,
    usage: tuple[int, int] | None = None,
) -> GatewayFailure:
    if provider_request_id is not None and not 1 <= len(provider_request_id) <= 256:
        provider_request_id = None
    bounded_raw: Mapping[str, object] | None = raw_response
    if raw_response is not None and len(canonical_json_bytes(raw_response)) > (
        _MAX_ERROR_RESPONSE_BYTES
    ):
        bounded_raw = None
    return GatewayFailure(
        kind=kind,
        error_code=code,
        provider_request_id=provider_request_id,
        raw_response=bounded_raw,
        prompt_tokens=None if usage is None else usage[0],
        completion_tokens=None if usage is None else usage[1],
    )


def _dump_mapping(value: object) -> FrozenJsonObject | None:
    if isinstance(value, Mapping):
        candidate: object = value
    else:
        model_dump = getattr(value, "model_dump", None)
        if not callable(model_dump):
            return None
        try:
            candidate = model_dump(mode="json")
        except Exception:
            return None
    if not isinstance(candidate, Mapping):
        return None
    try:
        return FrozenJsonObject(candidate)
    except (TypeError, ValueError):
        return None


def _exception_request_id(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        for name in ("x-request-id", "x-msh-request-id"):
            candidate = headers.get(name)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _exception_body(exc: Exception) -> FrozenJsonObject | None:
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return None
    try:
        return FrozenJsonObject(body)
    except (TypeError, ValueError):
        return None


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
