"""Deterministic checksum contracts for M2 registry snapshots."""

from datetime import datetime

from agent_factory.domain.enums import (
    InjectionMode,
    KnowledgeKind,
    RuleKind,
    ToolPermission,
)
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationRule,
    EvaluationSuiteDraft,
)
from agent_factory.domain.models import (
    AgentSpec,
    KnowledgeSlot,
    PrototypeRef,
    ResolvedToolSpec,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.evaluation import checksum_evaluation_suite
from agent_factory.domain.services.skills import checksum_skill_tree
from agent_factory.domain.services.spec import checksum_agent_spec
from agent_factory.domain.skills import SkillNode, SkillTreeDraft

CHECKSUM = "a" * 64


def test_skill_tree_checksum_normalizes_unordered_sets() -> None:
    suite = EvaluationSuiteRef(
        suite_id="engineer-suite",
        version="1.0.0",
        checksum=CHECKSUM,
    )
    first = SkillTreeDraft(
        tree_id="engineer-skills",
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="senior-engineer",
                display_name="Senior Engineer",
                parents=frozenset(("review-engineer", "mid-engineer")),
                granted_tools=frozenset(("code-search", "test-runner")),
                added_knowledge_slots=(
                    KnowledgeSlot(
                        name="engineering-docs",
                        accepted_kinds=frozenset(
                            (KnowledgeKind.POLICY, KnowledgeKind.DOCUMENT)
                        ),
                        injection_mode=InjectionMode.RETRIEVAL,
                    ),
                ),
                evaluation_suite=suite,
            ),
            SkillNode(
                node_id="mid-engineer",
                display_name="Mid Engineer",
                evaluation_suite=suite,
            ),
            SkillNode(
                node_id="review-engineer",
                display_name="Review Engineer",
                evaluation_suite=suite,
            ),
        ),
    )
    second = SkillTreeDraft.model_validate_json(first.model_dump_json())

    assert checksum_skill_tree(first) == checksum_skill_tree(second)


def test_evaluation_suite_checksum_uses_definition_only() -> None:
    suite = EvaluationSuiteDraft(
        suite_id="engineer-suite",
        version="1.0.0",
        rules=(
            EvaluationRule(
                rule_id="required-test",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ["pytest"]},
            ),
        ),
        cases=(EvaluationCase(case_id="case-one", input="Implement a change."),),
    )

    assert checksum_evaluation_suite(suite) == checksum_evaluation_suite(
        EvaluationSuiteDraft.model_validate_json(suite.model_dump_json())
    )


def test_agent_spec_1_1_checksum_normalizes_skill_and_permission_sets(
    fixed_now: datetime,
) -> None:
    base = {
        "schema_version": "1.1",
        "instance_id": "00000000-0000-0000-0000-000000000501",
        "revision": 1,
        "prototype": PrototypeRef(
            prototype_id="engineer-agent",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        "agent_type": "engineer-agent",
        "role": "Engineer",
        "system_prompt": "Implement and test.",
        "knowledge": (),
        "output_schema": {},
        "skill_tree": SkillTreeRef(
            tree_id="engineer-skills",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        "runtime_target": None,
        "generated_at": fixed_now,
        "spec_checksum": CHECKSUM,
    }
    first = AgentSpec.model_validate(
        {
            **base,
            "tools": (
                ResolvedToolSpec(
                    name="test-runner",
                    version="1.0.0",
                    description="Run tests",
                    input_schema={},
                    output_schema={},
                    permission_tags=frozenset(
                        (ToolPermission.FILESYSTEM, ToolPermission.READ_ONLY)
                    ),
                ),
            ),
            "active_skill_nodes": frozenset(("review-engineer", "mid-engineer")),
        }
    )
    second = AgentSpec.model_validate_json(first.model_dump_json())

    assert checksum_agent_spec(first) == checksum_agent_spec(second)
