"""Pure validation and threshold policy for skill degradation."""

from agent_factory.domain.enums import (
    EvaluationDecision,
    InstanceStatus,
    ReviewDecision,
)
from agent_factory.domain.errors import (
    EvaluationSuiteMismatchError,
    InstanceBusyError,
    SkillNodeNotFoundError,
    SkillNotActiveError,
    SkillTreeNotBoundError,
    StaleEvaluationReportError,
    TaskOutcomeMismatchError,
)
from agent_factory.domain.evaluation import EvaluationReport, EvaluationReview
from agent_factory.domain.models import AgentInstance, AgentSpec
from agent_factory.domain.skills import (
    DegradationDecision,
    ObservationPolicy,
    SkillNode,
    SkillTree,
    TaskOutcome,
)


class DegradationPolicy:
    """Validate observation evidence and evaluate deterministic thresholds."""

    def validate_observation(
        self,
        *,
        instance: AgentInstance,
        spec: AgentSpec,
        tree: SkillTree,
        skill_node_id: str,
        report: EvaluationReport,
        review: EvaluationReview | None,
        passed: bool,
    ) -> SkillNode:
        self._validate_status(instance)
        if instance.skill_tree is None:
            raise SkillTreeNotBoundError(
                details={"instance_id": str(instance.instance_id)}
            )

        target = next(
            (node for node in tree.nodes if node.node_id == skill_node_id),
            None,
        )
        if target is None:
            raise SkillNodeNotFoundError(details={"node_id": skill_node_id})
        if target.node_id not in instance.active_skill_nodes:
            raise SkillNotActiveError(details={"node_id": target.node_id})

        if (
            spec.instance_id != instance.instance_id
            or spec.revision != instance.revision
            or spec.prototype != instance.prototype
            or spec.skill_tree != instance.skill_tree
            or spec.active_skill_nodes != instance.active_skill_nodes
            or report.instance_id != instance.instance_id
            or report.instance_revision != instance.revision
            or report.agent_spec_checksum != spec.spec_checksum
            or report.skill_tree != instance.skill_tree
        ):
            raise StaleEvaluationReportError(
                details={
                    "report_id": str(report.report_id),
                    "report_revision": report.instance_revision,
                    "instance_revision": instance.revision,
                }
            )
        if report.suite != target.evaluation_suite:
            raise EvaluationSuiteMismatchError(
                details={
                    "expected": target.evaluation_suite.model_dump(mode="json"),
                    "actual": report.suite.model_dump(mode="json"),
                    "node_id": target.node_id,
                }
            )

        evidence_passed = self._evidence_passed(report, review)
        if passed is not evidence_passed:
            raise TaskOutcomeMismatchError(
                details={
                    "report_id": str(report.report_id),
                    "submitted_passed": passed,
                    "evidence_passed": evidence_passed,
                }
            )
        return target

    @staticmethod
    def evaluate(
        outcomes: tuple[TaskOutcome, ...],
        policy: ObservationPolicy,
    ) -> DegradationDecision:
        ordered = sorted(
            outcomes,
            key=lambda outcome: (outcome.recorded_at, str(outcome.task_id)),
        )
        window = ordered[-policy.window_size :]
        trailing_failures = 0
        for outcome in reversed(window):
            if outcome.passed:
                break
            trailing_failures += 1
        sample_count = len(window)
        failure_rate = (
            sum(not outcome.passed for outcome in window) / sample_count
            if sample_count
            else 0.0
        )
        enough_samples = sample_count >= policy.minimum_samples
        should_degrade = enough_samples and (
            trailing_failures >= policy.consecutive_failures
            or failure_rate >= policy.failure_rate_threshold
        )
        return DegradationDecision(
            sample_count=sample_count,
            trailing_failures=trailing_failures,
            failure_rate=failure_rate,
            should_degrade=should_degrade,
        )

    @staticmethod
    def _validate_status(instance: AgentInstance) -> None:
        allowed = {
            InstanceStatus.CREATED,
            InstanceStatus.WAITING,
            InstanceStatus.DEGRADED,
        }
        if instance.status not in allowed:
            raise InstanceBusyError(
                details={
                    "instance_id": str(instance.instance_id),
                    "status": instance.status.value,
                    "operation": "record-task-outcome",
                }
            )

    @staticmethod
    def _evidence_passed(
        report: EvaluationReport,
        review: EvaluationReview | None,
    ) -> bool:
        if report.decision is EvaluationDecision.PASS:
            return True
        if report.decision is EvaluationDecision.FAIL:
            return False
        if review is None or review.report_id != report.report_id:
            raise TaskOutcomeMismatchError(
                details={
                    "report_id": str(report.report_id),
                    "reason": "final-review-required",
                }
            )
        return review.decision is ReviewDecision.APPROVED
