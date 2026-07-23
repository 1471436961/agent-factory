"""Deterministic offline RuntimeAdapter for the fixed Writer demonstration."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.application.ports import Clock, IdGenerator
from agent_factory.application.runtime import (
    RunRequest,
    RunResult,
    RuntimeRunStatus,
)
from agent_factory.application.tool_contracts import (
    ToolCallRequest,
    ToolExecutionContext,
)
from agent_factory.application.tool_execution import ToolExecutor
from agent_factory.domain.common import FrozenJsonObject
from agent_factory.domain.errors import FactoryError
from agent_factory.infrastructure.runtime.registry import DocumentSearchOutput

logger = logging.getLogger("agent_factory.demo_runtime")

RUNTIME_NAME = "demo-runtime"


class OfflineDemoRuntimeAdapter:
    """Produce reproducible Writer output without a model or network access."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._tool_executor = tool_executor
        self._clock = clock
        self._id_generator = id_generator

    async def run(self, request: RunRequest) -> RunResult:
        started_at = self._clock.now()
        tool_call_ids = []
        context = ToolExecutionContext(
            spec=request.spec,
            knowledge=request.knowledge,
            actor=RUNTIME_NAME,
            correlation_id=request.task_id,
        )

        target_error = self._runtime_target_error(request)
        if target_error is not None:
            return self._failed(
                request,
                error_code=target_error,
                tool_call_ids=(),
                started_at=started_at,
            )

        try:
            await self._tool_executor.verify_context(context)
            evidence = ""
            granted = next(
                (tool for tool in request.spec.tools if tool.name == "document-search"),
                None,
            )
            if granted is not None:
                call_id = self._id_generator.new()
                tool_call_ids.append(call_id)
                execution = await self._tool_executor.execute(
                    ToolCallRequest(
                        call_id=call_id,
                        task_id=request.task_id,
                        instance_id=request.spec.instance_id,
                        instance_revision=request.spec.revision,
                        agent_spec_checksum=request.spec.spec_checksum,
                        tool_name=granted.name,
                        tool_version=granted.version,
                        arguments={"query": request.input, "top_k": 5},
                    ),
                    context,
                )
                search = DocumentSearchOutput.model_validate(execution.output)
                evidence = "\n\n".join(hit.content for hit in search.results)

            body = self._body(request.input, evidence)
            structured_output = {
                "title": "Agent Factory Demo",
                "body": body,
            }
            schema = FrozenJsonObject(request.spec.output_schema).to_builtin()
            errors = tuple(Draft202012Validator(schema).iter_errors(structured_output))
            if errors:
                return self._failed(
                    request,
                    error_code="RUNTIME_OUTPUT_VALIDATION_FAILED",
                    tool_call_ids=tuple(tool_call_ids),
                    started_at=started_at,
                )
            return RunResult(
                task_id=request.task_id,
                instance_id=request.spec.instance_id,
                instance_revision=request.spec.revision,
                agent_spec_checksum=request.spec.spec_checksum,
                status=RuntimeRunStatus.COMPLETED,
                content=body,
                structured_output=structured_output,
                tool_call_ids=tuple(tool_call_ids),
                runtime_name=RUNTIME_NAME,
                started_at=started_at,
                completed_at=self._clock.now(),
            )
        except FactoryError as exc:
            return self._failed(
                request,
                error_code=exc.code,
                tool_call_ids=tuple(tool_call_ids),
                started_at=started_at,
            )
        except Exception as exc:
            logger.error(
                "demo_runtime_failed",
                extra={
                    "task_id": str(request.task_id),
                    "exception_type": type(exc).__name__,
                },
            )
            return self._failed(
                request,
                error_code="RUNTIME_EXECUTION_FAILED",
                tool_call_ids=tuple(tool_call_ids),
                started_at=started_at,
            )

    @staticmethod
    def _runtime_target_error(request: RunRequest) -> str | None:
        if request.spec.runtime_target not in {None, RUNTIME_NAME}:
            return "RUNTIME_TARGET_MISMATCH"
        if (
            request.context_ref is not None
            and request.context_ref.runtime_name != RUNTIME_NAME
        ):
            return "RUNTIME_CONTEXT_MISMATCH"
        return None

    @staticmethod
    def _body(task_input: str, evidence: str) -> str:
        if evidence:
            return f"Task: {task_input}\n\nVerified knowledge:\n{evidence}"
        return f"Task: {task_input}"

    def _failed(
        self,
        request: RunRequest,
        *,
        error_code: str,
        tool_call_ids: tuple[UUID, ...],
        started_at: datetime,
    ) -> RunResult:
        return RunResult(
            task_id=request.task_id,
            instance_id=request.spec.instance_id,
            instance_revision=request.spec.revision,
            agent_spec_checksum=request.spec.spec_checksum,
            status=RuntimeRunStatus.FAILED,
            tool_call_ids=tool_call_ids,
            runtime_name=RUNTIME_NAME,
            error_code=error_code,
            started_at=started_at,
            completed_at=self._clock.now(),
        )
