"""Pure lifecycle transitions for immutable Agent instance snapshots."""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from agent_factory.domain.enums import InstanceStatus
from agent_factory.domain.errors import InvalidStateTransitionError
from agent_factory.domain.models import AgentInstance

ALLOWED_TRANSITIONS: Mapping[InstanceStatus, frozenset[InstanceStatus]] = (
    MappingProxyType(
        {
            InstanceStatus.CREATED: frozenset(
                {InstanceStatus.RUNNING, InstanceStatus.TERMINATED}
            ),
            InstanceStatus.RUNNING: frozenset(
                {
                    InstanceStatus.WAITING,
                    InstanceStatus.COMPLETED,
                    InstanceStatus.FAILED,
                    InstanceStatus.TERMINATED,
                }
            ),
            InstanceStatus.WAITING: frozenset(
                {
                    InstanceStatus.RUNNING,
                    InstanceStatus.FAILED,
                    InstanceStatus.TERMINATED,
                }
            ),
            InstanceStatus.FAILED: frozenset(
                {InstanceStatus.RUNNING, InstanceStatus.TERMINATED}
            ),
            InstanceStatus.DEGRADED: frozenset(
                {InstanceStatus.RUNNING, InstanceStatus.TERMINATED}
            ),
            InstanceStatus.COMPLETED: frozenset(),
            InstanceStatus.TERMINATED: frozenset(),
        }
    )
)


class LifecyclePolicy:
    """Apply the closed lifecycle graph without infrastructure dependencies."""

    def transition(
        self,
        instance: AgentInstance,
        target_status: InstanceStatus,
        *,
        reason: str,
        retry: bool,
        now: datetime,
    ) -> AgentInstance:
        details = {
            "instance_id": str(instance.instance_id),
            "from_status": instance.status.value,
            "to_status": target_status.value,
        }
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 1_000:
            raise InvalidStateTransitionError(
                details={**details, "reason": "invalid-reason"}
            )
        if target_status is InstanceStatus.DEGRADED:
            raise InvalidStateTransitionError(
                details={**details, "reason": "degraded-status-is-policy-owned"}
            )
        if target_status not in ALLOWED_TRANSITIONS[instance.status]:
            raise InvalidStateTransitionError(
                details={**details, "reason": "transition-not-allowed"}
            )

        is_failed_retry = (
            instance.status is InstanceStatus.FAILED
            and target_status is InstanceStatus.RUNNING
        )
        if is_failed_retry and not retry:
            raise InvalidStateTransitionError(
                details={**details, "reason": "retry-required"}
            )
        if retry and not is_failed_retry:
            raise InvalidStateTransitionError(
                details={**details, "reason": "unexpected-retry-flag"}
            )

        return AgentInstance.model_validate(
            {
                **instance.model_dump(mode="python"),
                "revision": instance.revision + 1,
                "status": target_status,
                "updated_at": now,
            }
        )
