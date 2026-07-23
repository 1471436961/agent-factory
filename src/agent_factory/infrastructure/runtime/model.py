"""Bounded RuntimeAdapter that delegates cognition to a model gateway."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from agent_factory.application.model_gateway import (
    ModelGateway,
    ModelInvocation,
    ModelToolDefinition,
    ModelToolResult,
)
from agent_factory.application.ports import Clock, IdGenerator
from agent_factory.application.runtime import RunRequest, RunResult, RuntimeRunStatus
from agent_factory.application.tool_contracts import (
    ToolCallRequest,
    ToolExecutionContext,
)
from agent_factory.application.tool_execution import ToolExecutor
from agent_factory.domain.common import FrozenJsonObject, Slug
from agent_factory.domain.errors import (
    FactoryError,
    ModelProtocolError,
    ModelTurnLimitError,
)

logger = logging.getLogger("agent_factory.model_runtime")
_RUNTIME_NAME_ADAPTER = TypeAdapter(Slug)


class ModelRuntimeAdapter:
    """Run a model/tool loop while factory code remains the policy authority."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        tool_executor: ToolExecutor,
        clock: Clock,
        id_generator: IdGenerator,
        runtime_name: Slug = "model-runtime",
        max_turns: int = 4,
    ) -> None:
        if not 1 <= max_turns <= 8:
            raise ValueError("max_turns must be between 1 and 8")
        self._gateway = gateway
        self._tool_executor = tool_executor
        self._clock = clock
        self._id_generator = id_generator
        self._runtime_name = _RUNTIME_NAME_ADAPTER.validate_python(runtime_name)
        self._max_turns = max_turns

    async def run(self, request: RunRequest) -> RunResult:
        started_at = self._clock.now()
        tool_call_ids: list[UUID] = []
        prompt_tokens = 0
        completion_tokens = 0
        model_name: str | None = None

        target_error = self._runtime_target_error(request)
        if target_error is not None:
            return self._failed(
                request,
                error_code=target_error,
                tool_call_ids=(),
                model_name=None,
                prompt_tokens=0,
                completion_tokens=0,
                started_at=started_at,
            )

        context = ToolExecutionContext(
            spec=request.spec,
            knowledge=request.knowledge,
            actor=self._runtime_name,
            correlation_id=request.task_id,
        )

        try:
            await self._tool_executor.verify_context(context)
            session = self._gateway.start(self._invocation(request))
            tool_results: tuple[ModelToolResult, ...] = ()
            provider_call_ids: set[str] = set()

            for turn_number in range(1, self._max_turns + 1):
                turn = await session.next(tool_results)
                prompt_tokens += turn.prompt_tokens
                completion_tokens += turn.completion_tokens
                if model_name is None:
                    model_name = turn.model_name
                elif turn.model_name != model_name:
                    raise ModelProtocolError(
                        details={"reason": "model-name-changed-during-run"}
                    )

                if turn.tool_call is None:
                    assert turn.structured_output is not None
                    if not self._output_is_valid(request, turn.structured_output):
                        return self._failed(
                            request,
                            error_code="RUNTIME_OUTPUT_VALIDATION_FAILED",
                            tool_call_ids=tuple(tool_call_ids),
                            model_name=model_name,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            started_at=started_at,
                        )
                    return RunResult(
                        task_id=request.task_id,
                        instance_id=request.spec.instance_id,
                        instance_revision=request.spec.revision,
                        agent_spec_checksum=request.spec.spec_checksum,
                        status=RuntimeRunStatus.COMPLETED,
                        content=turn.content,
                        structured_output=turn.structured_output,
                        tool_call_ids=tuple(tool_call_ids),
                        runtime_name=self._runtime_name,
                        model_name=model_name,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        started_at=started_at,
                        completed_at=self._clock.now(),
                    )

                call = turn.tool_call
                if call.provider_call_id in provider_call_ids:
                    raise ModelProtocolError(
                        details={"reason": "duplicate-provider-call-id"}
                    )
                provider_call_ids.add(call.provider_call_id)
                granted = next(
                    (tool for tool in request.spec.tools if tool.name == call.name),
                    None,
                )
                call_id = self._id_generator.new()
                tool_call_ids.append(call_id)
                execution = await self._tool_executor.execute(
                    ToolCallRequest(
                        call_id=call_id,
                        task_id=request.task_id,
                        instance_id=request.spec.instance_id,
                        instance_revision=request.spec.revision,
                        agent_spec_checksum=request.spec.spec_checksum,
                        tool_name=call.name,
                        tool_version=(
                            granted.version if granted is not None else "0.0.0"
                        ),
                        arguments=call.arguments,
                    ),
                    context,
                )
                tool_results = (
                    ModelToolResult(
                        provider_call_id=call.provider_call_id,
                        output=execution.output,
                    ),
                )
                if turn_number == self._max_turns:
                    raise ModelTurnLimitError(details={"max_turns": self._max_turns})
        except FactoryError as exc:
            return self._failed(
                request,
                error_code=exc.code,
                tool_call_ids=tuple(tool_call_ids),
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                started_at=started_at,
            )
        except Exception as exc:
            logger.error(
                "model_runtime_failed",
                extra={
                    "task_id": str(request.task_id),
                    "exception_type": type(exc).__name__,
                },
            )
            return self._failed(
                request,
                error_code="RUNTIME_EXECUTION_FAILED",
                tool_call_ids=tuple(tool_call_ids),
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                started_at=started_at,
            )

        raise AssertionError("bounded model loop must return a terminal result")

    def _invocation(self, request: RunRequest) -> ModelInvocation:
        return ModelInvocation(
            instructions=request.spec.system_prompt,
            task_input=request.input,
            tools=tuple(
                ModelToolDefinition(
                    name=tool.name,
                    version=tool.version,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
                for tool in request.spec.tools
            ),
            output_schema=request.spec.output_schema,
        )

    def _runtime_target_error(self, request: RunRequest) -> str | None:
        if request.spec.runtime_target not in {None, self._runtime_name}:
            return "RUNTIME_TARGET_MISMATCH"
        if (
            request.context_ref is not None
            and request.context_ref.runtime_name != self._runtime_name
        ):
            return "RUNTIME_CONTEXT_MISMATCH"
        return None

    @staticmethod
    def _output_is_valid(
        request: RunRequest,
        output: Mapping[str, object],
    ) -> bool:
        schema = FrozenJsonObject(request.spec.output_schema).to_builtin()
        instance = FrozenJsonObject(output).to_builtin()
        return not any(Draft202012Validator(schema).iter_errors(instance))

    def _failed(
        self,
        request: RunRequest,
        *,
        error_code: str,
        tool_call_ids: tuple[UUID, ...],
        model_name: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        started_at: datetime,
    ) -> RunResult:
        return RunResult(
            task_id=request.task_id,
            instance_id=request.spec.instance_id,
            instance_revision=request.spec.revision,
            agent_spec_checksum=request.spec.spec_checksum,
            status=RuntimeRunStatus.FAILED,
            tool_call_ids=tool_call_ids,
            runtime_name=self._runtime_name,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error_code=error_code,
            started_at=started_at,
            completed_at=self._clock.now(),
        )
