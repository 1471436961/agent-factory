"""Runtime boundary model tests."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.runtime import (
    ResolvedRuntimeKnowledge,
    RunRequest,
    RunResult,
    RuntimeContextRef,
    RuntimeRunStatus,
)
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.models import AgentSpec, KnowledgeRef, PrototypeRef

TASK_ID = UUID("00000000-0000-0000-0000-000000000811")
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000812")
TOOL_CALL_ID = UUID("00000000-0000-0000-0000-000000000813")
CONTENT = "# Product documentation"
CONTENT_CHECKSUM = checksum_knowledge_content(CONTENT)
SPEC_CHECKSUM = "b" * 64


def _spec(now: datetime) -> AgentSpec:
    return AgentSpec(
        instance_id=INSTANCE_ID,
        revision=3,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum="a" * 64,
        ),
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Use the supplied product documentation.",
        tools=(),
        knowledge=(
            KnowledgeRef(
                slot_name="product-docs",
                knowledge_id="agent-factory-docs",
                version="1.0.0",
                checksum=CONTENT_CHECKSUM,
                injection_mode=InjectionMode.INLINE,
            ),
        ),
        output_schema={"type": "object"},
        generated_at=now,
        spec_checksum=SPEC_CHECKSUM,
    )


def _knowledge() -> ResolvedRuntimeKnowledge:
    return ResolvedRuntimeKnowledge(
        slot_name="product-docs",
        knowledge_id="agent-factory-docs",
        version="1.0.0",
        checksum=CONTENT_CHECKSUM,
        injection_mode=InjectionMode.INLINE,
        mime_type="text/markdown",
        content=CONTENT,
    )


def _context(now: datetime) -> RuntimeContextRef:
    return RuntimeContextRef(
        instance_id=INSTANCE_ID,
        instance_revision=3,
        agent_spec_checksum=SPEC_CHECKSUM,
        runtime_name="demo-runtime",
        knowledge_namespaces=("product-docs",),
        created_at=now,
    )


def test_run_request_accepts_exact_spec_sources(fixed_now: datetime) -> None:
    request = RunRequest(
        task_id=TASK_ID,
        spec=_spec(fixed_now),
        input="Write a concise overview.",
        knowledge=(_knowledge(),),
        context_ref=_context(fixed_now),
        metadata={"audience": "engineer"},
    )

    assert request.context_ref is not None
    assert request.context_ref.agent_spec_checksum == request.spec.spec_checksum
    assert request.knowledge[0].content == CONTENT
    with pytest.raises(TypeError):
        request.metadata["audience"] = "manager"  # type: ignore[index]


def test_resolved_knowledge_rejects_content_checksum_mismatch() -> None:
    with pytest.raises(ValidationError, match="content checksum"):
        ResolvedRuntimeKnowledge(
            slot_name="product-docs",
            knowledge_id="agent-factory-docs",
            version="1.0.0",
            checksum="c" * 64,
            injection_mode=InjectionMode.INLINE,
            mime_type="text/plain",
            content=CONTENT,
        )


def test_run_request_rejects_missing_or_duplicate_knowledge(
    fixed_now: datetime,
) -> None:
    with pytest.raises(ValidationError, match="does not match AgentSpec"):
        RunRequest(
            task_id=TASK_ID,
            spec=_spec(fixed_now),
            input="Write.",
        )

    knowledge = _knowledge()
    with pytest.raises(ValidationError, match="duplicate references"):
        RunRequest(
            task_id=TASK_ID,
            spec=_spec(fixed_now),
            input="Write.",
            knowledge=(knowledge, knowledge),
        )


def test_run_request_rejects_context_from_another_revision(
    fixed_now: datetime,
) -> None:
    context = _context(fixed_now).model_copy(update={"instance_revision": 2})

    with pytest.raises(ValidationError, match="context does not match AgentSpec"):
        RunRequest(
            task_id=TASK_ID,
            spec=_spec(fixed_now),
            input="Write.",
            knowledge=(_knowledge(),),
            context_ref=context,
        )


def test_runtime_context_rejects_duplicate_namespaces(fixed_now: datetime) -> None:
    with pytest.raises(ValidationError, match="contains duplicates"):
        RuntimeContextRef(
            instance_id=INSTANCE_ID,
            instance_revision=3,
            agent_spec_checksum=SPEC_CHECKSUM,
            runtime_name="demo-runtime",
            knowledge_namespaces=("product-docs", "product-docs"),
            created_at=fixed_now,
        )


def test_run_result_enforces_terminal_outcome_contract(fixed_now: datetime) -> None:
    completed = RunResult(
        task_id=TASK_ID,
        instance_id=INSTANCE_ID,
        instance_revision=3,
        agent_spec_checksum=SPEC_CHECKSUM,
        status=RuntimeRunStatus.COMPLETED,
        content="Done.",
        tool_call_ids=(TOOL_CALL_ID,),
        runtime_name="demo-runtime",
        started_at=fixed_now,
        completed_at=fixed_now + timedelta(seconds=1),
    )
    failed = RunResult(
        task_id=TASK_ID,
        instance_id=INSTANCE_ID,
        instance_revision=3,
        agent_spec_checksum=SPEC_CHECKSUM,
        status=RuntimeRunStatus.FAILED,
        runtime_name="demo-runtime",
        error_code="RUNTIME_FAILED",
        started_at=fixed_now,
        completed_at=fixed_now,
    )

    assert completed.error_code is None
    assert failed.error_code == "RUNTIME_FAILED"


@pytest.mark.parametrize(
    "updates, message",
    [
        (
            {"status": RuntimeRunStatus.FAILED, "error_code": None},
            "requires error_code",
        ),
        (
            {"status": RuntimeRunStatus.COMPLETED, "error_code": "UNEXPECTED"},
            "cannot contain error_code",
        ),
    ],
)
def test_run_result_rejects_inconsistent_error_state(
    updates: dict[str, object],
    message: str,
    fixed_now: datetime,
) -> None:
    payload: dict[str, object] = {
        "task_id": TASK_ID,
        "instance_id": INSTANCE_ID,
        "instance_revision": 3,
        "agent_spec_checksum": SPEC_CHECKSUM,
        "status": RuntimeRunStatus.COMPLETED,
        "runtime_name": "demo-runtime",
        "started_at": fixed_now,
        "completed_at": fixed_now,
        **updates,
    }

    with pytest.raises(ValidationError, match=message):
        RunResult.model_validate(payload)


def test_run_result_rejects_time_and_duplicate_tool_calls(
    fixed_now: datetime,
) -> None:
    payload = {
        "task_id": TASK_ID,
        "instance_id": INSTANCE_ID,
        "instance_revision": 3,
        "agent_spec_checksum": SPEC_CHECKSUM,
        "status": RuntimeRunStatus.COMPLETED,
        "runtime_name": "demo-runtime",
        "started_at": fixed_now,
        "completed_at": fixed_now - timedelta(seconds=1),
    }
    with pytest.raises(ValidationError, match="completed_at"):
        RunResult.model_validate(payload)

    payload["completed_at"] = fixed_now
    payload["tool_call_ids"] = (TOOL_CALL_ID, TOOL_CALL_ID)
    with pytest.raises(ValidationError, match="contains duplicates"):
        RunResult.model_validate(payload)
