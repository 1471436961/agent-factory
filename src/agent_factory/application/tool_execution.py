"""Application service for authorized, bounded, and audited tool execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from datetime import datetime

from pydantic import ValidationError

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.ports import Clock, MonotonicClock
from agent_factory.application.tool_contracts import (
    RegisteredTool,
    ToolCallRecord,
    ToolCallRequest,
    ToolCallStatus,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRegistry,
)
from agent_factory.application.unit_of_work import UnitOfWorkFactory
from agent_factory.domain.common import FrozenModel, canonical_json_bytes, sha256_model
from agent_factory.domain.enums import InstanceStatus
from agent_factory.domain.errors import (
    FactoryError,
    InstanceNotReadyError,
    ToolCallAlreadyExistsError,
    ToolContextMismatchError,
    ToolDefinitionMismatchError,
    ToolExecutionError,
    ToolInputValidationError,
    ToolNotGrantedError,
    ToolOutputValidationError,
    ToolTimeoutError,
    ToolUnavailableError,
    ToolVersionMismatchError,
)

logger = logging.getLogger("agent_factory.tool_execution")


class ToolExecutor:
    """Execute one tool only after proving its immutable authorization chain."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        monotonic_clock: MonotonicClock,
        audit_factory: AuditEventFactory,
    ) -> None:
        self._registry = registry
        self._uow_factory = uow_factory
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._audit_factory = audit_factory

    async def execute(
        self,
        request: ToolCallRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        started_at = self._clock.now()
        started_ns = self._monotonic_clock.now_ns()
        arguments_hash = hashlib.sha256(
            canonical_json_bytes(request.arguments)
        ).hexdigest()
        recordable = False

        try:
            await self._require_persisted_spec(context)
            recordable = True
            await self._require_new_call_id(request)
            await self._require_current_running_instance(context)
            self._require_request_identity(request, context)
            registered = self._resolve_registered_tool(request, context)
            clean_input = self._validate_input(registered, request.arguments)
        except ToolCallAlreadyExistsError:
            raise
        except FactoryError as exc:
            if recordable:
                await self._persist_terminal(
                    request=request,
                    context=context,
                    status=ToolCallStatus.REJECTED,
                    arguments_hash=arguments_hash,
                    result_hash=None,
                    error_code=exc.code,
                    started_at=started_at,
                    started_ns=started_ns,
                )
            raise

        try:
            async with asyncio.timeout(registered.definition.timeout_seconds):
                raw_output = await registered.handler(clean_input, context)
        except TimeoutError as exc:
            timeout_error = ToolTimeoutError(details={"tool_name": request.tool_name})
            await self._persist_terminal(
                request=request,
                context=context,
                status=ToolCallStatus.TIMED_OUT,
                arguments_hash=arguments_hash,
                result_hash=None,
                error_code=timeout_error.code,
                started_at=started_at,
                started_ns=started_ns,
            )
            raise timeout_error from exc
        except FactoryError as exc:
            await self._persist_terminal(
                request=request,
                context=context,
                status=ToolCallStatus.FAILED,
                arguments_hash=arguments_hash,
                result_hash=None,
                error_code=exc.code,
                started_at=started_at,
                started_ns=started_ns,
            )
            raise
        except Exception as exc:
            logger.error(
                "tool_handler_failed",
                extra={
                    "call_id": str(request.call_id),
                    "tool_name": request.tool_name,
                    "exception_type": type(exc).__name__,
                },
            )
            execution_error = ToolExecutionError(
                details={"tool_name": request.tool_name}
            )
            await self._persist_terminal(
                request=request,
                context=context,
                status=ToolCallStatus.FAILED,
                arguments_hash=arguments_hash,
                result_hash=None,
                error_code=execution_error.code,
                started_at=started_at,
                started_ns=started_ns,
            )
            raise execution_error from exc

        try:
            clean_output = registered.output_model.model_validate(raw_output)
        except ValidationError as exc:
            output_error = ToolOutputValidationError(
                details={"errors": self._safe_validation_errors(exc)}
            )
            await self._persist_terminal(
                request=request,
                context=context,
                status=ToolCallStatus.FAILED,
                arguments_hash=arguments_hash,
                result_hash=None,
                error_code=output_error.code,
                started_at=started_at,
                started_ns=started_ns,
            )
            raise output_error from exc

        record = await self._persist_terminal(
            request=request,
            context=context,
            status=ToolCallStatus.SUCCEEDED,
            arguments_hash=arguments_hash,
            result_hash=sha256_model(clean_output),
            error_code=None,
            started_at=started_at,
            started_ns=started_ns,
        )
        return ToolExecutionResult(
            output=clean_output.model_dump(mode="json"),
            record=record,
        )

    async def verify_context(self, context: ToolExecutionContext) -> None:
        """Verify a runtime context even when a run makes no tool call."""

        await self._require_persisted_spec(context)
        await self._require_current_running_instance(context)

    async def _require_persisted_spec(self, context: ToolExecutionContext) -> None:
        spec = context.spec
        async with self._uow_factory(read_only=True) as uow:
            persisted = await uow.specs.get(spec.instance_id, spec.revision)
        if persisted != spec:
            raise ToolContextMismatchError(
                details={
                    "instance_id": str(spec.instance_id),
                    "instance_revision": spec.revision,
                    "reason": "spec-not-persisted-or-mismatched",
                }
            )

    async def _require_current_running_instance(
        self,
        context: ToolExecutionContext,
    ) -> None:
        spec = context.spec
        async with self._uow_factory(read_only=True) as uow:
            instance = await uow.instances.get(spec.instance_id)
        if instance is None or instance.revision != spec.revision:
            raise ToolContextMismatchError(
                details={
                    "instance_id": str(spec.instance_id),
                    "instance_revision": spec.revision,
                    "reason": "instance-revision-is-not-current",
                }
            )
        if instance.status is not InstanceStatus.RUNNING:
            raise InstanceNotReadyError(
                details={
                    "instance_id": str(spec.instance_id),
                    "instance_revision": spec.revision,
                    "status": instance.status.value,
                }
            )

    async def _require_new_call_id(self, request: ToolCallRequest) -> None:
        async with self._uow_factory(read_only=True) as uow:
            existing = await uow.tool_calls.get(request.call_id)
        if existing is not None:
            raise ToolCallAlreadyExistsError(details={"call_id": str(request.call_id)})

    @staticmethod
    def _require_request_identity(
        request: ToolCallRequest,
        context: ToolExecutionContext,
    ) -> None:
        spec = context.spec
        if (
            request.instance_id != spec.instance_id
            or request.instance_revision != spec.revision
            or request.agent_spec_checksum != spec.spec_checksum
        ):
            raise ToolContextMismatchError(
                details={
                    "instance_id": str(spec.instance_id),
                    "instance_revision": spec.revision,
                    "reason": "request-does-not-match-spec",
                }
            )

    def _resolve_registered_tool(
        self,
        request: ToolCallRequest,
        context: ToolExecutionContext,
    ) -> RegisteredTool:
        granted = next(
            (tool for tool in context.spec.tools if tool.name == request.tool_name),
            None,
        )
        if granted is None:
            raise ToolNotGrantedError(details={"tool_name": request.tool_name})
        if request.tool_version != granted.version:
            raise ToolVersionMismatchError(
                details={
                    "tool_name": request.tool_name,
                    "requested_version": request.tool_version,
                    "granted_version": granted.version,
                }
            )
        registered = self._registry.get(request.tool_name, request.tool_version)
        if registered is None or not registered.definition.enabled:
            raise ToolUnavailableError(
                details={
                    "tool_name": request.tool_name,
                    "tool_version": request.tool_version,
                }
            )
        if registered.definition.resolved_spec() != granted:
            raise ToolDefinitionMismatchError(
                details={
                    "tool_name": request.tool_name,
                    "tool_version": request.tool_version,
                }
            )
        return registered

    @staticmethod
    def _validate_input(
        registered: RegisteredTool,
        arguments: Mapping[str, object],
    ) -> FrozenModel:
        try:
            return registered.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInputValidationError(
                details={"errors": ToolExecutor._safe_validation_errors(exc)}
            ) from exc

    async def _persist_terminal(
        self,
        *,
        request: ToolCallRequest,
        context: ToolExecutionContext,
        status: ToolCallStatus,
        arguments_hash: str,
        result_hash: str | None,
        error_code: str | None,
        started_at: datetime,
        started_ns: int,
    ) -> ToolCallRecord:
        completed_at = self._clock.now()
        elapsed_ns = self._monotonic_clock.now_ns() - started_ns
        duration_ms = min(max(0, elapsed_ns // 1_000_000), 600_000)
        record = ToolCallRecord(
            call_id=request.call_id,
            task_id=request.task_id,
            instance_id=context.spec.instance_id,
            instance_revision=context.spec.revision,
            agent_spec_checksum=context.spec.spec_checksum,
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            status=status,
            arguments_hash=arguments_hash,
            result_hash=result_hash,
            error_code=error_code,
            duration_ms=duration_ms,
            actor=context.actor,
            correlation_id=context.correlation_id,
            started_at=started_at,
            completed_at=completed_at,
        )
        async with self._uow_factory() as uow:
            await uow.tool_calls.add(record)
            await uow.audit.append(self._audit_factory.tool_called(record))
            await uow.commit()
        return record

    @staticmethod
    def _safe_validation_errors(exc: ValidationError) -> list[dict[str, object]]:
        return [
            {
                "location": list(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
