"""Domain knowledge registration route."""

from http import HTTPStatus

from fastapi import APIRouter

from agent_factory.application.commands import RegisterKnowledgeCommand
from agent_factory.domain.models import DomainKnowledge, DomainKnowledgeDraft
from agent_factory.interfaces.api.contracts import RegisterKnowledgeRequest
from agent_factory.interfaces.api.dependencies import (
    ActorDep,
    ControllerDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post(
    "",
    response_model=DomainKnowledge,
    status_code=HTTPStatus.CREATED,
)
async def register_knowledge(
    body: RegisterKnowledgeRequest,
    controller: ControllerDep,
    actor: ActorDep,
    idempotency_key: IdempotencyHeader = None,
) -> DomainKnowledge:
    command = validate_command(
        RegisterKnowledgeCommand,
        {
            "knowledge": DomainKnowledgeDraft.model_validate(
                body.model_dump(mode="python")
            ),
            "actor": actor,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.register_knowledge(command)
