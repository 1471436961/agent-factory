"""FastAPI application factory for the M1 REST contract."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_factory import __version__
from agent_factory.container import build_container
from agent_factory.interfaces.api.errors import install_exception_handlers
from agent_factory.interfaces.api.middleware import RequestContextMiddleware
from agent_factory.interfaces.api.routers import api_router
from agent_factory.interfaces.api.routers.health import router as health_router
from agent_factory.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an app whose lifespan owns migration and process readiness."""

    resolved_settings = settings or Settings()
    container = build_container(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    application.state.container = container
    install_exception_handlers(application)
    application.add_middleware(
        RequestContextMiddleware,
        correlation_context=container.correlation_context,
        id_generator=container.id_generator,
        max_request_bytes=resolved_settings.max_request_bytes,
    )
    application.include_router(health_router)
    application.include_router(api_router, prefix=resolved_settings.api_prefix)

    return application


app = create_app()
