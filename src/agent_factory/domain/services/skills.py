"""Pure skill-tree traversal and full configuration reconstruction."""

from __future__ import annotations

import heapq

from pydantic import ValidationError

from agent_factory.domain.common import Slug
from agent_factory.domain.errors import (
    SkillConfigurationConflictError,
    SkillDependencyError,
    SkillNodeNotFoundError,
    SkillTreeCycleError,
)
from agent_factory.domain.models import AgentDefinition
from agent_factory.domain.skills import SkillNode, SkillTree
from agent_factory.domain.validation import validate_output_schema


def _nodes_by_id(tree: SkillTree) -> dict[str, SkillNode]:
    return {node.node_id: node for node in tree.nodes}


def topological_order(
    tree: SkillTree,
    active_node_ids: frozenset[Slug],
) -> tuple[SkillNode, ...]:
    """Return the dependency-safe stable order for an active skill subset."""

    by_id = _nodes_by_id(tree)
    unknown = set(active_node_ids) - set(by_id)
    if unknown:
        raise SkillNodeNotFoundError(details={"nodes": sorted(unknown)})

    for node_id in active_node_ids:
        missing = set(by_id[node_id].parents) - set(active_node_ids)
        if missing:
            raise SkillDependencyError(
                details={"node_id": node_id, "missing": sorted(missing)}
            )

    remaining = set(active_node_ids)
    ordered: list[SkillNode] = []
    ready = [
        node_id
        for node_id in remaining
        if set(by_id[node_id].parents).isdisjoint(remaining)
    ]
    heapq.heapify(ready)

    while ready:
        node_id = heapq.heappop(ready)
        if node_id not in remaining:
            continue
        ordered.append(by_id[node_id])
        remaining.remove(node_id)
        for candidate in sorted(remaining):
            if (
                set(by_id[candidate].parents).isdisjoint(remaining)
                and candidate not in ready
            ):
                heapq.heappush(ready, candidate)

    if remaining:
        raise SkillTreeCycleError(details={"nodes": sorted(remaining)})
    return tuple(ordered)


def descendants_of(tree: SkillTree, node_id: Slug) -> frozenset[Slug]:
    """Return every transitive child of a node, excluding the node itself."""

    by_id = _nodes_by_id(tree)
    if node_id not in by_id:
        raise SkillNodeNotFoundError(details={"nodes": [node_id]})

    children = {current: set[str]() for current in by_id}
    for node in tree.nodes:
        for parent in node.parents:
            children[parent].add(node.node_id)

    descendants: set[str] = set()
    pending = list(children[node_id])
    while pending:
        current = pending.pop()
        if current in descendants:
            continue
        descendants.add(current)
        pending.extend(children[current])
    return frozenset(descendants)


def apply_skill_nodes(
    *,
    base: AgentDefinition,
    tree: SkillTree,
    active_node_ids: frozenset[Slug],
) -> AgentDefinition:
    """Rebuild a definition from its immutable base and complete active set."""

    ordered = topological_order(tree, active_node_ids)
    override_nodes = [
        node.node_id for node in ordered if node.output_schema_override is not None
    ]
    if len(override_nodes) > 1:
        raise SkillConfigurationConflictError(
            details={
                "field": "output_schema",
                "nodes": override_nodes,
            }
        )

    prompt_parts = [base.system_prompt]
    tools = set(base.tools)
    slots = {slot.name: slot for slot in base.knowledge_slots}
    output_schema = base.output_schema

    for node in ordered:
        if node.prompt_appendix:
            prompt_parts.append(f"[skill:{node.node_id}]\n{node.prompt_appendix}")
        tools.update(node.granted_tools)
        for slot in node.added_knowledge_slots:
            existing = slots.get(slot.name)
            if existing is not None and existing != slot:
                raise SkillConfigurationConflictError(
                    details={
                        "field": "knowledge_slots",
                        "slot_name": slot.name,
                        "node_id": node.node_id,
                    }
                )
            slots[slot.name] = slot
        if node.output_schema_override is not None:
            validate_output_schema(node.output_schema_override)
            output_schema = node.output_schema_override

    try:
        return AgentDefinition.model_validate(
            {
                **base.model_dump(mode="python"),
                "system_prompt": "\n\n".join(prompt_parts),
                "tools": tuple(sorted(tools)),
                "knowledge_slots": tuple(slots[name] for name in sorted(slots)),
                "output_schema": output_schema,
            }
        )
    except ValidationError as exc:
        raise SkillConfigurationConflictError(
            details={"field": "agent_definition", "reason": "validation_failed"}
        ) from exc
