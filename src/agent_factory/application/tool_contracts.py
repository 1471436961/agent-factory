"""Immutable contracts for authorized runtime tool execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, PositiveInt, model_validator

from agent_factory.application.runtime import ResolvedRuntimeKnowledge
from agent_factory.domain.common import (
    Actor,
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    canonical_json_bytes,
)
from agent_factory.domain.models import AgentSpec, ResolvedToolSpec

ErrorCode = Annotated[
    str,
    Field(min_length=3, max_length=128, pattern=r"^[A-Z][A-Z0-9_]+$"),
]


class ToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed-out"


class ToolDefinition(ResolvedToolSpec):
    """Executable metadata whose resolved subset enters AgentSpec."""

    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    enabled: bool = True

    def resolved_spec(self) -> ResolvedToolSpec:
        return ResolvedToolSpec.model_validate(
            self.model_dump(
                mode="python",
                exclude={"timeout_seconds", "enabled"},
            )
        )


class ToolCallRequest(FrozenModel):
    call_id: UUID
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    tool_name: Slug
    tool_version: SemVer
    arguments: JsonObject


class ToolExecutionContext(FrozenModel):
    spec: AgentSpec
    knowledge: tuple[ResolvedRuntimeKnowledge, ...] = ()
    actor: Actor
    correlation_id: UUID

    @model_validator(mode="after")
    def knowledge_must_match_spec(self) -> Self:
        actual = [
            (
                item.slot_name,
                item.knowledge_id,
                item.version,
                item.checksum,
                item.injection_mode,
            )
            for item in self.knowledge
        ]
        if len(actual) != len(set(actual)):
            raise ValueError("tool knowledge contains duplicate references")
        expected = {
            (
                item.slot_name,
                item.knowledge_id,
                item.version,
                item.checksum,
                item.injection_mode,
            )
            for item in self.spec.knowledge
        }
        if set(actual) != expected:
            raise ValueError("tool knowledge does not match AgentSpec")
        return self


class ToolCallRecord(FrozenModel):
    call_id: UUID
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    tool_name: Slug
    tool_version: SemVer
    status: ToolCallStatus
    arguments_hash: Sha256
    result_hash: Sha256 | None = None
    error_code: ErrorCode | None = None
    duration_ms: int = Field(ge=0, le=600_000)
    actor: Actor
    correlation_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def outcome_must_be_consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status is ToolCallStatus.SUCCEEDED:
            if self.result_hash is None or self.error_code is not None:
                raise ValueError(
                    "succeeded tool call requires result_hash without error_code"
                )
        elif self.result_hash is not None or self.error_code is None:
            raise ValueError(
                "non-succeeded tool call requires error_code without result_hash"
            )
        return self


class ToolExecutionResult(FrozenModel):
    output: JsonObject
    record: ToolCallRecord


ToolHandler = Callable[
    [FrozenModel, ToolExecutionContext],
    Awaitable[FrozenModel],
]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    input_model: type[FrozenModel]
    output_model: type[FrozenModel]
    handler: ToolHandler

    def __post_init__(self) -> None:
        expected_input = self.input_model.model_json_schema(mode="validation")
        expected_output = self.output_model.model_json_schema(mode="validation")
        if canonical_json_bytes(self.definition.input_schema) != canonical_json_bytes(
            expected_input
        ):
            raise ValueError("tool input schema does not match input model")
        if canonical_json_bytes(self.definition.output_schema) != canonical_json_bytes(
            expected_output
        ):
            raise ValueError("tool output schema does not match output model")


class ToolRegistry(Protocol):
    """Resolve fixed executable tools by immutable name and version."""

    def get(self, name: str, version: str) -> RegisteredTool | None: ...

    def definitions(self) -> tuple[ToolDefinition, ...]: ...
