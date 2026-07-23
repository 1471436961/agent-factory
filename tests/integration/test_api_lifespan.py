"""FastAPI startup and readiness integration test."""

from pathlib import Path

import httpx
import pytest

from agent_factory.container import Container
from agent_factory.interfaces.api.main import create_app
from agent_factory.settings import Settings

AUTH_TOKEN = "lifespan-token-that-is-at-least-32-characters"


@pytest.mark.asyncio
async def test_lifespan_migrates_before_service_becomes_ready(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database_path = tmp_path / "factory.db"
    settings = Settings.model_validate(
        {
            "database_url": f"sqlite+aiosqlite:///{database_path.as_posix()}",
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
        }
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            liveness = await client.get("/health/live")
            readiness = await client.get("/health/ready")

        assert liveness.json() == {"status": "ok"}
        assert readiness.status_code == 200
        assert readiness.json() == {"status": "ok"}
        container: Container = app.state.container
        assert container.ready is True
        assert database_path.is_file()

    assert container.ready is False


@pytest.mark.asyncio
async def test_lifespan_is_not_ready_without_authentication_configuration(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
        }
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            liveness = await client.get("/health/live")
            readiness = await client.get("/health/ready")

        assert liveness.status_code == 200
        assert readiness.status_code == 503
        assert readiness.json()["error"]["code"] == "SERVICE_NOT_READY"
        container: Container = app.state.container
        assert container.ready is False
