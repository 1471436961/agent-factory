"""Prototype lifecycle and clone routes."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query

from agent_factory.application.commands import (
    CloneAgentCommand,
    DeprecatePrototypeCommand,
    PublishPrototypeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.application.queries import Page, PrototypeListQuery
from agent_factory.domain.common import SemVer, Slug
from agent_factory.domain.enums import PrototypeStatus
from agent_factory.domain.models import AgentInstance, AgentPrototype
from agent_factory.interfaces.api.contracts import (
    CloneAgentRequest,
    DeprecatePrototypeRequest,
    RegisterPrototypeRequest,
)
from agent_factory.interfaces.api.dependencies import (
    ControllerDep,
    FactoryReadPrincipalDep,
    FactoryWritePrincipalDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(prefix="/prototypes", tags=["prototypes"])


@router.post(
    "",
    response_model=AgentPrototype,
    status_code=HTTPStatus.CREATED,
)
async def register_prototype(
    body: RegisterPrototypeRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentPrototype:
    command = validate_command(
        RegisterPrototypeCommand,
        {
            **body.model_dump(mode="python"),
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.register_prototype(command)


@router.get("", response_model=Page[AgentPrototype])
async def list_prototypes(
    controller: ControllerDep,
    _principal: FactoryReadPrincipalDep,
    status_filter: Annotated[
        PrototypeStatus | None,
        Query(alias="status"),
    ] = None,
    agent_type: Slug | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AgentPrototype]:
    query = PrototypeListQuery(
        status=status_filter,
        agent_type=agent_type,
        page=page,
        page_size=page_size,
    )
    return await controller.list_prototypes(query)


@router.post(
    "/{prototype_id}/versions/{version}/publish",
    response_model=AgentPrototype,
)
async def publish_prototype(
    prototype_id: Slug,
    version: SemVer,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentPrototype:
    command = validate_command(
        PublishPrototypeCommand,
        {
            "prototype_id": prototype_id,
            "version": version,
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.publish_prototype(command)


@router.post(
    "/{prototype_id}/versions/{version}/deprecate",
    response_model=AgentPrototype,
)
async def deprecate_prototype(
    prototype_id: Slug,
    version: SemVer,
    body: DeprecatePrototypeRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentPrototype:
    command = validate_command(
        DeprecatePrototypeCommand,
        {
            "prototype_id": prototype_id,
            "version": version,
            "reason": body.reason,
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.deprecate_prototype(command)


@router.post(
    "/{prototype_id}/versions/{version}/instances",
    response_model=AgentInstance,
    status_code=HTTPStatus.CREATED,
)
async def clone_agent(
    prototype_id: Slug,
    version: SemVer,
    body: CloneAgentRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentInstance:
    command = validate_command(
        CloneAgentCommand,
        {
            "prototype_id": prototype_id,
            "prototype_version": version,
            "runtime_target": body.runtime_target,
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.clone_agent(command)
