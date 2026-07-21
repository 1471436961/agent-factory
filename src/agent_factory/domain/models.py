"""Core Pydantic models for Agent Factory production snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    Field,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_factory.domain.common import (
    Actor,
    FrozenJsonObject,
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    semver_tuple,
)
from agent_factory.domain.enums import (
    Capability,
    InjectionMode,
    InstanceStatus,
    KnowledgeKind,
    PrototypeStatus,
    ToolPermission,
)
from agent_factory.domain.references import SkillTreeRef


class KnowledgeSlot(FrozenModel):
    name: Slug
    required: bool = True
    accepted_kinds: Annotated[frozenset[KnowledgeKind], Field(min_length=1)]
    min_version: SemVer = "0.0.0"
    max_version_exclusive: SemVer | None = None
    injection_mode: InjectionMode
    multiple: bool = False
    max_items: int = Field(default=1, ge=1, le=32)

    @model_validator(mode="after")
    def validate_cardinality_and_version(self) -> Self:
        if not self.multiple and self.max_items != 1:
            raise ValueError("max_items must be 1 when multiple is false")
        if self.max_version_exclusive is not None and semver_tuple(
            self.min_version
        ) >= semver_tuple(self.max_version_exclusive):
            raise ValueError("min_version must be lower than max_version_exclusive")
        return self


class AgentDefinition(FrozenModel):
    agent_type: Slug
    role: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=32_000)
    tools: tuple[Slug, ...] = ()
    capabilities: frozenset[Capability] = frozenset()
    output_schema: JsonObject = Field(default_factory=FrozenJsonObject)
    knowledge_slots: tuple[KnowledgeSlot, ...] = ()
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)

    @field_validator("tools")
    @classmethod
    def tools_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tools contains duplicate names")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_values_must_be_strings(
        cls,
        value: Mapping[str, object],
    ) -> Mapping[str, object]:
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("metadata values must be strings")
        return value

    @model_validator(mode="after")
    def slot_names_must_be_unique(self) -> Self:
        names = [slot.name for slot in self.knowledge_slots]
        if len(names) != len(set(names)):
            raise ValueError("knowledge slot names must be unique")
        return self


class PrototypeRef(FrozenModel):
    prototype_id: Slug
    version: SemVer
    checksum: Sha256


class AgentPrototype(FrozenModel):
    prototype_id: Slug
    version: SemVer
    status: PrototypeStatus = PrototypeStatus.DRAFT
    definition: AgentDefinition
    skill_tree: SkillTreeRef | None = None
    checksum: Sha256
    created_at: AwareDatetime
    created_by: Actor
    published_at: AwareDatetime | None = None
    deprecation_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )

    @model_validator(mode="after")
    def status_metadata_must_be_consistent(self) -> Self:
        if self.published_at is not None and self.published_at < self.created_at:
            raise ValueError("published_at must not precede created_at")
        if self.status is PrototypeStatus.DRAFT:
            if self.published_at is not None or self.deprecation_reason is not None:
                raise ValueError("draft prototype cannot have publication metadata")
        elif self.status is PrototypeStatus.PUBLISHED:
            if self.published_at is None or self.deprecation_reason is not None:
                raise ValueError(
                    "published prototype requires published_at without "
                    "deprecation_reason"
                )
        elif self.published_at is None or self.deprecation_reason is None:
            raise ValueError(
                "deprecated prototype requires publication and deprecation metadata"
            )
        return self


class DomainKnowledgeDraft(FrozenModel):
    knowledge_id: Slug
    version: SemVer
    name: str = Field(min_length=1, max_length=256)
    kind: KnowledgeKind
    content: str | JsonObject | None = None
    source_uri: AnyHttpUrl | None = None
    mime_type: str = Field(default="text/plain", min_length=1, max_length=128)
    checksum: Sha256
    tags: frozenset[Slug] = frozenset()

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> Self:
        if (self.content is None) == (self.source_uri is None):
            raise ValueError("exactly one of content or source_uri is required")
        return self


class DomainKnowledge(DomainKnowledgeDraft):
    created_at: AwareDatetime
    created_by: Actor


class KnowledgeBinding(FrozenModel):
    slot_name: Slug
    knowledge_id: Slug
    knowledge_version: SemVer
    knowledge_checksum: Sha256
    injection_mode: InjectionMode
    bound_at: AwareDatetime
    bound_by: Actor


class KnowledgeRef(FrozenModel):
    slot_name: Slug
    knowledge_id: Slug
    version: SemVer
    checksum: Sha256
    injection_mode: InjectionMode


class ResolvedToolSpec(FrozenModel):
    name: Slug
    version: SemVer
    description: str = Field(min_length=1, max_length=1_000)
    input_schema: JsonObject
    output_schema: JsonObject
    permission_tags: frozenset[ToolPermission]


class AgentInstance(FrozenModel):
    instance_id: UUID
    prototype: PrototypeRef
    revision: PositiveInt
    status: InstanceStatus
    configuration: AgentDefinition
    knowledge_bindings: tuple[KnowledgeBinding, ...] = ()
    active_skill_nodes: frozenset[Slug] = frozenset()
    skill_tree: SkillTreeRef | None = None
    runtime_target: Slug | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    created_by: Actor

    @model_validator(mode="after")
    def updated_at_must_not_precede_creation(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.active_skill_nodes and self.skill_tree is None:
            raise ValueError("active skill nodes require a skill tree")
        return self


class AgentSpec(FrozenModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    instance_id: UUID
    revision: PositiveInt
    prototype: PrototypeRef
    agent_type: Slug
    role: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=32_000)
    tools: tuple[ResolvedToolSpec, ...]
    knowledge: tuple[KnowledgeRef, ...]
    output_schema: JsonObject
    active_skill_nodes: frozenset[Slug] = frozenset()
    skill_tree: SkillTreeRef | None = None
    runtime_target: Slug | None = None
    generated_at: AwareDatetime
    spec_checksum: Sha256
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)

    @field_validator("metadata")
    @classmethod
    def metadata_values_must_be_strings(
        cls,
        value: Mapping[str, object],
    ) -> Mapping[str, object]:
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("metadata values must be strings")
        return value

    @model_validator(mode="after")
    def schema_version_must_match_skill_tree(self) -> Self:
        if self.schema_version == "1.0" and self.skill_tree is not None:
            raise ValueError("AgentSpec 1.0 cannot contain a skill tree")
        if self.schema_version == "1.1" and self.skill_tree is None:
            raise ValueError("AgentSpec 1.1 requires a skill tree")
        if self.active_skill_nodes and self.skill_tree is None:
            raise ValueError("active skill nodes require a skill tree")
        return self
