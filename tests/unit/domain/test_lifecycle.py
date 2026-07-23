"""Closed lifecycle graph tests."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from agent_factory.domain.enums import InstanceStatus
from agent_factory.domain.errors import InvalidStateTransitionError
from agent_factory.domain.models import AgentDefinition, AgentInstance, PrototypeRef
from agent_factory.domain.services.lifecycle import LifecyclePolicy

INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000801")
EXPECTED_ALLOWED = {
    InstanceStatus.CREATED: {InstanceStatus.RUNNING, InstanceStatus.TERMINATED},
    InstanceStatus.RUNNING: {
        InstanceStatus.WAITING,
        InstanceStatus.COMPLETED,
        InstanceStatus.FAILED,
        InstanceStatus.TERMINATED,
    },
    InstanceStatus.WAITING: {
        InstanceStatus.RUNNING,
        InstanceStatus.FAILED,
        InstanceStatus.TERMINATED,
    },
    InstanceStatus.FAILED: {InstanceStatus.RUNNING, InstanceStatus.TERMINATED},
    InstanceStatus.DEGRADED: {InstanceStatus.RUNNING, InstanceStatus.TERMINATED},
    InstanceStatus.COMPLETED: set(),
    InstanceStatus.TERMINATED: set(),
}


def _instance(status: InstanceStatus, now: datetime) -> AgentInstance:
    return AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum="a" * 64,
        ),
        revision=4,
        status=status,
        configuration=AgentDefinition(
            agent_type="writer-agent",
            role="Writer",
            system_prompt="Write a deterministic response.",
        ),
        created_at=now,
        updated_at=now,
        created_by="owner",
    )


@pytest.mark.parametrize("source", list(InstanceStatus))
@pytest.mark.parametrize("target", list(InstanceStatus))
def test_lifecycle_policy_enforces_complete_transition_matrix(
    source: InstanceStatus,
    target: InstanceStatus,
    fixed_now: datetime,
) -> None:
    instance = _instance(source, fixed_now)
    retry = source is InstanceStatus.FAILED and target is InstanceStatus.RUNNING
    transition_at = fixed_now + timedelta(minutes=1)

    if target in EXPECTED_ALLOWED[source]:
        updated = LifecyclePolicy().transition(
            instance,
            target,
            reason="state matrix test",
            retry=retry,
            now=transition_at,
        )

        assert updated.revision == 5
        assert updated.status is target
        assert updated.updated_at == transition_at
        assert updated.configuration == instance.configuration
        assert instance.revision == 4
        assert instance.status is source
    else:
        with pytest.raises(InvalidStateTransitionError):
            LifecyclePolicy().transition(
                instance,
                target,
                reason="state matrix test",
                retry=retry,
                now=transition_at,
            )


def test_failed_to_running_requires_explicit_retry(fixed_now: datetime) -> None:
    with pytest.raises(InvalidStateTransitionError) as captured:
        LifecyclePolicy().transition(
            _instance(InstanceStatus.FAILED, fixed_now),
            InstanceStatus.RUNNING,
            reason="retry failed task",
            retry=False,
            now=fixed_now,
        )

    assert captured.value.details["reason"] == "retry-required"


def test_retry_flag_is_rejected_for_other_transitions(fixed_now: datetime) -> None:
    with pytest.raises(InvalidStateTransitionError) as captured:
        LifecyclePolicy().transition(
            _instance(InstanceStatus.CREATED, fixed_now),
            InstanceStatus.RUNNING,
            reason="start task",
            retry=True,
            now=fixed_now,
        )

    assert captured.value.details["reason"] == "unexpected-retry-flag"


@pytest.mark.parametrize("reason", ["", "   ", "x" * 1_001])
def test_lifecycle_policy_rejects_invalid_reason(
    reason: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(InvalidStateTransitionError) as captured:
        LifecyclePolicy().transition(
            _instance(InstanceStatus.CREATED, fixed_now),
            InstanceStatus.RUNNING,
            reason=reason,
            retry=False,
            now=fixed_now,
        )

    assert captured.value.details["reason"] == "invalid-reason"


def test_degraded_target_is_reserved_for_degradation_policy(
    fixed_now: datetime,
) -> None:
    with pytest.raises(InvalidStateTransitionError) as captured:
        LifecyclePolicy().transition(
            _instance(InstanceStatus.RUNNING, fixed_now),
            InstanceStatus.DEGRADED,
            reason="manual degradation",
            retry=False,
            now=fixed_now,
        )

    assert captured.value.details["reason"] == "degraded-status-is-policy-owned"
