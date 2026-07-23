"""Optional OpenAI Responses API implementation of the model gateway boundary."""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from agent_factory.application.model_gateway import (
    ModelGateway,
    ModelInvocation,
    ModelSession,
    ModelToolCall,
    ModelToolResult,
    ModelTurn,
)
from agent_factory.domain.common import (
    FrozenJsonObject,
    JsonBuiltin,
    canonical_json_bytes,
)
from agent_factory.domain.errors import ModelGatewayError, ModelProtocolError


class _ResponsesResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _OpenAIClient(Protocol):
    @property
    def responses(self) -> _ResponsesResource: ...


@dataclass(frozen=True, slots=True)
class OpenAIResponsesGateway(ModelGateway):
    """Create isolated, statelessly replayed OpenAI Responses sessions."""

    client: _OpenAIClient
    model: str
    max_output_tokens: int = 4_096

    def __post_init__(self) -> None:
        if not 1 <= len(self.model) <= 128:
            raise ValueError("model must contain between 1 and 128 characters")
        if not 1 <= self.max_output_tokens <= 128_000:
            raise ValueError("max_output_tokens must be between 1 and 128000")

    def start(self, invocation: ModelInvocation) -> ModelSession:
        return _OpenAIResponsesSession(
            client=self.client,
            model=self.model,
            max_output_tokens=self.max_output_tokens,
            invocation=invocation,
        )


class _OpenAIResponsesSession(ModelSession):
    def __init__(
        self,
        *,
        client: _OpenAIClient,
        model: str,
        max_output_tokens: int,
        invocation: ModelInvocation,
    ) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        output_schema = canonical_json_bytes(invocation.output_schema).decode("utf-8")
        self._instructions = (
            f"{invocation.instructions}\n\n"
            "Return the final answer as one JSON object matching this schema: "
            f"{output_schema}"
        )
        self._input_items: list[object] = [
            {"role": "user", "content": invocation.task_input}
        ]
        self._tools = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": FrozenJsonObject(tool.input_schema).to_builtin(),
                # ToolExecutor remains authoritative for schema validation.
                "strict": False,
            }
            for tool in invocation.tools
        ]

    async def next(
        self,
        tool_results: tuple[ModelToolResult, ...] = (),
    ) -> ModelTurn:
        for result in tool_results:
            self._input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.provider_call_id,
                    "output": canonical_json_bytes(result.output).decode("utf-8"),
                }
            )

        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=self._instructions,
                input=self._input_items,
                tools=self._tools,
                parallel_tool_calls=False,
                store=False,
                max_output_tokens=self._max_output_tokens,
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise ModelGatewayError(
                details={"provider": "openai", "model": self._model}
            ) from exc

        output_items = self._output_items(response)
        self._input_items.extend(self._dump_item(item) for item in output_items)
        calls = [
            item for item in output_items if self._item_type(item) == "function_call"
        ]
        if len(calls) > 1:
            raise ModelProtocolError(details={"reason": "parallel-tool-calls"})

        prompt_tokens, completion_tokens = self._usage(response)
        response_model = self._string_attribute(response, "model") or self._model
        if calls:
            return ModelTurn(
                model_name=response_model,
                tool_call=self._tool_call(calls[0]),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        output_text = self._string_attribute(response, "output_text")
        if output_text is None or not output_text:
            raise ModelProtocolError(details={"reason": "missing-output-text"})
        try:
            parsed = json.loads(output_text)
            structured_output = FrozenJsonObject(parsed)
        except (TypeError, ValueError) as exc:
            raise ModelProtocolError(
                details={"reason": "final-output-is-not-json-object"}
            ) from exc
        return ModelTurn(
            model_name=response_model,
            content=output_text,
            structured_output=structured_output,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    @classmethod
    def _tool_call(cls, item: object) -> ModelToolCall:
        provider_call_id = cls._string_attribute(item, "call_id")
        name = cls._string_attribute(item, "name")
        raw_arguments = cls._string_attribute(item, "arguments")
        if provider_call_id is None or name is None or raw_arguments is None:
            raise ModelProtocolError(details={"reason": "incomplete-tool-call"})
        try:
            arguments = FrozenJsonObject(json.loads(raw_arguments))
        except (TypeError, ValueError) as exc:
            raise ModelProtocolError(
                details={"reason": "tool-arguments-are-not-json-object"}
            ) from exc
        try:
            return ModelToolCall(
                provider_call_id=provider_call_id,
                name=name,
                arguments=arguments,
            )
        except ValueError as exc:
            raise ModelProtocolError(
                details={"reason": "tool-call-contract-invalid"}
            ) from exc

    @staticmethod
    def _output_items(response: object) -> list[object]:
        output = getattr(response, "output", None)
        if not isinstance(output, list):
            raise ModelProtocolError(details={"reason": "missing-output-items"})
        return cast(list[object], output)

    @staticmethod
    def _item_type(item: object) -> str | None:
        if isinstance(item, Mapping):
            value = item.get("type")
        else:
            value = getattr(item, "type", None)
        return value if isinstance(value, str) else None

    @staticmethod
    def _string_attribute(item: object, name: str) -> str | None:
        if isinstance(item, Mapping):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        return value if isinstance(value, str) else None

    @staticmethod
    def _dump_item(item: object) -> dict[str, JsonBuiltin]:
        if isinstance(item, Mapping):
            return FrozenJsonObject(item).to_builtin()
        model_dump = getattr(item, "model_dump", None)
        if not callable(model_dump):
            raise ModelProtocolError(details={"reason": "output-item-not-serializable"})
        dumped = model_dump(mode="json")
        try:
            return FrozenJsonObject(dumped).to_builtin()
        except (TypeError, ValueError) as exc:
            raise ModelProtocolError(
                details={"reason": "output-item-not-json"}
            ) from exc

    @staticmethod
    def _usage(response: object) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0
        prompt_tokens = getattr(usage, "input_tokens", 0)
        completion_tokens = getattr(usage, "output_tokens", 0)
        if (
            not isinstance(prompt_tokens, int)
            or isinstance(prompt_tokens, bool)
            or prompt_tokens < 0
            or not isinstance(completion_tokens, int)
            or isinstance(completion_tokens, bool)
            or completion_tokens < 0
        ):
            raise ModelProtocolError(details={"reason": "invalid-token-usage"})
        return prompt_tokens, completion_tokens


def create_openai_gateway(
    *,
    api_key: str,
    model: str,
    timeout_seconds: float = 60.0,
    max_retries: int = 2,
    max_output_tokens: int = 4_096,
) -> OpenAIResponsesGateway:
    """Build the optional official SDK client without importing it by default."""

    if not api_key:
        raise ValueError("api_key must not be empty")
    try:
        module = importlib.import_module("openai")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenAI support requires the 'llm' optional dependency"
        ) from exc
    client_type = getattr(module, "AsyncOpenAI", None)
    if not callable(client_type):
        raise RuntimeError("installed OpenAI SDK does not provide AsyncOpenAI")
    client = client_type(
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )
    return OpenAIResponsesGateway(
        client=cast(_OpenAIClient, client),
        model=model,
        max_output_tokens=max_output_tokens,
    )
