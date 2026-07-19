"""Instance knowledge binding and specification export routes."""

from uuid import UUID

from fastapi import APIRouter

from agent_factory.application.commands import BindKnowledgeCommand
from agent_factory.domain.models import AgentInstance, AgentSpec
from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest,
    ExportSpecRequest,
)
from agent_factory.interfaces.api.dependencies import (
    ActorDep,
    ControllerDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(prefix="/instances", tags=["instances"])


@router.post(
    "/{instance_id}/knowledge-bindings",
    response_model=AgentInstance,
)
async def bind_knowledge(
    instance_id: UUID,
    body: BindKnowledgeRequest,
    controller: ControllerDep,
    actor: ActorDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentInstance:
    command = validate_command(
        BindKnowledgeCommand,
        {
            "instance_id": instance_id,
            "expected_revision": body.expected_revision,
            "selections": body.selections,
            "replace_existing": body.replace_existing,
            "actor": actor,
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
    actor: ActorDep,
) -> AgentSpec:
    return await controller.export_spec(
        instance_id,
        revision=body.revision,
        actor=actor,
    )
