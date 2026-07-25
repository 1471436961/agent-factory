"""Central HTTP rejection and response-hardening security invariants."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent_factory.application.controller import FactoryController
from agent_factory.application.queries import Page, PrototypeListQuery
from agent_factory.application.security import FactoryRole
from agent_factory.domain.models import AgentDefinition, AgentPrototype
from agent_factory.interfaces.api.app import create_app
from agent_factory.settings import Settings

AUTH_TOKEN = "m43-security-token-that-is-at-least-32-characters"
CORRELATION_ID = "00000000-0000-0000-0000-000000004301"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    token: str | None = AUTH_TOKEN,
    roles: frozenset[FactoryRole] = frozenset({FactoryRole.ADMIN}),
    max_request_bytes: int = 1_024,
) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}",
        "migrations_dir": migrations_dir,
        "data_dir": tmp_path,
        "auth_subject": "m43-security-owner",
        "auth_roles": roles,
        "max_request_bytes": max_request_bytes,
    }
    if token is not None:
        values["auth_token"] = token
    return Settings.model_validate(values)


@asynccontextmanager
async def _running_app(
    settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client, app


def _auth_headers(token: str = AUTH_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _prototype_body(definition: AgentDefinition) -> dict[str, object]:
    return {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": definition.model_dump(mode="json"),
    }


def _assert_security_headers(response: httpx.Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"]


@pytest.mark.asyncio
async def test_security_headers_cover_success_and_early_middleware_errors(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_app(_settings(tmp_path, migrations_dir)) as (client, _):
        success = await client.get("/health/live")
        authentication_error = await client.get("/api/v1/prototypes")
        correlation_error = await client.get(
            "/health/live",
            headers={"X-Correlation-ID": "not-a-uuid"},
        )
        oversized = await client.post(
            "/api/v1/prototypes",
            content=b"x" * 1_025,
            headers=_auth_headers(),
        )

    assert success.status_code == 200
    assert authentication_error.status_code == 401
    assert correlation_error.status_code == 400
    assert oversized.status_code == 413
    for response in (success, authentication_error, correlation_error, oversized):
        _assert_security_headers(response)


@pytest.mark.parametrize("content_length", ["", "-1", "+1", "1.0", " 1"])
@pytest.mark.asyncio
async def test_non_decimal_content_lengths_are_rejected(
    content_length: str,
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_app(_settings(tmp_path, migrations_dir)) as (client, _):
        response = await client.get(
            "/health/live",
            headers={"Content-Length": content_length},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CONTENT_LENGTH"
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_duplicate_content_lengths_are_rejected(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_app(_settings(tmp_path, migrations_dir)) as (client, _):
        response = await client.get(
            "/health/live",
            headers=[("Content-Length", "0"), ("Content-Length", "0")],
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CONTENT_LENGTH"


@pytest.mark.asyncio
async def test_authentication_rejections_stop_before_controller_and_log_allowlist(
    tmp_path: Path,
    migrations_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def counted_list(
        self: FactoryController,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]:
        del self, query
        nonlocal calls
        calls += 1
        return Page(items=(), total=0, page=1, page_size=20)

    monkeypatch.setattr(FactoryController, "list_prototypes", counted_list)
    invalid_token = "INVALID-CREDENTIAL-MUST-NOT-LEAK"
    caplog.set_level(logging.WARNING, logger="agent_factory.security")

    async with _running_app(_settings(tmp_path, migrations_dir)) as (client, _):
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
    async with _running_app(_settings(tmp_path, migrations_dir, token=None)) as (
        client,
        _,
    ):
        not_configured = await client.get(
            "/api/v1/prototypes",
            headers=_auth_headers(),
        )

    assert calls == 0
    assert [
        missing.status_code,
        invalid.status_code,
        wrong_scheme.status_code,
        not_configured.status_code,
    ] == [
        401,
        401,
        401,
        503,
    ]
    records = [
        record for record in caplog.records if record.name == "agent_factory.security"
    ]
    assert [record.getMessage() for record in records] == [
        "authentication_rejected",
        "authentication_rejected",
        "authentication_rejected",
        "authentication_rejected",
    ]
    assert [record.__dict__["security_category"] for record in records] == [
        "authentication_required",
        "authentication_failed",
        "authentication_required",
        "authentication_not_configured",
    ]
    assert [record.__dict__["credential_present"] for record in records] == [
        False,
        True,
        True,
        True,
    ]
    assert records[0].__dict__["correlation_id"] == CORRELATION_ID
    assert AUTH_TOKEN not in caplog.text
    assert invalid_token not in caplog.text


@pytest.mark.asyncio
async def test_authorization_and_forged_actor_stop_before_controller(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def counted_register(
        self: FactoryController,
        command: object,
    ) -> AgentPrototype:
        del self, command
        nonlocal calls
        calls += 1
        raise AssertionError("controller must not run")

    monkeypatch.setattr(FactoryController, "register_prototype", counted_register)
    caplog.set_level(logging.WARNING, logger="agent_factory.security")
    settings = _settings(
        tmp_path,
        migrations_dir,
        roles=frozenset({FactoryRole.VIEWER}),
    )
    body = _prototype_body(writer_definition)

    async with _running_app(settings) as (client, _):
        denied = await client.post(
            "/api/v1/prototypes",
            json=body,
            headers=_auth_headers(),
        )
        forged_header = await client.post(
            "/api/v1/prototypes",
            json=body,
            headers={**_auth_headers(), "X-Actor-ID": "forged-header-actor"},
        )
        forged_body = await client.post(
            "/api/v1/prototypes",
            json={**body, "actor": "forged-body-actor"},
            headers=_auth_headers(),
        )

    async with _running_app(_settings(tmp_path, migrations_dir)) as (client, _):
        admin_forged_body = await client.post(
            "/api/v1/prototypes",
            json={**body, "actor": "forged-body-actor"},
            headers=_auth_headers(),
        )

    assert calls == 0
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AUTHORIZATION_DENIED"
    assert forged_header.status_code == 400
    assert forged_header.json()["error"]["code"] == "ACTOR_HEADER_NOT_ALLOWED"
    assert forged_body.status_code == 403
    assert forged_body.json()["error"]["code"] == "AUTHORIZATION_DENIED"
    assert admin_forged_body.status_code == 422
    assert admin_forged_body.json()["error"]["code"] == ("REQUEST_VALIDATION_FAILED")
    combined = (
        denied.text
        + forged_header.text
        + forged_body.text
        + admin_forged_body.text
        + caplog.text
    )
    assert "forged-header-actor" not in combined
    assert "forged-body-actor" not in combined
    security_events = [
        record.getMessage()
        for record in caplog.records
        if record.name == "agent_factory.security"
    ]
    assert security_events == [
        "authorization_rejected",
        "authentication_rejected",
        "authorization_rejected",
    ]
