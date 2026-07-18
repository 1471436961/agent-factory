"""FastAPI application factory and M0 health endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from agent_factory import __version__
from agent_factory.container import Container, build_container
from agent_factory.settings import Settings


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an app whose lifespan owns migration and process readiness."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or Settings()
        container = build_container(resolved_settings)
        app.state.container = container
        await container.start()
        try:
            yield
        finally:
            await container.close()

    application = FastAPI(
        title="Agent Factory",
        version=__version__,
        lifespan=lifespan,
    )

    @application.get(
        "/health/live",
        response_model=HealthResponse,
        tags=["health"],
    )
    async def liveness() -> HealthResponse:
        return HealthResponse()

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
        tags=["health"],
    )
    async def readiness(request: Request) -> HealthResponse:
        container = _get_container(request)
        if not container.ready or not await container.migration_runner.ping():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service is not ready",
            )
        return HealthResponse()

    return application


def _get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


app = create_app()
