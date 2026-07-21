"""Tests for M2 skill-tree models and pure reconstruction algorithms."""

from collections.abc import Mapping
from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import InjectionMode, KnowledgeKind
from agent_factory.domain.errors import (
    InvalidOutputSchemaError,
    SkillConfigurationConflictError,
    SkillDependencyError,
    SkillNodeNotFoundError,
)
from agent_factory.domain.models import AgentDefinition, KnowledgeSlot
from agent_factory.domain.references import EvaluationSuiteRef
from agent_factory.domain.services.skills import (
    apply_skill_nodes,
    descendants_of,
    topological_order,
)
from agent_factory.domain.skills import (
    ObservationPolicy,
    SkillNode,
    SkillTree,
    SkillTreeDraft,
)

CHECKSUM = "a" * 64
SUITE_REF = EvaluationSuiteRef(
    suite_id="writer-suite",
    version="1.0.0",
    checksum=CHECKSUM,
)


def _node(
    node_id: str,
    *,
    parents: frozenset[str] = frozenset(),
    prompt_appendix: str = "",
    granted_tools: frozenset[str] = frozenset(),
    added_knowledge_slots: tuple[KnowledgeSlot, ...] = (),
    output_schema_override: Mapping[str, object] | None = None,
) -> SkillNode:
    return SkillNode(
        node_id=node_id,
        display_name=node_id,
        parents=parents,
        prompt_appendix=prompt_appendix,
        granted_tools=granted_tools,
        added_knowledge_slots=added_knowledge_slots,
        output_schema_override=output_schema_override,
        evaluation_suite=SUITE_REF,
    )


def _tree(fixed_now: datetime, *nodes: SkillNode) -> SkillTree:
    return SkillTree(
        tree_id="writer-skills",
        version="1.0.0",
        nodes=nodes,
        checksum=CHECKSUM,
        created_at=fixed_now,
        created_by="owner",
    )


def test_skill_tree_normalizes_nodes_and_accepts_valid_branching_dag(
    fixed_now: datetime,
) -> None:
    root = _node("junior-writer")
    mid = _node("mid-writer", parents=frozenset({root.node_id}))
    reviewer = _node("review-writer", parents=frozenset({root.node_id}))
    senior = _node(
        "senior-writer",
        parents=frozenset({mid.node_id, reviewer.node_id}),
    )

    tree = _tree(fixed_now, senior, reviewer, mid, root)

    assert [node.node_id for node in tree.nodes] == [
        "junior-writer",
        "mid-writer",
        "review-writer",
        "senior-writer",
    ]


def test_skill_tree_rejects_duplicate_missing_self_and_cycle() -> None:
    root = _node("junior-writer")
    with pytest.raises(ValidationError, match="ids must be unique"):
        SkillTreeDraft(
            tree_id="writer-skills",
            version="1.0.0",
            nodes=(root, root),
        )
    with pytest.raises(ValidationError, match="missing parents"):
        SkillTreeDraft(
            tree_id="writer-skills",
            version="1.0.0",
            nodes=(
                _node(
                    "mid-writer",
                    parents=frozenset({"missing-writer"}),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        SkillTreeDraft(
            tree_id="writer-skills",
            version="1.0.0",
            nodes=(
                _node(
                    "mid-writer",
                    parents=frozenset({"mid-writer"}),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="contains a cycle"):
        SkillTreeDraft(
            tree_id="writer-skills",
            version="1.0.0",
            nodes=(
                _node("mid-writer", parents=frozenset({"senior-writer"})),
                _node("senior-writer", parents=frozenset({"mid-writer"})),
            ),
        )


def test_topological_order_is_stable_and_requires_dependency_closure(
    fixed_now: datetime,
) -> None:
    tree = _tree(
        fixed_now,
        _node("junior-writer"),
        _node("mid-writer", parents=frozenset({"junior-writer"})),
        _node("review-writer", parents=frozenset({"junior-writer"})),
        _node(
            "senior-writer",
            parents=frozenset({"mid-writer", "review-writer"}),
        ),
    )
    active = frozenset(
        {"senior-writer", "review-writer", "junior-writer", "mid-writer"}
    )

    assert [node.node_id for node in topological_order(tree, active)] == [
        "junior-writer",
        "mid-writer",
        "review-writer",
        "senior-writer",
    ]
    with pytest.raises(SkillDependencyError):
        topological_order(tree, frozenset({"mid-writer"}))
    with pytest.raises(SkillNodeNotFoundError):
        topological_order(tree, frozenset({"unknown-writer"}))


def test_descendants_returns_transitive_children_only(
    fixed_now: datetime,
) -> None:
    tree = _tree(
        fixed_now,
        _node("junior-writer"),
        _node("mid-writer", parents=frozenset({"junior-writer"})),
        _node("review-writer", parents=frozenset({"junior-writer"})),
        _node("senior-writer", parents=frozenset({"mid-writer"})),
    )

    assert descendants_of(tree, "junior-writer") == frozenset(
        {"mid-writer", "review-writer", "senior-writer"}
    )
    assert descendants_of(tree, "review-writer") == frozenset()
    with pytest.raises(SkillNodeNotFoundError):
        descendants_of(tree, "unknown-writer")


def test_apply_skill_nodes_rebuilds_complete_definition_deterministically(
    fixed_now: datetime,
) -> None:
    legal_slot = KnowledgeSlot(
        name="legal-guidance",
        accepted_kinds=frozenset({KnowledgeKind.POLICY}),
        injection_mode=InjectionMode.RETRIEVAL,
    )
    output_schema = {
        "type": "object",
        "required": ["body"],
        "properties": {"body": {"type": "string"}},
    }
    tree = _tree(
        fixed_now,
        _node(
            "junior-writer",
            prompt_appendix="Use concise sentences.",
            granted_tools=frozenset({"style-checker"}),
        ),
        _node(
            "mid-writer",
            parents=frozenset({"junior-writer"}),
            prompt_appendix="Check policy constraints.",
            granted_tools=frozenset({"policy-search"}),
            added_knowledge_slots=(legal_slot,),
            output_schema_override=output_schema,
        ),
    )
    base = AgentDefinition(
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write a document.",
        tools=("document-search",),
    )
    active = frozenset({"mid-writer", "junior-writer"})

    first = apply_skill_nodes(base=base, tree=tree, active_node_ids=active)
    second = apply_skill_nodes(base=base, tree=tree, active_node_ids=active)

    assert first == second
    assert sha256_model(first) == sha256_model(second)
    assert first.system_prompt == (
        "Write a document.\n\n"
        "[skill:junior-writer]\nUse concise sentences.\n\n"
        "[skill:mid-writer]\nCheck policy constraints."
    )
    assert first.tools == ("document-search", "policy-search", "style-checker")
    assert [slot.name for slot in first.knowledge_slots] == ["legal-guidance"]
    assert first.model_dump(mode="json")["output_schema"] == output_schema


def test_apply_skill_nodes_rejects_slot_and_output_schema_conflicts(
    fixed_now: datetime,
) -> None:
    base_slot = KnowledgeSlot(
        name="product-docs",
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        injection_mode=InjectionMode.RETRIEVAL,
    )
    conflicting_slot = KnowledgeSlot(
        name="product-docs",
        accepted_kinds=frozenset({KnowledgeKind.POLICY}),
        injection_mode=InjectionMode.RETRIEVAL,
    )
    base = AgentDefinition(
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write.",
        knowledge_slots=(base_slot,),
    )
    slot_tree = _tree(
        fixed_now,
        _node("mid-writer", added_knowledge_slots=(conflicting_slot,)),
    )
    with pytest.raises(SkillConfigurationConflictError):
        apply_skill_nodes(
            base=base,
            tree=slot_tree,
            active_node_ids=frozenset({"mid-writer"}),
        )

    schema = {"type": "object"}
    override_tree = _tree(
        fixed_now,
        _node("mid-writer", output_schema_override=schema),
        _node("review-writer", output_schema_override=schema),
    )
    with pytest.raises(SkillConfigurationConflictError):
        apply_skill_nodes(
            base=base,
            tree=override_tree,
            active_node_ids=frozenset({"mid-writer", "review-writer"}),
        )


def test_apply_skill_nodes_rejects_invalid_schema_and_oversized_prompt(
    fixed_now: datetime,
) -> None:
    base = AgentDefinition(
        agent_type="writer-agent",
        role="Writer",
        system_prompt="x" * 32_000,
    )
    invalid_schema_tree = _tree(
        fixed_now,
        _node(
            "mid-writer",
            output_schema_override={"type": "not-a-json-schema-type"},
        ),
    )
    with pytest.raises(InvalidOutputSchemaError):
        apply_skill_nodes(
            base=base,
            tree=invalid_schema_tree,
            active_node_ids=frozenset({"mid-writer"}),
        )

    prompt_tree = _tree(
        fixed_now,
        _node("mid-writer", prompt_appendix="more"),
    )
    with pytest.raises(SkillConfigurationConflictError):
        apply_skill_nodes(
            base=base,
            tree=prompt_tree,
            active_node_ids=frozenset({"mid-writer"}),
        )


def test_observation_policy_requires_thresholds_inside_window() -> None:
    with pytest.raises(ValidationError, match="minimum_samples"):
        ObservationPolicy(window_size=3, minimum_samples=4)
    with pytest.raises(ValidationError, match="consecutive_failures"):
        ObservationPolicy(window_size=3, consecutive_failures=4)
