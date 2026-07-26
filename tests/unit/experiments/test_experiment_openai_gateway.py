"""OpenAI experiment mapping tests with no network or live credentials."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping
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
from experiments.openai_gateway import (
    OpenAIExperimentGateway,
    create_openai_experiment_gateway,
)

RUN_ID = UUID("70000000-0000-0000-0000-000000000001")
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
        },
        "next_action": {"type": "string", "minLength": 1},
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
_DEFAULT_OUTPUT = object()


class _FakeResponses:
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
        self.responses = _FakeResponses(outcomes)
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _FakeResponse:
    def __init__(
        self,
        *,
        request_id: str | None = "resp-pilot-1",
        status: str = "completed",
        output_text: object = _DEFAULT_OUTPUT,
        input_tokens: object = 125,
        output_tokens: object = 48,
        raw_updates: Mapping[str, object] | None = None,
    ) -> None:
        self.id = request_id
        self.status = status
        self.output_text = (
            _json_text(VALID_OUTPUT) if output_text is _DEFAULT_OUTPUT else output_text
        )
        self.usage = (
            None
            if input_tokens is None
            else SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        self._raw_updates = dict(raw_updates or {})

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        raw: dict[str, object] = {
            "id": self.id,
            "status": self.status,
            "output_text": self.output_text,
            "output": [],
            "usage": (
                None
                if self.usage is None
                else {
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                }
            ),
        }
        raw.update(self._raw_updates)
        return raw


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
        self.body = body or {"error": {"code": "provider_error"}}


class _AsyncClosable(Protocol):
    async def close(self) -> None: ...


def _json_text(value: Mapping[str, object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


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
            provider="openai",
            model="gpt-4.1-mini-2025-04-14",
            sdk_version="2.46.0",
            temperature=0,
            max_output_tokens=1024,
            seed=None,
            request_timeout_seconds=60,
            max_attempts=2,
            concurrency=1,
        ),
        invocation=invocation,
        expected_output_schema=OUTPUT_SCHEMA,
        prompt_hash=hashlib.sha256(canonical_json_bytes(invocation)).hexdigest(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [False, True])
async def test_gateway_maps_condition_format_and_normalizes_success(
    factory: bool,
) -> None:
    client = _FakeClient([_FakeResponse()])
    gateway = OpenAIExperimentGateway(client=client)

    outcome = await gateway.generate(_request(factory=factory))

    assert gateway.is_live is True
    assert isinstance(outcome, GatewaySuccess)
    assert outcome.provider_request_id == "resp-pilot-1"
    assert FrozenJsonObject(outcome.structured_output).to_builtin() == VALID_OUTPUT
    assert outcome.prompt_tokens == 125
    assert outcome.completion_tokens == 48
    assert outcome.raw_response["status"] == "completed"
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4.1-mini-2025-04-14"
    assert call["temperature"] == 0
    assert call["max_output_tokens"] == 1024
    assert call["store"] is False
    assert call["timeout"] == 60
    assert call["input"] == [
        {"role": "user", "content": "Write the current routing reference."}
    ]
    text = cast(dict[str, object], call["text"])
    response_format = cast(dict[str, object], text["format"])
    if factory:
        assert response_format == {
            "type": "json_schema",
            "name": "agent_factory_writer_output",
            "schema": OUTPUT_SCHEMA,
            "strict": True,
        }
    else:
        assert response_format == {"type": "json_object"}
    await gateway.close()
    assert client.closed == 1


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_request_before_sdk_call() -> None:
    client = _FakeClient([])
    gateway = OpenAIExperimentGateway(client=client)
    valid = _request(factory=True)

    provider = valid.generation.model_copy(update={"provider": "anthropic"})
    outcome = await gateway.generate(valid.model_copy(update={"generation": provider}))
    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "PROVIDER_MISMATCH",
    )

    seeded = valid.generation.model_copy(update={"seed": 7})
    outcome = await gateway.generate(valid.model_copy(update={"generation": seeded}))
    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "OPENAI_SEED_UNSUPPORTED",
    )

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
    assert client.responses.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invocation",
    [
        {"instructions": "instruction", "task_input": "task"},
        {
            "instructions": "",
            "task_input": "task",
            "output_schema": None,
        },
    ],
)
async def test_gateway_rejects_invalid_invocation_shape(
    invocation: dict[str, object],
) -> None:
    client = _FakeClient([])
    request = _request(factory=False).model_copy(update={"invocation": invocation})

    outcome = await OpenAIExperimentGateway(client=client).generate(request)

    assert _failure_identity(outcome) == (
        GatewayFailureKind.CLIENT_ERROR,
        "INVOCATION_INVALID",
    )
    assert client.responses.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "kind", "code"),
    [
        (
            _FakeResponse(request_id=None),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_REQUEST_ID_MISSING",
        ),
        (
            _FakeResponse(status="incomplete"),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_RESPONSE_INCOMPLETE",
        ),
        (
            _FakeResponse(request_id="r" * 257),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_REQUEST_ID_INVALID",
        ),
        (
            _FakeResponse(output_text=None),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_MISSING",
        ),
        (
            _FakeResponse(output_text="[]"),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_NOT_JSON_OBJECT",
        ),
        (
            _FakeResponse(output_text='{"title":"only one field"}'),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_OUTPUT_SCHEMA_INVALID",
        ),
        (
            _FakeResponse(input_tokens=None),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_USAGE_INVALID",
        ),
        (
            _FakeResponse(input_tokens=-1),
            GatewayFailureKind.INVALID_RESPONSE,
            "OPENAI_USAGE_INVALID",
        ),
        (
            _FakeResponse(
                status="incomplete",
                raw_updates={"incomplete_details": {"reason": "content_filter"}},
            ),
            GatewayFailureKind.FILTERED,
            "OPENAI_CONTENT_FILTERED",
        ),
        (
            _FakeResponse(raw_updates={"output": [{"type": "refusal"}]}),
            GatewayFailureKind.FILTERED,
            "OPENAI_CONTENT_FILTERED",
        ),
    ],
)
async def test_gateway_rejects_incomplete_or_invalid_provider_output(
    response: object,
    kind: GatewayFailureKind,
    code: str,
) -> None:
    gateway = OpenAIExperimentGateway(client=_FakeClient([response]))

    outcome = await gateway.generate(_request(factory=True))

    assert _failure_identity(outcome) == (kind, code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "kind", "code"),
    [
        (
            TimeoutError("secret timeout detail"),
            GatewayFailureKind.TIMED_OUT,
            "OPENAI_REQUEST_TIMED_OUT",
        ),
        (
            _ProviderError(429),
            GatewayFailureKind.RATE_LIMITED,
            "OPENAI_RATE_LIMITED",
        ),
        (
            _ProviderError(503),
            GatewayFailureKind.SERVER_ERROR,
            "OPENAI_SERVER_ERROR",
        ),
        (
            _ProviderError(400),
            GatewayFailureKind.CLIENT_ERROR,
            "OPENAI_CLIENT_ERROR",
        ),
        (
            OSError("secret network detail"),
            GatewayFailureKind.NETWORK,
            "OPENAI_NETWORK_ERROR",
        ),
        (
            RuntimeError("secret SDK detail"),
            GatewayFailureKind.CLIENT_ERROR,
            "OPENAI_SDK_ERROR",
        ),
    ],
)
async def test_gateway_classifies_provider_exceptions_without_message_leakage(
    exception: Exception,
    kind: GatewayFailureKind,
    code: str,
) -> None:
    gateway = OpenAIExperimentGateway(client=_FakeClient([exception]))

    outcome = await gateway.generate(_request(factory=False))

    assert _failure_identity(outcome) == (kind, code)
    assert isinstance(outcome, GatewayFailure)
    assert "secret" not in outcome.model_dump_json()
    if isinstance(exception, _ProviderError):
        assert outcome.provider_request_id == "req-error-1"
        assert outcome.raw_response == {"error": {"code": "provider_error"}}


@pytest.mark.asyncio
async def test_gateway_supports_mapping_response() -> None:
    raw = _FakeResponse().model_dump(mode="json")

    outcome = await OpenAIExperimentGateway(client=_FakeClient([raw])).generate(
        _request(factory=False)
    )

    assert isinstance(outcome, GatewaySuccess)
    assert FrozenJsonObject(outcome.structured_output).to_builtin() == VALID_OUTPUT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        object(),
        SimpleNamespace(model_dump=lambda **kwargs: []),
        SimpleNamespace(
            model_dump=lambda **kwargs: (_ for _ in ()).throw(RuntimeError())
        ),
    ],
)
async def test_gateway_contains_unserializable_sdk_responses(response: object) -> None:
    outcome = await OpenAIExperimentGateway(client=_FakeClient([response])).generate(
        _request(factory=True)
    )

    assert _failure_identity(outcome) == (
        GatewayFailureKind.INVALID_RESPONSE,
        "OPENAI_RESPONSE_INVALID",
    )


@pytest.mark.asyncio
async def test_gateway_drops_oversized_success_evidence() -> None:
    response = _FakeResponse(raw_updates={"padding": "x" * (1024 * 1024)})

    outcome = await OpenAIExperimentGateway(client=_FakeClient([response])).generate(
        _request(factory=True)
    )

    assert _failure_identity(outcome) == (
        GatewayFailureKind.INVALID_RESPONSE,
        "OPENAI_RESPONSE_TOO_LARGE",
    )


@pytest.mark.asyncio
async def test_gateway_bounds_provider_error_evidence_and_identity() -> None:
    error = _ProviderError(
        302,
        request_id="r" * 257,
        body={"payload": "x" * (64 * 1024)},
    )

    outcome = await OpenAIExperimentGateway(client=_FakeClient([error])).generate(
        _request(factory=False)
    )

    assert _failure_identity(outcome) == (
        GatewayFailureKind.NETWORK,
        "OPENAI_SDK_ERROR",
    )
    assert isinstance(outcome, GatewayFailure)
    assert outcome.provider_request_id is None
    assert outcome.raw_response is None


@pytest.mark.asyncio
async def test_gateway_uses_response_header_request_id() -> None:
    error = RuntimeError("sensitive")
    error.response = SimpleNamespace(headers={"x-request-id": "req-from-header"})  # type: ignore[attr-defined]

    outcome = await OpenAIExperimentGateway(client=_FakeClient([error])).generate(
        _request(factory=False)
    )

    assert isinstance(outcome, GatewayFailure)
    assert outcome.provider_request_id == "req-from-header"


def test_factory_disables_sdk_retries_and_does_not_store_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.responses = _FakeResponses([])

    monkeypatch.setattr(
        "experiments.openai_gateway.importlib.import_module",
        lambda name: SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI),
    )

    gateway = create_openai_experiment_gateway(api_key="test-secret-key")

    assert captured == {"api_key": "test-secret-key", "max_retries": 0}
    assert not hasattr(gateway, "api_key")
    assert "test-secret-key" not in repr(gateway)


def test_factory_rejects_empty_key_or_missing_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("", "   "):
        with pytest.raises(ValueError, match="must not be empty"):
            create_openai_experiment_gateway(api_key=key)

    def missing_module(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "experiments.openai_gateway.importlib.import_module", missing_module
    )
    with pytest.raises(RuntimeError, match="requires the 'llm' optional dependency"):
        create_openai_experiment_gateway(api_key="test-key")

    monkeypatch.setattr(
        "experiments.openai_gateway.importlib.import_module",
        lambda name: SimpleNamespace(),
    )
    with pytest.raises(RuntimeError, match="does not provide AsyncOpenAI"):
        create_openai_experiment_gateway(api_key="test-key")


@pytest.mark.asyncio
async def test_locked_official_sdk_exposes_required_responses_parameters() -> None:
    pytest.importorskip("openai")
    gateway = create_openai_experiment_gateway(api_key="test-key")
    try:
        parameters = inspect.signature(gateway.client.responses.create).parameters
        assert {
            "model",
            "instructions",
            "input",
            "temperature",
            "max_output_tokens",
            "store",
            "timeout",
            "text",
        } <= set(parameters)
    finally:
        await cast(_AsyncClosable, gateway.client).close()


def _failure_identity(
    outcome: GatewaySuccess | GatewayFailure,
) -> tuple[GatewayFailureKind, str]:
    assert isinstance(outcome, GatewayFailure)
    return outcome.kind, outcome.error_code
