"""OpenAI Responses adapter that preserves bounded M5 experiment evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.domain.common import (
    FrozenJsonObject,
    canonical_json_bytes,
)
from experiments.gateway import (
    GatewayFailure,
    GatewayFailureKind,
    GatewayOutcome,
    GatewayRequest,
    GatewaySuccess,
)

_MAX_RAW_RESPONSE_BYTES = 1024 * 1024
_MAX_ERROR_RESPONSE_BYTES = 64 * 1024
_INVOCATION_KEYS = {"instructions", "task_input", "output_schema"}


class _ResponsesResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _OpenAIClient(Protocol):
    @property
    def responses(self) -> _ResponsesResource: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenAIExperimentGateway:
    """Execute one OpenAI request without hidden retries or evidence loss."""

    client: _OpenAIClient
    is_live: bool = field(default=True, init=False, repr=False)

    async def generate(self, request: GatewayRequest) -> GatewayOutcome:
        prepared = _prepare_request(request)
        if isinstance(prepared, GatewayFailure):
            return prepared
        try:
            response = await self.client.responses.create(**prepared)
        except Exception as exc:
            return _classify_exception(exc)
        return _normalize_response(response, request.expected_output_schema)

    async def close(self) -> None:
        """Release the SDK transport owned by this experiment gateway."""

        await self.client.close()


def create_openai_experiment_gateway(*, api_key: str) -> OpenAIExperimentGateway:
    """Build the optional SDK client with retries disabled at the SDK layer."""

    if not api_key.strip():
        raise ValueError("api_key must not be empty")
    try:
        module = importlib.import_module("openai")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenAI experiment support requires the 'llm' optional dependency"
        ) from exc
    client_type = getattr(module, "AsyncOpenAI", None)
    if not callable(client_type):
        raise RuntimeError("installed OpenAI SDK does not provide AsyncOpenAI")
    client = client_type(api_key=api_key, max_retries=0)
    return OpenAIExperimentGateway(client=cast(_OpenAIClient, client))


def _prepare_request(request: GatewayRequest) -> dict[str, object] | GatewayFailure:
    generation = request.generation
    if generation.provider != "openai":
        return _failure(GatewayFailureKind.CLIENT_ERROR, "PROVIDER_MISMATCH")
    if generation.seed is not None:
        return _failure(GatewayFailureKind.CLIENT_ERROR, "OPENAI_SEED_UNSUPPORTED")

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
    response_format: dict[str, object]
    if output_schema is None:
        response_format = {"type": "json_object"}
    else:
        response_format = {
            "type": "json_schema",
            "name": "agent_factory_writer_output",
            "schema": expected_schema,
            "strict": True,
        }
    return {
        "model": generation.model,
        "instructions": instructions,
        "input": [{"role": "user", "content": task_input}],
        "temperature": generation.temperature,
        "max_output_tokens": generation.max_output_tokens,
        "store": False,
        "timeout": generation.request_timeout_seconds,
        "text": {"format": response_format},
    }


def _normalize_response(
    response: object,
    expected_output_schema: Mapping[str, object],
) -> GatewayOutcome:
    raw = _dump_response(response)
    if raw is None:
        return _failure(GatewayFailureKind.INVALID_RESPONSE, "OPENAI_RESPONSE_INVALID")
    raw_size = len(canonical_json_bytes(raw))
    if raw_size > _MAX_RAW_RESPONSE_BYTES:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_RESPONSE_TOO_LARGE",
        )
    request_id = _string_value(response, "id")
    if request_id is None:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_REQUEST_ID_MISSING",
            raw_response=raw,
        )
    if len(request_id) > 256:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_REQUEST_ID_INVALID",
            raw_response=raw,
        )
    if _is_content_filtered(raw):
        return _failure(
            GatewayFailureKind.FILTERED,
            "OPENAI_CONTENT_FILTERED",
            provider_request_id=request_id,
            raw_response=raw,
        )
    status = _string_value(response, "status")
    if status != "completed":
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_RESPONSE_INCOMPLETE",
            provider_request_id=request_id,
            raw_response=raw,
        )
    output_text = _string_value(response, "output_text")
    if output_text is None or not output_text:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_MISSING",
            provider_request_id=request_id,
            raw_response=raw,
        )
    try:
        parsed = json.loads(output_text)
        if not isinstance(parsed, Mapping):
            raise ValueError("structured output must be an object")
        structured = FrozenJsonObject(parsed)
    except (TypeError, ValueError):
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_NOT_JSON_OBJECT",
            provider_request_id=request_id,
            raw_response=raw,
        )
    if not Draft202012Validator(expected_output_schema).is_valid(
        structured.to_builtin()
    ):
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_SCHEMA_INVALID",
            provider_request_id=request_id,
            raw_response=raw,
        )
    usage = _usage(response)
    if usage is None:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_USAGE_INVALID",
            provider_request_id=request_id,
            raw_response=raw,
        )
    prompt_tokens, completion_tokens = usage
    try:
        return GatewaySuccess(
            provider_request_id=request_id,
            raw_response=raw,
            output_text=output_text,
            structured_output=structured,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except ValueError:
        return _failure(
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_SUCCESS_CONTRACT_INVALID",
            provider_request_id=request_id,
            raw_response=raw,
        )


def _classify_exception(exc: Exception) -> GatewayFailure:
    name = type(exc).__name__
    request_id = _exception_request_id(exc)
    raw = _exception_body(exc)
    if isinstance(exc, TimeoutError) or name in {"APITimeoutError", "TimeoutException"}:
        return _failure(
            GatewayFailureKind.TIMED_OUT,
            "OPENAI_REQUEST_TIMED_OUT",
            provider_request_id=request_id,
            raw_response=raw,
        )
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        if status == 429:
            kind = GatewayFailureKind.RATE_LIMITED
            code = "OPENAI_RATE_LIMITED"
        elif status >= 500:
            kind = GatewayFailureKind.SERVER_ERROR
            code = "OPENAI_SERVER_ERROR"
        elif status >= 400:
            kind = GatewayFailureKind.CLIENT_ERROR
            code = "OPENAI_CLIENT_ERROR"
        else:
            kind = GatewayFailureKind.NETWORK
            code = "OPENAI_SDK_ERROR"
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
            "OPENAI_NETWORK_ERROR",
            provider_request_id=request_id,
            raw_response=raw,
        )
    return _failure(
        GatewayFailureKind.CLIENT_ERROR,
        "OPENAI_SDK_ERROR",
        provider_request_id=request_id,
        raw_response=raw,
    )


def _failure(
    kind: GatewayFailureKind,
    code: str,
    *,
    provider_request_id: str | None = None,
    raw_response: Mapping[str, object] | None = None,
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
    )


def _dump_response(response: object) -> FrozenJsonObject | None:
    if isinstance(response, Mapping):
        value: object = response
    else:
        model_dump = getattr(response, "model_dump", None)
        if not callable(model_dump):
            return None
        try:
            value = model_dump(mode="json")
        except Exception:
            return None
    if not isinstance(value, Mapping):
        return None
    try:
        return FrozenJsonObject(value)
    except (TypeError, ValueError):
        return None


def _usage(response: object) -> tuple[int, int] | None:
    usage = _value(response, "usage")
    if usage is None:
        return None
    prompt_tokens = _value(usage, "input_tokens")
    completion_tokens = _value(usage, "output_tokens")
    if not _is_nonnegative_int(prompt_tokens) or not _is_nonnegative_int(
        completion_tokens
    ):
        return None
    return cast(int, prompt_tokens), cast(int, completion_tokens)


def _is_content_filtered(raw: Mapping[str, object]) -> bool:
    def contains_filter(value: object) -> bool:
        if isinstance(value, Mapping):
            if value.get("type") == "refusal":
                return True
            if value.get("reason") == "content_filter":
                return True
            return any(contains_filter(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_filter(item) for item in value)
        return False

    return contains_filter(raw)


def _exception_request_id(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        candidate = headers.get("x-request-id")
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


def _string_value(item: object, name: str) -> str | None:
    value = _value(item, name)
    return value if isinstance(value, str) and value else None


def _value(item: object, name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
