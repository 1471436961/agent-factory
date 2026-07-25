"""FastAPI dependency adapters for security, commands, and queries."""

from http import HTTPStatus
from typing import Annotated, TypeVar

from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError

from agent_factory.application.controller import FactoryController
from agent_factory.application.security import (
    Authenticator,
    AuthorizationPolicy,
    FactoryPermission,
    Principal,
)
from agent_factory.container import Container
from agent_factory.interfaces.api.errors import ApiContractError, correlation_id_for
from agent_factory.interfaces.api.security_events import log_authentication_rejected

CommandT = TypeVar("CommandT", bound=BaseModel)
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Alpha static bearer credential configured by the server operator.",
)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_controller(
    container: Annotated[Container, Depends(get_container)],
) -> FactoryController:
    return container.controller


def get_authenticator(
    container: Annotated[Container, Depends(get_container)],
) -> Authenticator:
    return container.authenticator


def get_authorization_policy(
    container: Annotated[Container, Depends(get_container)],
) -> AuthorizationPolicy:
    return container.authorization_policy


def get_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
) -> Principal:
    credential_present = "authorization" in request.headers
    if "x-actor-id" in request.headers:
        log_authentication_rejected(
            correlation_id=correlation_id_for(request),
            category="actor_header_not_allowed",
            credential_present=credential_present,
        )
        raise ApiContractError(
            code="ACTOR_HEADER_NOT_ALLOWED",
            message="X-Actor-ID is not accepted; actor comes from authentication",
            status_code=HTTPStatus.BAD_REQUEST,
        )
    if not authenticator.ready:
        log_authentication_rejected(
            correlation_id=correlation_id_for(request),
            category="authentication_not_configured",
            credential_present=credential_present,
        )
        raise ApiContractError(
            code="AUTHENTICATION_NOT_CONFIGURED",
            message="Authentication is not configured",
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    if credentials is None:
        log_authentication_rejected(
            correlation_id=correlation_id_for(request),
            category="authentication_required",
            credential_present=credential_present,
        )
        raise ApiContractError(
            code="AUTHENTICATION_REQUIRED",
            message="Bearer authentication is required",
            status_code=HTTPStatus.UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = authenticator.authenticate(credentials.credentials)
    if principal is None:
        log_authentication_rejected(
            correlation_id=correlation_id_for(request),
            category="authentication_failed",
            credential_present=True,
        )
        raise ApiContractError(
            code="AUTHENTICATION_FAILED",
            message="Bearer credential is invalid",
            status_code=HTTPStatus.UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_factory_read(
    principal: Annotated[Principal, Depends(get_principal)],
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> Principal:
    policy.require(principal, FactoryPermission.FACTORY_READ)
    return principal


def require_factory_write(
    principal: Annotated[Principal, Depends(get_principal)],
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> Principal:
    policy.require(principal, FactoryPermission.FACTORY_WRITE)
    return principal


def require_audit_read(
    principal: Annotated[Principal, Depends(get_principal)],
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> Principal:
    policy.require(principal, FactoryPermission.AUDIT_READ)
    return principal


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
FactoryReadPrincipalDep = Annotated[Principal, Depends(require_factory_read)]
FactoryWritePrincipalDep = Annotated[Principal, Depends(require_factory_write)]
AuditReadPrincipalDep = Annotated[Principal, Depends(require_audit_read)]
IdempotencyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
