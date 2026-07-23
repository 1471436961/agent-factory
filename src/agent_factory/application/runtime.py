"""Transport-neutral contracts between Agent Factory and runtime adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, PositiveInt, field_validator, model_validator

from agent_factory.domain.common import (
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    checksum_knowledge_content,
)
from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.models import AgentSpec


class RuntimeRunStatus(StrEnum):
    """Terminal outcome reported by a runtime adapter."""

    COMPLETED = "completed"
    FAILED = "failed"


class RuntimeContextRef(FrozenModel):
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    runtime_name: Slug
    external_thread_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    knowledge_namespaces: tuple[Slug, ...] = ()
    created_at: AwareDatetime

    @field_validator("knowledge_namespaces")
    @classmethod
    def namespaces_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("knowledge_namespaces contains duplicates")
        return value


class ResolvedRuntimeKnowledge(FrozenModel):
    slot_name: Slug
    knowledge_id: Slug
    version: SemVer
    checksum: Sha256
    injection_mode: InjectionMode
    mime_type: str = Field(min_length=1, max_length=128)
    content: str | JsonObject

    @model_validator(mode="after")
    def content_must_match_checksum(self) -> ResolvedRuntimeKnowledge:
        if checksum_knowledge_content(self.content) != self.checksum:
            raise ValueError("runtime knowledge content checksum does not match")
        return self


class RunRequest(FrozenModel):
    task_id: UUID
    spec: AgentSpec
    input: str = Field(min_length=1, max_length=64_000)
    knowledge: tuple[ResolvedRuntimeKnowledge, ...] = ()
    context_ref: RuntimeContextRef | None = None
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)

    @model_validator(mode="after")
    def sources_must_match_spec(self) -> RunRequest:
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
            raise ValueError("runtime knowledge contains duplicate references")
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
            raise ValueError("runtime knowledge does not match AgentSpec")

        context = self.context_ref
        if context is not None and (
            context.instance_id != self.spec.instance_id
            or context.instance_revision != self.spec.revision
            or context.agent_spec_checksum != self.spec.spec_checksum
        ):
            raise ValueError("runtime context does not match AgentSpec")
        return self


class RunResult(FrozenModel):
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    status: RuntimeRunStatus
    content: str = Field(default="", max_length=128_000)
    structured_output: JsonObject | None = None
    tool_call_ids: tuple[UUID, ...] = ()
    runtime_name: Slug
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    error_code: Annotated[
        str | None,
        Field(pattern=r"^[A-Z][A-Z0-9_]*$"),
    ] = None
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("tool_call_ids")
    @classmethod
    def tool_call_ids_must_be_unique(
        cls,
        value: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tool_call_ids contains duplicates")
        return value

    @model_validator(mode="after")
    def outcome_must_be_consistent(self) -> RunResult:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status is RuntimeRunStatus.FAILED and self.error_code is None:
            raise ValueError("failed runtime result requires error_code")
        if self.status is RuntimeRunStatus.COMPLETED and self.error_code is not None:
            raise ValueError("completed runtime result cannot contain error_code")
        return self


class RuntimeAdapter(Protocol):
    """Execute one validated request without owning factory persistence."""

    async def run(self, request: RunRequest) -> RunResult:
        """Return a terminal result for the supplied immutable request."""
