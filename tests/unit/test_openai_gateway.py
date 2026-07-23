"""OpenAI Responses mapping tests with a local structural fake client."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Protocol, cast

import pytest

from agent_factory.application.model_gateway import (
    ModelInvocation,
    ModelToolDefinition,
    ModelToolResult,
)
from agent_factory.domain.errors import ModelGatewayError, ModelProtocolError
from agent_factory.infrastructure.runtime.openai_gateway import (
    OpenAIResponsesGateway,
    create_openai_gateway,
)


class _FakeResponses:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = _FakeResponses(responses)


class _AsyncClosable(Protocol):
    async def close(self) -> None: ...


def _invocation() -> ModelInvocation:
    return ModelInvocation(
        instructions="Use verified knowledge.",
        task_input="Write a summary.",
        tools=(
            ModelToolDefinition(
                name="document-search",
                version="1.0.0",
                description="Search documents.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            ),
        ),
        output_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
        },
    )


def _response(
    *,
    output: list[object],
    output_text: str = "",
    prompt_tokens: int = 3,
    completion_tokens: int = 2,
) -> object:
    return SimpleNamespace(
        output=output,
        output_text=output_text,
        model="openai-test-model",
        usage=SimpleNamespace(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_openai_gateway_maps_tool_call_and_replays_validated_result() -> None:
    tool_item = {
        "type": "function_call",
        "call_id": "provider-call-1",
        "name": "document-search",
        "arguments": '{"query":"Agent Factory"}',
    }
    client = _FakeClient(
        [
            _response(output=[tool_item]),
            _response(
                output=[{"type": "message", "content": []}],
                output_text='{"title":"Agent Factory"}',
            ),
        ]
    )
    gateway = OpenAIResponsesGateway(client=client, model="configured-model")
    session = gateway.start(_invocation())

    first = await session.next()
    assert first.tool_call is not None
    assert first.tool_call.arguments == {"query": "Agent Factory"}

    second = await session.next(
        (
            ModelToolResult(
                provider_call_id="provider-call-1",
                output={"results": []},
            ),
        )
    )
    assert second.structured_output == {"title": "Agent Factory"}
    assert second.prompt_tokens == 3
    assert second.completion_tokens == 2

    first_call = client.responses.calls[0]
    assert first_call["parallel_tool_calls"] is False
    assert first_call["store"] is False
    tools = cast(list[dict[str, object]], first_call["tools"])
    assert tools[0]["strict"] is False

    second_input = cast(list[object], client.responses.calls[1]["input"])
    assert tool_item in second_input
    assert {
        "type": "function_call_output",
        "call_id": "provider-call-1",
        "output": '{"results":[]}',
    } in second_input


@pytest.mark.asyncio
async def test_openai_gateway_rejects_parallel_or_invalid_final_output() -> None:
    call = {
        "type": "function_call",
        "call_id": "call-1",
        "name": "document-search",
        "arguments": "{}",
    }
    parallel = _FakeClient([_response(output=[call, {**call, "call_id": "call-2"}])])
    session = OpenAIResponsesGateway(
        client=parallel,
        model="configured-model",
    ).start(_invocation())
    with pytest.raises(ModelProtocolError, match="invalid response"):
        await session.next()

    invalid = _FakeClient([_response(output=[], output_text="not-json")])
    session = OpenAIResponsesGateway(
        client=invalid,
        model="configured-model",
    ).start(_invocation())
    with pytest.raises(ModelProtocolError, match="invalid response"):
        await session.next()


@pytest.mark.asyncio
async def test_openai_gateway_normalizes_provider_exception() -> None:
    client = _FakeClient([RuntimeError("sensitive provider message")])
    session = OpenAIResponsesGateway(
        client=client,
        model="configured-model",
    ).start(_invocation())

    with pytest.raises(ModelGatewayError) as caught:
        await session.next()

    assert caught.value.code == "MODEL_GATEWAY_FAILED"
    assert "sensitive" not in caught.value.message


@pytest.mark.asyncio
async def test_optional_official_sdk_exposes_required_responses_parameters() -> None:
    pytest.importorskip("openai")
    gateway = create_openai_gateway(api_key="test-key", model="test-model")
    try:
        parameters = inspect.signature(gateway.client.responses.create).parameters
        assert {
            "model",
            "input",
            "instructions",
            "tools",
            "parallel_tool_calls",
            "store",
            "max_output_tokens",
            "text",
        } <= set(parameters)
    finally:
        await cast(_AsyncClosable, gateway.client).close()
