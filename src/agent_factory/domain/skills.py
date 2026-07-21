"""Immutable M2 skill-tree and observation models."""

from __future__ import annotations

import heapq
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, PositiveInt, field_validator, model_validator

from agent_factory.domain.common import (
    Actor,
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
)
from agent_factory.domain.models import KnowledgeSlot
from agent_factory.domain.references import EvaluationSuiteRef


class ObservationPolicy(FrozenModel):
    window_size: int = Field(default=10, ge=3, le=100)
    minimum_samples: int = Field(default=5, ge=1, le=100)
    consecutive_failures: int = Field(default=3, ge=1, le=20)
    failure_rate_threshold: float = Field(default=0.5, gt=0, le=1)

    @model_validator(mode="after")
    def thresholds_must_fit_window(self) -> Self:
        if self.minimum_samples > self.window_size:
            raise ValueError("minimum_samples cannot exceed window_size")
        if self.consecutive_failures > self.window_size:
            raise ValueError("consecutive_failures cannot exceed window_size")
        return self


class SkillNode(FrozenModel):
    node_id: Slug
    display_name: str = Field(min_length=1, max_length=128)
    parents: frozenset[Slug] = frozenset()
    prompt_appendix: str = Field(default="", max_length=8_000)
    granted_tools: frozenset[Slug] = frozenset()
    added_knowledge_slots: tuple[KnowledgeSlot, ...] = ()
    output_schema_override: JsonObject | None = None
    evaluation_suite: EvaluationSuiteRef
    observation_policy: ObservationPolicy = Field(default_factory=ObservationPolicy)

    @field_validator("added_knowledge_slots")
    @classmethod
    def added_slots_must_be_unique(
        cls,
        value: tuple[KnowledgeSlot, ...],
    ) -> tuple[KnowledgeSlot, ...]:
        names = [slot.name for slot in value]
        if len(names) != len(set(names)):
            raise ValueError("added knowledge slot names must be unique")
        return tuple(sorted(value, key=lambda slot: slot.name))


class SkillTreeDraft(FrozenModel):
    tree_id: Slug
    version: SemVer
    nodes: Annotated[tuple[SkillNode, ...], Field(min_length=1)]

    @field_validator("nodes")
    @classmethod
    def nodes_must_be_unique_and_sorted(
        cls,
        value: tuple[SkillNode, ...],
    ) -> tuple[SkillNode, ...]:
        ids = [node.node_id for node in value]
        if len(ids) != len(set(ids)):
            raise ValueError("skill node ids must be unique")
        return tuple(sorted(value, key=lambda node: node.node_id))

    @model_validator(mode="after")
    def graph_must_be_a_dag(self) -> Self:
        by_id = {node.node_id: node for node in self.nodes}
        ids = set(by_id)
        children = {node_id: set[str]() for node_id in ids}
        indegree = {node_id: 0 for node_id in ids}

        for node in self.nodes:
            if node.node_id in node.parents:
                raise ValueError(f"node {node.node_id} cannot depend on itself")
            missing = set(node.parents) - ids
            if missing:
                raise ValueError(
                    f"node {node.node_id} has missing parents: {sorted(missing)}"
                )
            indegree[node.node_id] = len(node.parents)
            for parent in node.parents:
                children[parent].add(node.node_id)

        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        visited = 0
        while ready:
            current = heapq.heappop(ready)
            visited += 1
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, child)
        if visited != len(ids):
            raise ValueError("skill tree contains a cycle")
        return self


class SkillTree(SkillTreeDraft):
    checksum: Sha256
    created_at: AwareDatetime
    created_by: Actor


class TaskOutcome(FrozenModel):
    task_id: UUID
    skill_node_id: Slug
    passed: bool
    evaluation_report_id: UUID
    recorded_at: AwareDatetime


class DegradationDecision(FrozenModel):
    sample_count: int = Field(ge=0, le=100)
    trailing_failures: int = Field(ge=0, le=100)
    failure_rate: float = Field(ge=0, le=1)
    should_degrade: bool


class DegradationCheckResult(FrozenModel):
    instance_id: UUID
    checked_revision: PositiveInt
    degraded: bool
    resulting_revision: PositiveInt
    removed_nodes: frozenset[Slug] = frozenset()
    removed_binding_slots: frozenset[Slug] = frozenset()
