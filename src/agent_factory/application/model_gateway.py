"""Provider-neutral contracts for bounded model-driven runtime execution."""

from __future__ import annotations

from typing import Protocol, Self

from pydantic import Field, model_validator

from agent_factory.domain.common import (
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    SemVer,
    Slug,
)


class ModelToolDefinition(FrozenModel):
    """One AgentSpec tool projected into a model-provider declaration."""

    name: Slug
    version: SemVer
    description: str = Field(min_length=1, max_length=1_000)
    input_schema: JsonObject


class ModelInvocation(FrozenModel):
    """Immutable input used to open one provider conversation."""

    instructions: str = Field(min_length=1, max_length=64_000)
    task_input: str = Field(min_length=1, max_length=64_000)
    tools: tuple[ModelToolDefinition, ...] = ()
    output_schema: JsonObject

    @model_validator(mode="after")
    def tool_names_must_be_unique(self) -> Self:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("model tools contain duplicate names")
        return self


class ModelToolCall(FrozenModel):
    """A single provider request to invoke a named factory-controlled tool."""

    provider_call_id: str = Field(min_length=1, max_length=256)
    name: Slug
    arguments: JsonObject


class ModelToolResult(FrozenModel):
    """Validated tool output returned to the model provider."""

    provider_call_id: str = Field(min_length=1, max_length=256)
    output: JsonObject = Field(default_factory=FrozenJsonObject)


class ModelTurn(FrozenModel):
    """Exactly one tool request or one final structured model result."""

    model_name: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=128_000)
    structured_output: JsonObject | None = None
    tool_call: ModelToolCall | None = None
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def outcome_must_be_exclusive(self) -> Self:
        if self.tool_call is not None:
            if self.structured_output is not None or self.content:
                raise ValueError("tool-call turn cannot contain final output")
            return self
        if self.structured_output is None:
            raise ValueError("final model turn requires structured_output")
        return self


class ModelSession(Protocol):
    """Provider-owned conversation state hidden from the runtime adapter."""

    async def next(
        self,
        tool_results: tuple[ModelToolResult, ...] = (),
    ) -> ModelTurn:
        """Advance the conversation by one bounded model turn."""


class ModelGateway(Protocol):
    """Create isolated provider sessions without leaking provider DTOs."""

    def start(self, invocation: ModelInvocation) -> ModelSession:
        """Create a session; network I/O begins only when ``next`` is awaited."""
