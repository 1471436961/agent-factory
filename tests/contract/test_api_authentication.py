"""API bearer authentication and authorization contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from agent_factory.application.security import FactoryRole
from agent_factory.domain.models import AgentDefinition
from agent_factory.interfaces.api.main import create_app
from agent_factory.settings import Settings

AUTH_TOKEN = "authentication-contract-token-at-least-32-characters"
OTHER_TOKEN = "other-authentication-token-at-least-32-characters"
CORRELATION_ID = "00000000-0000-0000-0000-000000000701"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    database_name: str = "factory.db",
    token: str | None = AUTH_TOKEN,
    subject: str = "trusted-owner",
    roles: frozenset[FactoryRole] = frozenset({FactoryRole.ADMIN}),
) -> Settings:
    values: dict[str, object] = {
        "database_url": (
            f"sqlite+aiosqlite:///{(tmp_path / database_name).as_posix()}"
        ),
        "migrations_dir": migrations_dir,
        "data_dir": tmp_path,
        "auth_subject": subject,
        "auth_roles": roles,
    }
    if token is not None:
        values["auth_token"] = token
    return Settings.model_validate(values)


@asynccontextmanager
async def _running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


def _auth_headers(
    token: str = AUTH_TOKEN,
    **extra: str,
) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _prototype_body(writer_definition: AgentDefinition) -> dict[str, object]:
    return {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": writer_definition.model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_missing_server_authentication_configuration_fails_closed(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_client(
        _settings(tmp_path, migrations_dir, token=None)
    ) as client:
        liveness = await client.get("/health/live")
        readiness = await client.get("/health/ready")
        business = await client.get(
            "/api/v1/prototypes",
            headers=_auth_headers(),
        )

    assert liveness.status_code == 200
    assert readiness.status_code == 503
    assert readiness.json()["error"]["code"] == "SERVICE_NOT_READY"
    assert business.status_code == 503
    assert business.json()["error"]["code"] == ("AUTHENTICATION_NOT_CONFIGURED")


@pytest.mark.asyncio
async def test_missing_and_invalid_credentials_are_stable_and_redacted(
    tmp_path: Path,
    migrations_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    invalid_token = "invalid-token-that-must-not-appear-in-any-response"
    async with _running_client(_settings(tmp_path, migrations_dir)) as client:
        missing = await client.get(
            "/api/v1/prototypes",
            headers={"X-Correlation-ID": CORRELATION_ID},
        )
        invalid = await client.get(
            "/api/v1/prototypes",
            headers=_auth_headers(invalid_token),
        )
        wrong_scheme = await client.get(
            "/api/v1/prototypes",
            headers={"Authorization": f"Basic {AUTH_TOKEN}"},
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert missing.json()["error"]["correlation_id"] == CORRELATION_ID
    assert missing.headers["x-correlation-id"] == CORRELATION_ID
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert invalid.headers["www-authenticate"] == "Bearer"
    assert invalid_token not in invalid.text
    assert wrong_scheme.status_code == 401
    assert wrong_scheme.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert all(invalid_token not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_legacy_actor_header_is_rejected(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_client(_settings(tmp_path, migrations_dir)) as client:
        response = await client.get(
            "/api/v1/prototypes",
            headers=_auth_headers(**{"X-Actor-ID": "forged-owner"}),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ACTOR_HEADER_NOT_ALLOWED"
    assert "forged-owner" not in response.text


@pytest.mark.parametrize(
    ("role", "write_status", "audit_status"),
    [
        (FactoryRole.VIEWER, 403, 403),
        (FactoryRole.OPERATOR, 201, 403),
        (FactoryRole.AUDITOR, 403, 200),
        (FactoryRole.ADMIN, 201, 200),
    ],
)
@pytest.mark.asyncio
async def test_role_matrix_protects_read_write_and_audit_routes(
    role: FactoryRole,
    write_status: int,
    audit_status: int,
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    settings = _settings(
        tmp_path,
        migrations_dir,
        database_name=f"factory-{role.value}.db",
        subject=f"{role.value}-subject",
        roles=frozenset({role}),
    )
    async with _running_client(settings) as client:
        read = await client.get(
            "/api/v1/prototypes",
            headers=_auth_headers(),
        )
        write = await client.post(
            "/api/v1/prototypes",
            json=_prototype_body(writer_definition),
            headers=_auth_headers(),
        )
        audit = await client.get(
            "/api/v1/audit-events",
            headers=_auth_headers(),
        )

    assert read.status_code == 200
    assert write.status_code == write_status
    if write_status == 403:
        assert write.json()["error"]["code"] == "AUTHORIZATION_DENIED"
        assert write.json()["error"]["details"] == {
            "required_permission": "factory:write"
        }
    assert audit.status_code == audit_status
    if audit_status == 403:
        assert audit.json()["error"]["code"] == "AUTHORIZATION_DENIED"
        assert audit.json()["error"]["details"] == {"required_permission": "audit:read"}


@pytest.mark.asyncio
async def test_authenticated_subject_is_the_only_audit_actor(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
) -> None:
    settings = _settings(
        tmp_path,
        migrations_dir,
        subject="configured-owner",
    )
    async with _running_client(settings) as client:
        registered = await client.post(
            "/api/v1/prototypes",
            json=_prototype_body(writer_definition),
            headers=_auth_headers(),
        )
        audit = await client.get(
            "/api/v1/audit-events",
            headers=_auth_headers(),
        )

    assert registered.status_code == 201
    assert audit.status_code == 200
    assert audit.json()["items"][0]["actor"] == "configured-owner"


def test_openapi_secures_every_non_health_operation(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    app = create_app(_settings(tmp_path, migrations_dir))
    openapi = app.openapi()

    bearer = openapi["components"]["securitySchemes"]["BearerAuth"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    for path, path_item in openapi["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if path.startswith("/health/"):
                assert "security" not in operation
            else:
                assert operation["security"] == [{"BearerAuth": []}]


@pytest.mark.asyncio
async def test_independent_apps_do_not_share_authentication_configuration(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    first_settings = _settings(
        tmp_path,
        migrations_dir,
        database_name="first.db",
        token=AUTH_TOKEN,
        subject="first-owner",
    )
    second_settings = _settings(
        tmp_path,
        migrations_dir,
        database_name="second.db",
        token=OTHER_TOKEN,
        subject="second-owner",
    )
    async with _running_client(first_settings) as first_client:
        async with _running_client(second_settings) as second_client:
            first_valid = await first_client.get(
                "/api/v1/prototypes",
                headers=_auth_headers(AUTH_TOKEN),
            )
            first_invalid = await first_client.get(
                "/api/v1/prototypes",
                headers=_auth_headers(OTHER_TOKEN),
            )
            second_valid = await second_client.get(
                "/api/v1/prototypes",
                headers=_auth_headers(OTHER_TOKEN),
            )
            second_invalid = await second_client.get(
                "/api/v1/prototypes",
                headers=_auth_headers(AUTH_TOKEN),
            )

    assert first_valid.status_code == 200
    assert second_valid.status_code == 200
    assert first_invalid.status_code == 401
    assert second_invalid.status_code == 401
