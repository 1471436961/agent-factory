"""Process liveness and dependency readiness routes."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from agent_factory.container import Container
from agent_factory.interfaces.api.contracts import HealthResponse
from agent_factory.interfaces.api.dependencies import get_container
from agent_factory.interfaces.api.errors import ApiContractError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"description": "Not ready"}},
)
async def readiness(
    container: Annotated[Container, Depends(get_container)],
) -> HealthResponse:
    if not container.ready or not await container.migration_runner.ping():
        raise ApiContractError(
            code="SERVICE_NOT_READY",
            message="Service is not ready",
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return HealthResponse(status="ok")
