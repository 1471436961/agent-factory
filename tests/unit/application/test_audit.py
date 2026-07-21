"""Unit tests for allowlisted audit payloads."""

from datetime import datetime
from uuid import UUID

from agent_factory.application.audit import AuditEventFactory
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import (
    AuditEventType,
    EvaluationDecision,
    KnowledgeKind,
    ReviewDecision,
    RuleKind,
)
from agent_factory.domain.evaluation import (
    CaseResultRef,
    EvaluationCase,
    EvaluationReport,
    EvaluationReview,
    EvaluationRule,
    EvaluationSuite,
    RuleResult,
)
from agent_factory.domain.models import DomainKnowledge
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillNode, SkillTree

EVENT_ID = UUID("00000000-0000-0000-0000-000000000201")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000301")


class FixedIdGenerator:
    def new(self) -> UUID:
        return EVENT_ID


def test_knowledge_audit_payload_excludes_content(fixed_now: datetime) -> None:
    content = "sensitive product instructions"
    knowledge = DomainKnowledge(
        knowledge_id="agent-factory-docs",
        version="1.0.0",
        name="Product Docs",
        kind=KnowledgeKind.DOCUMENT,
        content=content,
        checksum=checksum_knowledge_content(content),
        created_at=fixed_now,
        created_by="owner",
    )

    event = AuditEventFactory(FixedIdGenerator()).knowledge_registered(
        knowledge,
        actor="owner",
        correlation_id=CORRELATION_ID,
        at=fixed_now,
    )

    assert event.event_type is AuditEventType.KNOWLEDGE_REGISTERED
    assert event.event_id == EVENT_ID
    assert "content" not in event.payload
    assert content not in str(event.model_dump(mode="json")["payload"])


def test_governance_audit_payloads_exclude_raw_evidence_and_review_text(
    fixed_now: datetime,
) -> None:
    suite_ref = EvaluationSuiteRef(
        suite_id="engineer-readiness",
        version="1.0.0",
        checksum="a" * 64,
    )
    tree_ref = SkillTreeRef(
        tree_id="engineer-skills",
        version="1.0.0",
        checksum="b" * 64,
    )
    suite = EvaluationSuite(
        suite_id=suite_ref.suite_id,
        version=suite_ref.version,
        checksum=suite_ref.checksum,
        rules=(
            EvaluationRule(
                rule_id="mentions-pytest",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ("pytest",)},
            ),
        ),
        cases=(
            EvaluationCase(
                case_id="case-one",
                input="sensitive evaluation input",
            ),
        ),
        created_at=fixed_now,
        created_by="owner",
    )
    tree = SkillTree(
        tree_id=tree_ref.tree_id,
        version=tree_ref.version,
        checksum=tree_ref.checksum,
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                prompt_appendix="sensitive skill instructions",
                evaluation_suite=suite_ref,
            ),
        ),
        created_at=fixed_now,
        created_by="owner",
    )
    report_id = UUID("00000000-0000-0000-0000-000000000401")
    report = EvaluationReport(
        report_id=report_id,
        instance_id=UUID("00000000-0000-0000-0000-000000000001"),
        instance_revision=1,
        agent_spec_checksum="c" * 64,
        skill_tree=tree_ref,
        suite=suite_ref,
        runtime_model="test-model-1",
        case_results=(CaseResultRef(case_id="case-one", checksum="d" * 64),),
        rule_results=(
            RuleResult(
                rule_id="mentions-pytest",
                case_id="case-one",
                passed=True,
                score=1.0,
                evidence={"raw": "sensitive rule evidence"},
            ),
        ),
        hard_rules_passed=True,
        soft_score=1.0,
        decision=EvaluationDecision.REVIEW_REQUIRED,
        started_at=fixed_now,
        completed_at=fixed_now,
    )
    review = EvaluationReview(
        review_id=UUID("00000000-0000-0000-0000-000000000402"),
        report_id=report_id,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVED,
        comment="sensitive review comment",
        reviewed_at=fixed_now,
    )
    factory = AuditEventFactory(FixedIdGenerator())

    events = (
        factory.evaluation_suite_registered(
            suite,
            actor="owner",
            correlation_id=CORRELATION_ID,
            at=fixed_now,
        ),
        factory.skill_tree_registered(
            tree,
            actor="owner",
            correlation_id=CORRELATION_ID,
            at=fixed_now,
        ),
        factory.evaluation_completed(
            report,
            actor="owner",
            correlation_id=CORRELATION_ID,
            at=fixed_now,
        ),
        factory.evaluation_reviewed(
            review,
            actor="reviewer",
            correlation_id=CORRELATION_ID,
            at=fixed_now,
        ),
    )
    serialized = str([event.model_dump(mode="json") for event in events])

    assert {event.event_type for event in events} == {
        AuditEventType.EVALUATION_SUITE_REGISTERED,
        AuditEventType.SKILL_TREE_REGISTERED,
        AuditEventType.EVALUATION_COMPLETED,
        AuditEventType.EVALUATION_REVIEWED,
    }
    assert "sensitive evaluation input" not in serialized
    assert "sensitive skill instructions" not in serialized
    assert "sensitive rule evidence" not in serialized
    assert "sensitive review comment" not in serialized
