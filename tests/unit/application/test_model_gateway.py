"""Provider-neutral model gateway contract tests."""

import pytest
from pydantic import ValidationError

from agent_factory.application.model_gateway import (
    ModelInvocation,
    ModelToolCall,
    ModelToolDefinition,
    ModelTurn,
)


def _invocation() -> ModelInvocation:
    return ModelInvocation(
        instructions="Use only verified evidence.",
        task_input="Summarize Agent Factory.",
        tools=(
            ModelToolDefinition(
                name="document-search",
                version="1.0.0",
                description="Search bound documents.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            ),
        ),
        output_schema={"type": "object"},
    )


def test_model_invocation_is_immutable_and_has_unique_tools() -> None:
    invocation = _invocation()

    assert invocation.tools[0].version == "1.0.0"
    with pytest.raises(TypeError):
        invocation.output_schema["type"] = "array"  # type: ignore[index]

    duplicate = invocation.tools[0].model_copy(update={"version": "2.0.0"})
    payload = invocation.model_dump(mode="python")
    payload["tools"] = (invocation.tools[0], duplicate)
    with pytest.raises(ValidationError, match="duplicate names"):
        ModelInvocation.model_validate(payload)


def test_model_turn_requires_one_exclusive_outcome() -> None:
    call = ModelToolCall(
        provider_call_id="call-1",
        name="document-search",
        arguments={"query": "factory"},
    )
    tool_turn = ModelTurn(model_name="test-model", tool_call=call)
    final_turn = ModelTurn(
        model_name="test-model",
        content='{"title":"Factory"}',
        structured_output={"title": "Factory"},
    )

    assert tool_turn.tool_call == call
    assert final_turn.structured_output == {"title": "Factory"}

    with pytest.raises(ValidationError, match="requires structured_output"):
        ModelTurn(model_name="test-model")
    with pytest.raises(ValidationError, match="cannot contain final output"):
        ModelTurn(
            model_name="test-model",
            content="unexpected",
            tool_call=call,
        )
