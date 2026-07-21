"""Pure policy for validating an explicit Agent skill promotion."""

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
from agent_factory.domain.evaluation import EvaluationReport, EvaluationReview
from agent_factory.domain.models import AgentInstance, AgentSpec
from agent_factory.domain.skills import SkillNode, SkillTree


class PromotionPolicy:
    """Validate promotion evidence without accessing clocks or repositories."""

    def validate(
        self,
        *,
        instance: AgentInstance,
        spec: AgentSpec,
        tree: SkillTree,
        target_node_id: str,
        report: EvaluationReport,
        review: EvaluationReview | None,
    ) -> SkillNode:
        self._validate_status(instance)
        if instance.skill_tree is None:
            raise SkillTreeNotBoundError(
                details={"instance_id": str(instance.instance_id)}
            )

        target = next(
            (node for node in tree.nodes if node.node_id == target_node_id),
            None,
        )
        if target is None:
            raise SkillNodeNotFoundError(details={"node_id": target_node_id})
        if target.node_id in instance.active_skill_nodes:
            raise SkillAlreadyActiveError(details={"node_id": target.node_id})
        missing = set(target.parents) - set(instance.active_skill_nodes)
        if missing:
            raise SkillDependencyError(
                details={
                    "node_id": target.node_id,
                    "missing": sorted(missing),
                }
            )

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

        self._validate_decision(report, review)
        return target

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
                    "operation": "promote-agent",
                }
            )

    @staticmethod
    def _validate_decision(
        report: EvaluationReport,
        review: EvaluationReview | None,
    ) -> None:
        if report.decision is EvaluationDecision.PASS:
            if review is not None:
                raise PromotionRejectedError(
                    details={
                        "report_id": str(report.report_id),
                        "reason": "review-not-required",
                    }
                )
            return
        if report.decision is EvaluationDecision.REVIEW_REQUIRED:
            if (
                review is not None
                and review.report_id == report.report_id
                and review.decision is ReviewDecision.APPROVED
            ):
                return
            raise PromotionRejectedError(
                details={
                    "report_id": str(report.report_id),
                    "reason": "approved-review-required",
                }
            )
        raise PromotionRejectedError(
            details={
                "report_id": str(report.report_id),
                "decision": report.decision.value,
                "reason": "evaluation-failed",
            }
        )
