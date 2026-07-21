"""Unit tests for deterministic M2.5 observation and degradation policy."""

from datetime import datetime, timedelta
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
    SkillNotActiveError,
    StaleEvaluationReportError,
    TaskOutcomeMismatchError,
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
from agent_factory.domain.services.degradation import DegradationPolicy
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import (
    ObservationPolicy,
    SkillNode,
    SkillTree,
    TaskOutcome,
)

INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000002")


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
        revision=2,
        status=InstanceStatus.CREATED,
        configuration=AgentDefinition(
            agent_type="engineer-agent",
            role="Software Engineer",
            system_prompt="Produce verifiable engineering work.",
            output_schema={"type": "object"},
        ),
        active_skill_nodes=frozenset({"junior-engineer"}),
        skill_tree=SkillTreeRef(
            tree_id=tree.tree_id,
            version=tree.version,
            checksum=tree.checksum,
        ),
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="owner",
    )


def _report(
    instance: AgentInstance,
    fixed_now: datetime,
    *,
    decision: EvaluationDecision,
    suite: EvaluationSuiteRef | None = None,
) -> tuple[AgentSpec, EvaluationReport]:
    spec = AgentSpecBuilder().build(
        instance=instance,
        tools=(),
        generated_at=fixed_now,
    )
    assert instance.skill_tree is not None
    report = EvaluationReport(
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
    return spec, report


def _outcomes(fixed_now: datetime, values: tuple[bool, ...]) -> tuple[TaskOutcome, ...]:
    return tuple(
        TaskOutcome(
            task_id=UUID(int=index + 1),
            skill_node_id="junior-engineer",
            passed=passed,
            evaluation_report_id=UUID(int=100 + index),
            recorded_at=fixed_now + timedelta(minutes=index),
        )
        for index, passed in enumerate(values)
    )


def test_degradation_threshold_requires_minimum_samples(
    fixed_now: datetime,
) -> None:
    policy = ObservationPolicy(
        window_size=5,
        minimum_samples=3,
        consecutive_failures=2,
        failure_rate_threshold=0.5,
    )

    insufficient = DegradationPolicy.evaluate(
        _outcomes(fixed_now, (False, False)), policy
    )
    enough = DegradationPolicy.evaluate(
        _outcomes(fixed_now, (True, False, False)), policy
    )

    assert insufficient.should_degrade is False
    assert insufficient.trailing_failures == 2
    assert enough.should_degrade is True
    assert enough.failure_rate == pytest.approx(2 / 3)


def test_degradation_threshold_uses_latest_window_and_is_order_independent(
    fixed_now: datetime,
) -> None:
    policy = ObservationPolicy(
        window_size=3,
        minimum_samples=3,
        consecutive_failures=3,
        failure_rate_threshold=0.6,
    )
    outcomes = _outcomes(fixed_now, (False, True, False, True, False))

    decision = DegradationPolicy.evaluate(tuple(reversed(outcomes)), policy)

    assert decision.sample_count == 3
    assert decision.trailing_failures == 1
    assert decision.failure_rate == pytest.approx(2 / 3)
    assert decision.should_degrade is True


def test_observation_accepts_consistent_current_evidence(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec, report = _report(
        instance,
        fixed_now,
        decision=EvaluationDecision.FAIL,
    )

    target = DegradationPolicy().validate_observation(
        instance=instance,
        spec=spec,
        tree=tree,
        skill_node_id="junior-engineer",
        report=report,
        review=None,
        passed=False,
    )

    assert target.node_id == "junior-engineer"


def test_observation_requires_active_node_matching_suite_and_snapshot(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec, report = _report(instance, fixed_now, decision=EvaluationDecision.PASS)
    policy = DegradationPolicy()

    with pytest.raises(SkillNotActiveError):
        policy.validate_observation(
            instance=instance,
            spec=spec,
            tree=tree,
            skill_node_id="security-engineer",
            report=report,
            review=None,
            passed=True,
        )
    wrong_suite_spec, wrong_suite_report = _report(
        instance,
        fixed_now,
        decision=EvaluationDecision.PASS,
        suite=_suite_ref("security-readiness"),
    )
    with pytest.raises(EvaluationSuiteMismatchError):
        policy.validate_observation(
            instance=instance,
            spec=wrong_suite_spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=wrong_suite_report,
            review=None,
            passed=True,
        )
    with pytest.raises(StaleEvaluationReportError):
        policy.validate_observation(
            instance=instance.model_copy(update={"revision": 3}),
            spec=spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=report,
            review=None,
            passed=True,
        )


def test_observation_requires_resolved_consistent_final_decision(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now)
    spec, report = _report(
        instance,
        fixed_now,
        decision=EvaluationDecision.REVIEW_REQUIRED,
    )
    policy = DegradationPolicy()

    with pytest.raises(TaskOutcomeMismatchError, match="evaluation evidence"):
        policy.validate_observation(
            instance=instance,
            spec=spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=report,
            review=None,
            passed=True,
        )
    review = EvaluationReview(
        review_id=UUID(int=50),
        report_id=report.report_id,
        reviewer="reviewer",
        decision=ReviewDecision.REJECTED,
        reviewed_at=fixed_now,
    )
    with pytest.raises(TaskOutcomeMismatchError):
        policy.validate_observation(
            instance=instance,
            spec=spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=report,
            review=review,
            passed=True,
        )
    assert (
        policy.validate_observation(
            instance=instance,
            spec=spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=report,
            review=review,
            passed=False,
        ).node_id
        == "junior-engineer"
    )


def test_observation_rejects_non_governable_instance_state(
    fixed_now: datetime,
) -> None:
    tree = _tree(fixed_now)
    instance = _instance(tree, fixed_now).model_copy(
        update={"status": InstanceStatus.RUNNING}
    )
    spec, report = _report(instance, fixed_now, decision=EvaluationDecision.FAIL)

    with pytest.raises(InstanceBusyError):
        DegradationPolicy().validate_observation(
            instance=instance,
            spec=spec,
            tree=tree,
            skill_node_id="junior-engineer",
            report=report,
            review=None,
            passed=False,
        )
