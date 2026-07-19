"""FastAPI dependency adapters for the M1 application service."""

from typing import Annotated, TypeVar

from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from agent_factory.application.controller import FactoryController
from agent_factory.container import Container
from agent_factory.interfaces.api.errors import ApiContractError

CommandT = TypeVar("CommandT", bound=BaseModel)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_controller(
    container: Annotated[Container, Depends(get_container)],
) -> FactoryController:
    return container.controller


def get_actor(
    x_actor_id: Annotated[
        str,
        Header(alias="X-Actor-ID", min_length=1, max_length=128),
    ],
) -> str:
    actor = x_actor_id.strip()
    if not actor:
        raise ApiContractError(
            code="INVALID_ACTOR_ID",
            message="X-Actor-ID must not be blank",
            status_code=422,
        )
    return actor


def validate_command(
    model_type: type[CommandT],
    payload: dict[str, object],
) -> CommandT:
    """Convert adapter data and preserve request-level validation semantics."""

    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


ControllerDep = Annotated[FactoryController, Depends(get_controller)]
ActorDep = Annotated[str, Depends(get_actor)]
IdempotencyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
