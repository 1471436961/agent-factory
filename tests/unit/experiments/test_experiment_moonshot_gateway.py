"""Moonshot experiment mapping tests with no network or live credentials."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Protocol, cast
from uuid import UUID

import pytest

from agent_factory.domain.common import FrozenJsonObject, canonical_json_bytes
from experiments.contracts import GenerationConfig
from experiments.gateway import (
    GatewayFailure,
    GatewayFailureKind,
    GatewayRequest,
    GatewaySuccess,
)
from experiments.moonshot_gateway import (
    MOONSHOT_BASE_URL,
    MOONSHOT_PROVIDER_OPTIONS,
    MoonshotExperimentGateway,
    create_moonshot_experiment_gateway,
)

RUN_ID = UUID("70000000-0000-0000-0000-000000000002")
OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 6,
        },
        "next_action": {"type": "string", "minLength": 1},
    },
    "required": ["title", "summary", "key_points", "next_action"],
    "additionalProperties": False,
}
MFJS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "Agent Factory post-validates these constraints: minLength=1."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "Agent Factory post-validates these constraints: minLength=1."
            ),
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Agent Factory post-validates these constraints: "
                "maxItems=6, minItems=2."
            ),
        },
        "next_action": {
            "type": "string",
            "description": (
                "Agent Factory post-validates these constraints: minLength=1."
            ),
        },
    },
    "required": ["title", "summary", "key_points", "next_action"],
    "additionalProperties": False,
}
VALID_OUTPUT = {
    "title": "Current routing contract",
    "summary": "Use the current endpoint.",
    "key_points": ["current", "bounded"],
    "next_action": "Review staging output.",
}


class _FakeStream:
    def __init__(
        self,
        chunks: Sequence[object],
        *,
        failure_after: int | None = None,
    ) -> None:
        self._chunks = tuple(chunks)
        self._index = 0
        self._failure_after = failure_after

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> object:
        if self._failure_after is not None and self._index == self._failure_after:
            raise OSError("sensitive stream failure")
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class _FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(outcomes))
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _ProviderError(Exception):
    def __init__(
        self,
        status_code: int,
        *,
        request_id: str = "req-error-1",
        body: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__("sensitive provider exception text")
        self.status_code = status_code
        self.request_id = request_id
        self.body = body or {"error": {"type": "provider_error"}}


class _AsyncClosable(Protocol):
    async def close(self) -> None: ...


def _json_text(value: Mapping[str, object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _chunks(
    *,
    output_text: str | None = None,
    request_id: str = "cmpl-pilot-1",
    finish_reason: str = "stop",
    usage: object = None,
    usage_in_choice: bool = False,
    reasoning_content: str | None = None,
) -> list[dict[str, object]]:
    text = _json_text(VALID_OUTPUT) if output_text is None else output_text
    midpoint = len(text) // 2
    final_usage = (
        {"prompt_tokens": 125, "completion_tokens": 48, "total_tokens": 173}
        if usage is None
        else usage
    )
    first_delta: dict[str, object] = {"content": text[:midpoint]}
    if reasoning_content is not None:
        first_delta["reasoning_content"] = reasoning_content
    final_choice: dict[str, object] = {
        "index": 0,
        "delta": {"content": text[midpoint:]},
        "finish_reason": finish_reason,
    }
    if usage_in_choice:
        final_choice["usage"] = final_usage
    return [
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": first_delta, "finish_reason": None}],
            "usage": None,
        },
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [final_choice],
            "usage": None if usage_in_choice else final_usage,
        },
    ]


def _request(*, factory: bool) -> GatewayRequest:
    invocation = {
        "instructions": "Use only current supplied knowledge.",
        "task_input": "Write the current routing reference.",
        "output_schema": OUTPUT_SCHEMA if factory else None,
    }
    return GatewayRequest(
        run_id=RUN_ID,
        attempt_number=1,
        generation=GenerationConfig(
            provider="moonshot",
            model="kimi-k2.6",
            sdk_version="2.46.0",
            temperature=0.6,
            max_output_tokens=1024,
            seed=None,
            request_timeout_seconds=60,
            max_attempts=2,
            concurrency=1,
            provider_options=MOONSHOT_PROVIDER_OPTIONS,
        ),
        invocation=invocation,
        expected_output_schema=OUTPUT_SCHEMA,
        prompt_hash=hashlib.sha256(canonical_json_bytes(invocation)).hexdigest(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [False, True])
async def test_gateway_maps_frozen_profile_and_normalizes_stream(
    factory: bool,
) -> None:
    client = _FakeClient([_FakeStream(_chunks())])
    gateway = MoonshotExperimentGateway(client=client)

    outcome = await gateway.generate(_request(factory=factory))

    assert gateway.is_live is True
    assert isinstance(outcome, GatewaySuccess)
    assert outcome.provider_request_id == "cmpl-pilot-1"
    assert FrozenJsonObject(outcome.structured_output).to_builtin() == VALID_OUTPUT
    assert outcome.prompt_tokens == 125
    assert outcome.completion_tokens == 48
    assert len(cast(tuple[object, ...], outcome.raw_response["chunks"])) == 2
    call = client.chat.completions.calls[0]
    assert call["model"] == "kimi-k2.6"
    assert call["temperature"] == 0.6
    assert call["top_p"] == 0.95
    assert call["n"] == 1
    assert call["max_completion_tokens"] == 1024
    assert call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert call["timeout"] == 60
    assert call["messages"] == [
        {"role": "system", "content": "Use only current supplied knowledge."},
        {"role": "user", "content": "Write the current routing reference."},
    ]
    if factory:
        assert call["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "agent_factory_writer_output",
                "schema": MFJS_OUTPUT_SCHEMA,
                "strict": True,
            },
        }
    else:
        assert call["response_format"] == {"type": "json_object"}
    await gateway.close()
    assert client.closed == 1


@pytest.mark.asyncio
async def test_gateway_accepts_kimi_choice_level_stream_usage() -> None:
    client = _FakeClient([_FakeStream(_chunks(usage_in_choice=True))])

    outcome = await MoonshotExperimentGateway(client=client).generate(
        _request(factory=True)
    )

    assert isinstance(outcome, GatewaySuccess)
    assert outcome.prompt_tokens == 125
    assert outcome.completion_tokens == 48


@pytest.mark.asyncio
async def test_gateway_rejects_unmapped_schema_keyword_before_sdk_call() -> None:
    client = _FakeClient([])
    schema = {
        "type": "object",
        "oneOf": [
            {"properties": {"title": {"type": "string"}}},
            {"properties": {"summary": {"type": "string"}}},
        ],
    }
    invocation = {
        "instructions": "Use only current supplied knowledge.",
        "task_input": "Write the current routing reference.",
        "output_schema": schema,
    }
    request = _request(factory=True).model_copy(
        update={
            "invocation": invocation,
            "expected_output_schema": schema,
            "prompt_hash": hashlib.sha256(canonical_json_bytes(invocation)).hexdigest(),
        }
    )

    outcome = await MoonshotExperimentGateway(client=client).generate(request)

    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "MOONSHOT_OUTPUT_SCHEMA_UNSUPPORTED",
    )
    assert client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_gateway_rejects_request_drift_before_sdk_call() -> None:
    client = _FakeClient([])
    gateway = MoonshotExperimentGateway(client=client)
    valid = _request(factory=True)

    variants = (
        (
            valid.generation.model_copy(update={"provider": "openai"}),
            "PROVIDER_MISMATCH",
        ),
        (
            valid.generation.model_copy(update={"seed": 7}),
            "MOONSHOT_SEED_UNSUPPORTED",
        ),
        (
            valid.generation.model_copy(update={"temperature": 0}),
            "MOONSHOT_TEMPERATURE_UNSUPPORTED",
        ),
        (
            valid.generation.model_copy(update={"provider_options": {}}),
            "MOONSHOT_INFERENCE_PROFILE_UNSUPPORTED",
        ),
    )
    for generation, code in variants:
        outcome = await gateway.generate(
            valid.model_copy(update={"generation": generation})
        )
        assert _failure_identity(outcome) == (GatewayFailureKind.CLIENT_ERROR, code)

    outcome = await gateway.generate(valid.model_copy(update={"prompt_hash": "f" * 64}))
    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "PROMPT_HASH_MISMATCH",
    )
    mismatched = valid.model_copy(update={"expected_output_schema": {"type": "object"}})
    outcome = await gateway.generate(mismatched)
    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "OUTPUT_SCHEMA_MISMATCH",
    )
    assert client.chat.completions.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream", "kind", "code"),
    [
        (object(), GatewayFailureKind.INVALID_RESPONSE, "MOONSHOT_STREAM_INVALID"),
        (
            _FakeStream(_chunks(request_id="")),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_REQUEST_ID_INVALID",
        ),
        (
            _FakeStream(_chunks(finish_reason="length")),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_RESPONSE_INCOMPLETE",
        ),
        (
            _FakeStream(_chunks(finish_reason="content_filter")),
            GatewayFailureKind.FILTERED,
            "MOONSHOT_CONTENT_FILTERED",
        ),
        (
            _FakeStream(_chunks(output_text="[]")),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_OUTPUT_NOT_JSON_OBJECT",
        ),
        (
            _FakeStream(_chunks(output_text='{"title":"incomplete"}')),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_OUTPUT_SCHEMA_INVALID",
        ),
        (
            _FakeStream(_chunks(usage={})),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_USAGE_INVALID",
        ),
        (
            _FakeStream(_chunks(reasoning_content="unexpected thinking")),
            GatewayFailureKind.INVALID_RESPONSE,
            "MOONSHOT_THINKING_PROFILE_VIOLATION",
        ),
    ],
)
async def test_gateway_rejects_invalid_or_incomplete_stream(
    stream: object,
    kind: GatewayFailureKind,
    code: str,
) -> None:
    outcome = await MoonshotExperimentGateway(client=_FakeClient([stream])).generate(
        _request(factory=True)
    )

    assert _failure_identity(outcome) == (kind, code)


@pytest.mark.asyncio
async def test_invalid_output_preserves_provider_usage_for_cost_accounting() -> None:
    outcome = await MoonshotExperimentGateway(
        client=_FakeClient([_FakeStream(_chunks(output_text="- [ ]"))])
    ).generate(_request(factory=True))

    assert _failure_identity(outcome) == (
        GatewayFailureKind.INVALID_RESPONSE,
        "MOONSHOT_OUTPUT_NOT_JSON_OBJECT",
    )
    assert isinstance(outcome, GatewayFailure)
    assert outcome.prompt_tokens == 125
    assert outcome.completion_tokens == 48


def test_gateway_failure_rejects_partial_usage() -> None:
    with pytest.raises(ValueError, match="both token counts"):
        GatewayFailure(
            kind=GatewayFailureKind.INVALID_RESPONSE,
            error_code="INVALID_RESPONSE",
            prompt_tokens=125,
        )


@pytest.mark.asyncio
async def test_gateway_rejects_request_id_change_and_oversized_evidence() -> None:
    changed = _chunks()
    changed[1]["id"] = "different-id"
    changed_outcome = await MoonshotExperimentGateway(
        client=_FakeClient([_FakeStream(changed)])
    ).generate(_request(factory=False))
    assert _failure_identity(changed_outcome) == (
        GatewayFailureKind.INVALID_RESPONSE,
        "MOONSHOT_REQUEST_ID_CHANGED",
    )

    oversized = _chunks(output_text="x" * (1024 * 1024))
    oversized_outcome = await MoonshotExperimentGateway(
        client=_FakeClient([_FakeStream(oversized)])
    ).generate(_request(factory=False))
    assert _failure_identity(oversized_outcome) == (
        GatewayFailureKind.INVALID_RESPONSE,
        "MOONSHOT_RESPONSE_TOO_LARGE",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "kind", "code"),
    [
        (
            TimeoutError("secret timeout detail"),
            GatewayFailureKind.TIMED_OUT,
            "MOONSHOT_REQUEST_TIMED_OUT",
        ),
        (
            _ProviderError(429),
            GatewayFailureKind.RATE_LIMITED,
            "MOONSHOT_RATE_LIMITED",
        ),
        (
            _ProviderError(503),
            GatewayFailureKind.SERVER_ERROR,
            "MOONSHOT_SERVER_ERROR",
        ),
        (
            _ProviderError(400),
            GatewayFailureKind.CLIENT_ERROR,
            "MOONSHOT_CLIENT_ERROR",
        ),
        (
            OSError("secret network detail"),
            GatewayFailureKind.NETWORK,
            "MOONSHOT_NETWORK_ERROR",
        ),
        (
            RuntimeError("secret SDK detail"),
            GatewayFailureKind.CLIENT_ERROR,
            "MOONSHOT_SDK_ERROR",
        ),
    ],
)
async def test_gateway_classifies_provider_failures_without_secret(
    exception: Exception,
    kind: GatewayFailureKind,
    code: str,
) -> None:
    outcome = await MoonshotExperimentGateway(client=_FakeClient([exception])).generate(
        _request(factory=False)
    )

    assert _failure_identity(outcome) == (kind, code)
    assert isinstance(outcome, GatewayFailure)
    assert "secret" not in outcome.model_dump_json()


@pytest.mark.asyncio
async def test_stream_iteration_failure_is_classified() -> None:
    outcome = await MoonshotExperimentGateway(
        client=_FakeClient([_FakeStream(_chunks(), failure_after=1)])
    ).generate(_request(factory=False))

    assert _failure_identity(outcome) == (
        GatewayFailureKind.NETWORK,
        "MOONSHOT_NETWORK_ERROR",
    )


def test_factory_sets_domestic_endpoint_disables_retries_and_hides_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=_FakeCompletions([]))

    monkeypatch.setattr(
        "experiments.moonshot_gateway.importlib.import_module",
        lambda name: SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI),
    )

    gateway = create_moonshot_experiment_gateway(api_key="test-secret-key")

    assert captured == {
        "api_key": "test-secret-key",
        "base_url": MOONSHOT_BASE_URL,
        "max_retries": 0,
    }
    assert not hasattr(gateway, "api_key")
    assert "test-secret-key" not in repr(gateway)


def test_factory_rejects_empty_key_or_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        create_moonshot_experiment_gateway(api_key=" ")

    def missing_module(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "experiments.moonshot_gateway.importlib.import_module",
        missing_module,
    )
    with pytest.raises(RuntimeError, match="requires the 'llm' optional dependency"):
        create_moonshot_experiment_gateway(api_key="test-key")


@pytest.mark.asyncio
async def test_locked_sdk_exposes_required_chat_completion_parameters() -> None:
    pytest.importorskip("openai")
    gateway = create_moonshot_experiment_gateway(api_key="test-key")
    try:
        parameters = inspect.signature(
            gateway.client.chat.completions.create
        ).parameters
        assert {
            "model",
            "messages",
            "temperature",
            "top_p",
            "n",
            "max_completion_tokens",
            "response_format",
            "stream",
            "stream_options",
            "extra_body",
            "timeout",
        } <= set(parameters)
    finally:
        await cast(_AsyncClosable, gateway.client).close()


def _failure_identity(
    outcome: GatewaySuccess | GatewayFailure,
) -> tuple[GatewayFailureKind, str]:
    assert isinstance(outcome, GatewayFailure)
    return outcome.kind, outcome.error_code
