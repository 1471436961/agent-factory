"""Unit tests for deterministic M2.4 promotion decisions."""

from datetime import datetime
from uuid import UUID

import pytest

from agent_factory.domain.enums import (
    EvaluationDecision,
    InstanceStatus,
    ReviewDecision,
)
from agent_factory.domain.errors import (
    EvaluationSuiteMismatchError,
    InstanceBusyError,
    PromotionRejectedError,
    SkillAlreadyActiveError,
    SkillDependencyError,
    SkillNodeNotFoundError,
    SkillTreeNotBoundError,
    StaleEvaluationReportError,
)
from agent_factory.domain.evaluation import (
    CaseResultRef,
    EvaluationReport,
    EvaluationReview,
    RuleResult,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentSpec,
    PrototypeRef,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.promotion import PromotionPolicy
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import SkillNode, SkillTree

INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000002")
REVIEW_ID = UUID("00000000-0000-0000-0000-000000000003")


def _suite_ref(suite_id: str = "engineer-readiness") -> EvaluationSuiteRef:
    return EvaluationSuiteRef(
        suite_id=suite_id,
        version="1.0.0",
        checksum=("a" if suite_id == "engineer-readiness" else "b") * 64,
    )


def _tree(fixed_now: datetime) -> SkillTree:
    return SkillTree(
        tree_id="engineer-skills",
        version="1.0.0",
        checksum="c" * 64,
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                evaluation_suite=_suite_ref(),
            ),
            SkillNode(
                node_id="mid-engineer",
                display_name="Mid Engineer",
                parents=frozenset({"junior-engineer"}),
                evaluation_suite=_suite_ref(),
            ),
            SkillNode(
                node_id="security-engineer",
                display_name="Security Engineer",
                evaluation_suite=_suite_ref("security-readiness"),
            ),
        ),
        created_at=fixed_now,
        created_by="owner",
    )


def _instance(tree: SkillTree, fixed_now: datetime) -> AgentInstance:
    return AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id="engineer-agent",
            version="1.0.0",
            checksum="d" * 64,
        ),
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=AgentDefinition(
            agent_type="engineer-agent",
            role="Software Engineer",
            system_prompt="Produce verifiable engineering work.",
            output_schema={"type": "object"},
        ),
        skill_tree=SkillTreeRef(
            tree_id=tree.tree_id,
            version=tree.version,
            checksum=tree.checksum,
        ),
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="owner",
    )


def _spec(instance: AgentInstance, fixed_now: datetime) -> AgentSpec:
    return AgentSpecBuilder().build(
        instance=instance,
        tools=(),
        generated_at=fixed_now,
    )


def _report(
    instance: AgentInstance,
    spec: AgentSpec,
    fixed_now: datetime,
    *,
    decision: EvaluationDecision = EvaluationDecision.PASS,
    suite: EvaluationSuiteRef | None = None,
) -> EvaluationReport:
    assert instance.skill_tree is not None
    return EvaluationReport(
        report_id=REPORT_ID,
        instance_id=instance.instance_id,
        instance_revision=instance.revision,
        agent_spec_checksum=spec.spec_checksum,
        skill_tree=instance.skill_tree,
        suite=suite or _suite_ref(),
        runtime_model="test-model-1",
        case_results=(CaseResultRef(case_id="case-one", checksum="e" * 64),),
        rule_results=(
            RuleResult(
                rule_id="rule-one",
                case_id="case-one",
                passed=decision is not EvaluationDecision.FAIL,
                score=0 if decision is EvaluationDecision.FAIL else 1,
            ),
        ),
        hard_rules_passed=decision is not EvaluationDecision.FAIL,
        soft_score=0 if decision is EvaluationDecision.FAIL else 1,
        decision=decision,
        started_at=fixed_now,
        completed_at=fixed_now,
    )


def _review(
    fixed_now: datetime,
    *,
    decision: ReviewDecision = ReviewDecision.APPROVED,
    report_id: UUID = REPORT_ID,
) -> EvaluationReview:
    return EvaluationReview(
        review_id=REVIEW_ID,
        report_id=report_id,
        reviewer="reviewer",
        decision=decision,
        reviewed_at=fixed_now,
    )


def test_promotion_policy_accepts_passed_current_evidence(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec = _spec(instance, fixed_now)

    target = PromotionPolicy().validate(
        instance=instance,
        spec=spec,
        tree=tree,
        target_node_id="junior-engineer",
        report=_report(instance, spec, fixed_now),
        review=None,
    )

    assert target.node_id == "junior-engineer"


def test_promotion_policy_rejects_invalid_state_tree_and_node(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec = _spec(instance, fixed_now)
    report = _report(instance, spec, fixed_now)
    policy = PromotionPolicy()

    with pytest.raises(InstanceBusyError):
        policy.validate(
            instance=instance.model_copy(update={"status": InstanceStatus.RUNNING}),
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=report,
            review=None,
        )
    with pytest.raises(SkillTreeNotBoundError):
        policy.validate(
            instance=instance.model_copy(update={"skill_tree": None}),
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=report,
            review=None,
        )
    with pytest.raises(SkillNodeNotFoundError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="unknown-node",
            report=report,
            review=None,
        )


def test_promotion_policy_rejects_active_node_and_missing_parent(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec = _spec(instance, fixed_now)
    report = _report(instance, spec, fixed_now)
    policy = PromotionPolicy()

    with pytest.raises(SkillDependencyError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="mid-engineer",
            report=report,
            review=None,
        )

    active = instance.model_copy(
        update={"active_skill_nodes": frozenset({"junior-engineer"})}
    )
    active_spec = _spec(active, fixed_now)
    active_report = _report(active, active_spec, fixed_now)
    with pytest.raises(SkillAlreadyActiveError):
        policy.validate(
            instance=active,
            spec=active_spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=active_report,
            review=None,
        )


def test_promotion_policy_rejects_stale_or_wrong_suite_evidence(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec = _spec(instance, fixed_now)
    report = _report(instance, spec, fixed_now)
    policy = PromotionPolicy()

    with pytest.raises(StaleEvaluationReportError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=report.model_copy(update={"agent_spec_checksum": "f" * 64}),
            review=None,
        )
    with pytest.raises(EvaluationSuiteMismatchError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="security-engineer",
            report=report,
            review=None,
        )


def test_promotion_policy_enforces_report_and_review_decisions(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec = _spec(instance, fixed_now)
    policy = PromotionPolicy()

    with pytest.raises(PromotionRejectedError, match="requirements"):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=_report(
                instance,
                spec,
                fixed_now,
                decision=EvaluationDecision.FAIL,
            ),
            review=None,
        )

    manual_report = _report(
        instance,
        spec,
        fixed_now,
        decision=EvaluationDecision.REVIEW_REQUIRED,
    )
    with pytest.raises(PromotionRejectedError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=manual_report,
            review=None,
        )
    with pytest.raises(PromotionRejectedError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=manual_report,
            review=_review(fixed_now, decision=ReviewDecision.REJECTED),
        )

    target = policy.validate(
        instance=instance,
        spec=spec,
        tree=tree,
        target_node_id="junior-engineer",
        report=manual_report,
        review=_review(fixed_now),
    )
    assert target.node_id == "junior-engineer"

    with pytest.raises(PromotionRejectedError):
        policy.validate(
            instance=instance,
            spec=spec,
            tree=tree,
            target_node_id="junior-engineer",
            report=_report(instance, spec, fixed_now),
            review=_review(fixed_now),
        )
