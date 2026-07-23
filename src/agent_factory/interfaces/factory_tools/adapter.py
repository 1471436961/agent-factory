"""Authorization-preserving adapter from Factory Tools to application services."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from pydantic import ValidationError

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    PromoteAgentCommand,
)
from agent_factory.application.ports import CorrelationContext
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.application.security import (
    AuthorizationPolicy,
    FactoryPermission,
    Principal,
)
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import FrozenModel
from agent_factory.domain.errors import FactoryError
from agent_factory.domain.models import AgentInstance, AgentPrototype
from agent_factory.interfaces.factory_tools.contracts import (
    ApplyPromotionToolInput,
    BindKnowledgeToolInput,
    CloneAgentToolInput,
    FactoryToolCallContext,
    FactoryToolDefinition,
    FactoryToolError,
    FactoryToolResult,
    ListPrototypesToolInput,
    QueryAuditLogToolInput,
)

logger = logging.getLogger("agent_factory.factory_tools")


class _FactoryOperations(Protocol):
    async def list_prototypes(
        self,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]: ...

    async def clone_agent(self, command: CloneAgentCommand) -> AgentInstance: ...

    async def bind_knowledge(
        self,
        command: BindKnowledgeCommand,
    ) -> AgentInstance: ...

    async def promote_agent(
        self,
        command: PromoteAgentCommand,
    ) -> AgentInstance: ...

    async def query_audit(self, query: AuditQuery) -> Page[AuditEvent]: ...


_ToolHandler = Callable[
    [FrozenModel, FactoryToolCallContext],
    Awaitable[FrozenModel],
]


@dataclass(frozen=True, slots=True)
class _ToolRegistration:
    definition: FactoryToolDefinition
    input_model: type[FrozenModel]
    output_model: type[FrozenModel]
    handler: _ToolHandler


class FactoryToolAdapter:
    """Expose selected Controller operations through one strict tool pipeline."""

    def __init__(
        self,
        *,
        controller: _FactoryOperations,
        authorization_policy: AuthorizationPolicy,
        correlation_context: CorrelationContext,
    ) -> None:
        self._controller = controller
        self._authorization_policy = authorization_policy
        self._correlation_context = correlation_context
        registrations = (
            self._registration(
                name="list_prototypes",
                description="List registered Agent prototypes using stable filters.",
                input_model=ListPrototypesToolInput,
                output_model=Page[AgentPrototype],
                permission=FactoryPermission.FACTORY_READ,
                handler=self._list_prototypes,
            ),
            self._registration(
                name="clone_agent",
                description="Clone a published Agent prototype into a new instance.",
                input_model=CloneAgentToolInput,
                output_model=AgentInstance,
                permission=FactoryPermission.FACTORY_WRITE,
                handler=self._clone_agent,
            ),
            self._registration(
                name="bind_knowledge",
                description="Bind validated knowledge packages to an Agent instance.",
                input_model=BindKnowledgeToolInput,
                output_model=AgentInstance,
                permission=FactoryPermission.FACTORY_WRITE,
                handler=self._bind_knowledge,
            ),
            self._registration(
                name="apply_promotion",
                description="Apply an evidence-backed skill promotion to an Agent.",
                input_model=ApplyPromotionToolInput,
                output_model=AgentInstance,
                permission=FactoryPermission.FACTORY_WRITE,
                handler=self._apply_promotion,
            ),
            self._registration(
                name="query_audit_log",
                description="Query immutable Agent Factory audit events.",
                input_model=QueryAuditLogToolInput,
                output_model=Page[AuditEvent],
                permission=FactoryPermission.AUDIT_READ,
                handler=self._query_audit_log,
            ),
        )
        self._registrations: Mapping[str, _ToolRegistration] = MappingProxyType(
            {item.definition.name: item for item in registrations}
        )

    def definitions(
        self,
        principal: Principal,
    ) -> tuple[FactoryToolDefinition, ...]:
        """Return only definitions the authenticated principal may invoke."""

        return tuple(
            registration.definition
            for name, registration in sorted(self._registrations.items())
            if self._authorization_policy.allows(
                principal,
                registration.definition.required_permission,
            )
        )

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: FactoryToolCallContext,
    ) -> FactoryToolResult:
        """Validate and execute one factory operation without leaking raw input."""

        registration = self._registrations.get(tool_name)
        if registration is None:
            return self._failure(
                context,
                code="FACTORY_TOOL_NOT_FOUND",
                message="Factory tool was not found",
            )

        try:
            self._authorization_policy.require(
                context.principal,
                registration.definition.required_permission,
            )
        except FactoryError as exc:
            return self._factory_failure(context, exc)

        try:
            validated_input = registration.input_model.model_validate(arguments)
        except ValidationError as exc:
            return self._failure(
                context,
                code="TOOL_INPUT_VALIDATION_FAILED",
                message="Factory tool input validation failed",
                details={"errors": self._safe_validation_errors(exc)},
            )

        token = self._correlation_context.set(str(context.correlation_id))
        try:
            try:
                output = await registration.handler(validated_input, context)
            except FactoryError as exc:
                return self._factory_failure(context, exc)
            except Exception as exc:
                logger.error(
                    "factory_tool_unhandled_error",
                    extra={
                        "tool_name": tool_name,
                        "request_id": str(context.request_id),
                        "correlation_id": str(context.correlation_id),
                        "exception_type": type(exc).__name__,
                    },
                )
                return self._failure(
                    context,
                    code="INTERNAL_ERROR",
                    message="Internal factory tool error",
                )
        finally:
            self._correlation_context.reset(token)

        try:
            validated_output = registration.output_model.model_validate(output)
        except ValidationError:
            logger.error(
                "factory_tool_output_validation_failed",
                extra={
                    "tool_name": tool_name,
                    "request_id": str(context.request_id),
                    "correlation_id": str(context.correlation_id),
                },
            )
            return self._failure(
                context,
                code="TOOL_OUTPUT_VALIDATION_FAILED",
                message="Factory tool output validation failed",
            )

        return FactoryToolResult(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            ok=True,
            output=validated_output.model_dump(mode="json"),
        )

    def _registration(
        self,
        *,
        name: str,
        description: str,
        input_model: type[FrozenModel],
        output_model: type[FrozenModel],
        permission: FactoryPermission,
        handler: _ToolHandler,
    ) -> _ToolRegistration:
        return _ToolRegistration(
            definition=FactoryToolDefinition(
                name=name,
                description=description,
                input_schema=input_model.model_json_schema(mode="validation"),
                output_schema=output_model.model_json_schema(mode="validation"),
                required_permission=permission,
            ),
            input_model=input_model,
            output_model=output_model,
            handler=handler,
        )

    async def _list_prototypes(
        self,
        model: FrozenModel,
        context: FactoryToolCallContext,
    ) -> FrozenModel:
        del context
        tool_input = cast(ListPrototypesToolInput, model)
        query = PrototypeListQuery.model_validate(tool_input.model_dump())
        return await self._controller.list_prototypes(query)

    async def _clone_agent(
        self,
        model: FrozenModel,
        context: FactoryToolCallContext,
    ) -> FrozenModel:
        tool_input = cast(CloneAgentToolInput, model)
        return await self._controller.clone_agent(
            CloneAgentCommand(
                prototype_id=tool_input.prototype_id,
                prototype_version=tool_input.version,
                runtime_target=tool_input.runtime_target,
                actor=context.principal.subject,
                idempotency_key=self._idempotency_key("clone_agent", context),
            )
        )

    async def _bind_knowledge(
        self,
        model: FrozenModel,
        context: FactoryToolCallContext,
    ) -> FrozenModel:
        tool_input = cast(BindKnowledgeToolInput, model)
        return await self._controller.bind_knowledge(
            BindKnowledgeCommand(
                instance_id=tool_input.instance_id,
                expected_revision=tool_input.expected_revision,
                selections=tool_input.selections,
                replace_existing=tool_input.replace_existing,
                actor=context.principal.subject,
                idempotency_key=self._idempotency_key("bind_knowledge", context),
            )
        )

    async def _apply_promotion(
        self,
        model: FrozenModel,
        context: FactoryToolCallContext,
    ) -> FrozenModel:
        tool_input = cast(ApplyPromotionToolInput, model)
        return await self._controller.promote_agent(
            PromoteAgentCommand(
                instance_id=tool_input.instance_id,
                expected_revision=tool_input.expected_revision,
                target_node_id=tool_input.target_node_id,
                evaluation_report_id=tool_input.evaluation_report_id,
                evaluation_review_id=tool_input.evaluation_review_id,
                knowledge_selections=tool_input.knowledge_selections,
                actor=context.principal.subject,
                idempotency_key=self._idempotency_key("apply_promotion", context),
            )
        )

    async def _query_audit_log(
        self,
        model: FrozenModel,
        context: FactoryToolCallContext,
    ) -> FrozenModel:
        del context
        tool_input = cast(QueryAuditLogToolInput, model)
        query = AuditQuery.model_validate(tool_input.model_dump())
        return await self._controller.query_audit(query)

    @staticmethod
    def _idempotency_key(
        tool_name: str,
        context: FactoryToolCallContext,
    ) -> str:
        return context.idempotency_key or f"tool:{tool_name}:{context.request_id}"

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

    @staticmethod
    def _factory_failure(
        context: FactoryToolCallContext,
        exc: FactoryError,
    ) -> FactoryToolResult:
        return FactoryToolAdapter._failure(
            context,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @staticmethod
    def _failure(
        context: FactoryToolCallContext,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> FactoryToolResult:
        return FactoryToolResult(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            ok=False,
            error=FactoryToolError(
                code=code,
                message=message,
                details=details or {},
            ),
        )
