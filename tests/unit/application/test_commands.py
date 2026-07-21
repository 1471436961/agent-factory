"""Application command validation tests."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    EvaluateInstanceCommand,
    KnowledgeSelection,
    PromoteAgentCommand,
    RecordTaskOutcomeCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
    ReviewEvaluationCommand,
)
from agent_factory.domain.enums import ReviewDecision, RuleKind
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import AgentDefinition, DomainKnowledgeDraft
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillNode, SkillTreeDraft


def test_register_commands_accept_valid_domain_inputs(
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    prototype = RegisterPrototypeCommand(
        prototype_id="writer-agent",
        version="1.0.0",
        definition=writer_definition,
        publish=True,
        actor="owner",
        idempotency_key="register-prototype-1",
    )
    knowledge = RegisterKnowledgeCommand(
        knowledge=product_knowledge_draft,
        actor="owner",
        idempotency_key="register-knowledge-1",
    )

    assert prototype.publish is True
    assert knowledge.knowledge.knowledge_id == "agent-factory-docs"


def test_command_rejects_short_idempotency_key(
    writer_definition: AgentDefinition,
) -> None:
    with pytest.raises(ValidationError, match="at least 8"):
        RegisterPrototypeCommand(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=writer_definition,
            actor="owner",
            idempotency_key="short",
        )


def test_clone_command_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        CloneAgentCommand.model_validate(
            {
                "prototype_id": "writer-agent",
                "prototype_version": "1.0.0",
                "actor": "owner",
                "unexpected": True,
            }
        )


def test_bind_command_requires_positive_revision() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        BindKnowledgeCommand(
            instance_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_revision=0,
            selections=(
                KnowledgeSelection(
                    slot_name="product-docs",
                    knowledge_id="agent-factory-docs",
                    version="1.0.0",
                ),
            ),
            actor="owner",
        )


def test_bind_command_requires_non_empty_unique_selections() -> None:
    instance_id = UUID("00000000-0000-0000-0000-000000000001")
    selection = KnowledgeSelection(
        slot_name="product-docs",
        knowledge_id="agent-factory-docs",
        version="1.0.0",
    )

    with pytest.raises(ValidationError, match="at least 1"):
        BindKnowledgeCommand(
            instance_id=instance_id,
            expected_revision=1,
            selections=(),
            actor="owner",
        )
    with pytest.raises(ValidationError, match="duplicate knowledge references"):
        BindKnowledgeCommand(
            instance_id=instance_id,
            expected_revision=1,
            selections=(selection, selection),
            actor="owner",
        )


def test_m2_governance_commands_preserve_validated_refs() -> None:
    instance_id = UUID("00000000-0000-0000-0000-000000000001")
    suite_ref = EvaluationSuiteRef(
        suite_id="engineer-readiness",
        version="1.0.0",
        checksum="a" * 64,
    )
    suite = EvaluationSuiteDraft(
        suite_id=suite_ref.suite_id,
        version=suite_ref.version,
        rules=(
            EvaluationRule(
                rule_id="mentions-pytest",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ("pytest",)},
            ),
        ),
        cases=(EvaluationCase(case_id="case-one", input="Explain testing."),),
    )
    tree = SkillTreeDraft(
        tree_id="engineer-skills",
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                evaluation_suite=suite_ref,
            ),
        ),
    )
    submission = EvaluationSubmission(
        instance_id=instance_id,
        instance_revision=1,
        suite=suite_ref,
        runtime_model="test-model-1",
        case_results=(
            SubmittedCaseResult(case_id="case-one", output_text="Use pytest."),
        ),
    )

    assert RegisterEvaluationSuiteCommand(suite=suite, actor="owner").suite == suite
    assert RegisterSkillTreeCommand(tree=tree, actor="owner").tree == tree
    assert (
        EvaluateInstanceCommand(submission=submission, actor="owner").submission
        == submission
    )
    review = ReviewEvaluationCommand(
        report_id=instance_id,
        decision=ReviewDecision.APPROVED,
        actor="reviewer",
    )
    assert review.comment == ""


def test_prototype_command_accepts_exact_skill_tree_ref(
    writer_definition: AgentDefinition,
) -> None:
    tree_ref = SkillTreeRef(
        tree_id="writer-skills",
        version="1.0.0",
        checksum="b" * 64,
    )

    command = RegisterPrototypeCommand(
        prototype_id="writer-agent",
        version="1.0.0",
        definition=writer_definition,
        skill_tree=tree_ref,
        actor="owner",
    )

    assert command.skill_tree == tree_ref


def test_review_command_bounds_comment_length() -> None:
    with pytest.raises(ValidationError, match="at most 2000"):
        ReviewEvaluationCommand(
            report_id=UUID("00000000-0000-0000-0000-000000000001"),
            decision=ReviewDecision.REJECTED,
            comment="x" * 2_001,
            actor="reviewer",
        )


def test_promote_command_accepts_empty_or_unique_knowledge_selections() -> None:
    instance_id = UUID("00000000-0000-0000-0000-000000000001")
    report_id = UUID("00000000-0000-0000-0000-000000000002")
    selection = KnowledgeSelection(
        slot_name="security-policy",
        knowledge_id="secure-coding-guide",
        version="1.0.0",
    )

    command = PromoteAgentCommand(
        instance_id=instance_id,
        expected_revision=1,
        target_node_id="security-engineer",
        evaluation_report_id=report_id,
        knowledge_selections=(selection,),
        actor="owner",
    )

    assert command.knowledge_selections == (selection,)
    assert command.evaluation_review_id is None
    with pytest.raises(ValidationError, match="duplicate knowledge references"):
        PromoteAgentCommand(
            instance_id=instance_id,
            expected_revision=1,
            target_node_id="security-engineer",
            evaluation_report_id=report_id,
            knowledge_selections=(selection, selection),
            actor="owner",
        )


def test_record_task_outcome_command_validates_revision_and_actor() -> None:
    command = RecordTaskOutcomeCommand(
        instance_id=UUID("00000000-0000-0000-0000-000000000001"),
        expected_revision=2,
        task_id=UUID("00000000-0000-0000-0000-000000000002"),
        skill_node_id="junior-engineer",
        passed=False,
        evaluation_report_id=UUID("00000000-0000-0000-0000-000000000003"),
        actor="owner",
        idempotency_key="observe-task-1",
    )

    assert command.expected_revision == 2
    assert command.passed is False
    with pytest.raises(ValidationError):
        RecordTaskOutcomeCommand(
            instance_id=command.instance_id,
            expected_revision=0,
            task_id=command.task_id,
            skill_node_id=command.skill_node_id,
            passed=False,
            evaluation_report_id=command.evaluation_report_id,
            actor="owner",
        )
