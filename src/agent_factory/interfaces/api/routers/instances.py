"""Instance binding, evaluation, promotion, observation, and export routes."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    EvaluateInstanceCommand,
    PromoteAgentCommand,
    RecordTaskOutcomeCommand,
)
from agent_factory.domain.evaluation import EvaluationReport
from agent_factory.domain.models import AgentInstance, AgentSpec
from agent_factory.domain.skills import DegradationCheckResult
from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest,
    EvaluateInstanceRequest,
    ExportSpecRequest,
    PromoteAgentRequest,
    RecordTaskOutcomeRequest,
)
from agent_factory.interfaces.api.dependencies import (
    ControllerDep,
    FactoryWritePrincipalDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(prefix="/instances", tags=["instances"])


@router.post(
    "/{instance_id}/evaluations",
    response_model=EvaluationReport,
    status_code=HTTPStatus.CREATED,
)
async def evaluate_instance(
    instance_id: UUID,
    body: EvaluateInstanceRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> EvaluationReport:
    command = validate_command(
        EvaluateInstanceCommand,
        {
            "submission": {
                "instance_id": instance_id,
                "instance_revision": body.expected_revision,
                "suite": body.suite,
                "runtime_model": body.runtime_model,
                "case_results": body.case_results,
            },
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.evaluate_instance(command)


@router.post(
    "/{instance_id}/promotions",
    response_model=AgentInstance,
)
async def promote_agent(
    instance_id: UUID,
    body: PromoteAgentRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentInstance:
    command = validate_command(
        PromoteAgentCommand,
        {
            "instance_id": instance_id,
            **body.model_dump(mode="python"),
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.promote_agent(command)


@router.post(
    "/{instance_id}/task-outcomes",
    response_model=DegradationCheckResult,
)
async def record_task_outcome(
    instance_id: UUID,
    body: RecordTaskOutcomeRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> DegradationCheckResult:
    command = validate_command(
        RecordTaskOutcomeCommand,
        {
            "instance_id": instance_id,
            **body.model_dump(mode="python"),
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.record_task_outcome(command)


@router.post(
    "/{instance_id}/knowledge-bindings",
    response_model=AgentInstance,
)
async def bind_knowledge(
    instance_id: UUID,
    body: BindKnowledgeRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentInstance:
    command = validate_command(
        BindKnowledgeCommand,
        {
            "instance_id": instance_id,
            "expected_revision": body.expected_revision,
            "selections": body.selections,
            "replace_existing": body.replace_existing,
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.bind_knowledge(command)


@router.post(
    "/{instance_id}/spec-exports",
    response_model=AgentSpec,
)
async def export_spec(
    instance_id: UUID,
    body: ExportSpecRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
) -> AgentSpec:
    return await controller.export_spec(
        instance_id,
        revision=body.revision,
        actor=principal.subject,
    )
