"""HTTP contract tests over the real M1 Controller and file-backed SQLite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from agent_factory.application.controller import FactoryController
from agent_factory.application.queries import Page, PrototypeListQuery
from agent_factory.domain.models import (
    AgentDefinition,
    AgentPrototype,
    DomainKnowledgeDraft,
)
from agent_factory.interfaces.api.main import create_app
from agent_factory.settings import Settings

CORRELATION_ID = "00000000-0000-0000-0000-000000000301"
AUTH_TOKEN = "rest-contract-token-that-is-at-least-32-characters"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    api_prefix: str = "/api/v1",
    max_request_bytes: int = 1_048_576,
) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "api_prefix": api_prefix,
            "max_request_bytes": max_request_bytes,
            "auth_token": AUTH_TOKEN,
        }
    )


@asynccontextmanager
async def _running_client(
    settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client, app


def _headers(
    *,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    return headers


@pytest.mark.asyncio
async def test_register_clone_bind_export(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    prototype_body = {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": writer_definition.model_dump(mode="json"),
    }

    async with _running_client(settings) as (client, _):
        register_headers = _headers(
            idempotency_key="register-prototype-1",
            correlation_id=CORRELATION_ID,
        )
        registered = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
            headers=register_headers,
        )
        replay = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
            headers=register_headers,
        )
        assert registered.status_code == 201
        assert replay.status_code == 201
        assert replay.json() == registered.json()
        assert registered.headers["x-correlation-id"] == CORRELATION_ID

        listed = await client.get(
            "/api/v1/prototypes",
            params={"status": "draft"},
            headers=_headers(),
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        published = await client.post(
            "/api/v1/prototypes/writer-agent/versions/1.0.0/publish",
            headers=_headers(idempotency_key="publish-prototype-1"),
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"

        knowledge = await client.post(
            "/api/v1/knowledge",
            json=product_knowledge_draft.model_dump(mode="json"),
            headers=_headers(idempotency_key="register-knowledge-1"),
        )
        assert knowledge.status_code == 201

        cloned = await client.post(
            "/api/v1/prototypes/writer-agent/versions/1.0.0/instances",
            json={"runtime_target": "local-runtime"},
            headers=_headers(idempotency_key="clone-writer-agent-1"),
        )
        assert cloned.status_code == 201
        instance_id = cloned.json()["instance_id"]

        unbound_export = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={},
            headers=_headers(),
        )
        assert unbound_export.status_code == 422
        assert unbound_export.json()["error"]["code"] == "MISSING_KNOWLEDGE_BINDING"

        bound = await client.post(
            f"/api/v1/instances/{instance_id}/knowledge-bindings",
            json={
                "expected_revision": 1,
                "selections": [
                    {
                        "slot_name": "product-docs",
                        "knowledge_id": "agent-factory-docs",
                        "version": "1.0.0",
                    }
                ],
            },
            headers=_headers(idempotency_key="bind-product-docs-1"),
        )
        assert bound.status_code == 200
        assert bound.json()["revision"] == 2

        first_spec = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={},
            headers=_headers(),
        )
        second_spec = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 2},
            headers=_headers(),
        )
        assert first_spec.status_code == 200
        assert second_spec.json() == first_spec.json()
        assert first_spec.json()["tools"][0]["name"] == "document-search"
        first_spec_payload = first_spec.json()

        deprecated = await client.post(
            "/api/v1/prototypes/writer-agent/versions/1.0.0/deprecate",
            json={"reason": "Replaced by version 2."},
            headers=_headers(idempotency_key="deprecate-prototype-1"),
        )
        assert deprecated.status_code == 200
        assert deprecated.json()["status"] == "deprecated"

        audit = await client.get(
            "/api/v1/audit-events",
            params=[("event_type", "prototype.registered"), ("page_size", "100")],
            headers=_headers(),
        )
        assert audit.status_code == 200
        assert audit.json()["total"] == 1
        assert audit.json()["items"][0]["correlation_id"] == CORRELATION_ID

        all_audit = await client.get(
            "/api/v1/audit-events",
            params={"page_size": 100},
            headers=_headers(),
        )
        assert all_audit.json()["total"] == 7

        instance_audit = await client.get(
            "/api/v1/audit-events",
            params={
                "entity_type": "instance",
                "entity_id": instance_id,
                "page_size": 100,
            },
            headers=_headers(),
        )
        assert [
            event["event_type"] for event in reversed(instance_audit.json()["items"])
        ] == [
            "instance.cloned",
            "knowledge.bound",
            "spec.exported",
        ]

    async with _running_client(settings) as (client, _):
        readiness = await client.get("/health/ready")
        assert readiness.status_code == 200

        persisted_prototypes = await client.get(
            "/api/v1/prototypes",
            params={"status": "deprecated"},
            headers=_headers(),
        )
        assert persisted_prototypes.status_code == 200
        assert persisted_prototypes.json()["total"] == 1

        persisted_spec = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 2},
            headers=_headers(),
        )
        assert persisted_spec.status_code == 200
        assert persisted_spec.json() == first_spec_payload

        persisted_audit = await client.get(
            "/api/v1/audit-events",
            params={"page_size": 100},
            headers=_headers(),
        )
        assert persisted_audit.status_code == 200
        assert persisted_audit.json()["total"] == 7
        assert (
            sum(
                event["event_type"] == "spec.exported"
                for event in persisted_audit.json()["items"]
            )
            == 1
        )


@pytest.mark.asyncio
async def test_rest_errors_are_stable_correlated_and_redacted(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    prototype_body = {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": writer_definition.model_dump(mode="json"),
    }

    async with _running_client(settings) as (client, app):
        missing_authentication = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
        )
        assert missing_authentication.status_code == 401
        assert missing_authentication.json()["error"]["code"] == (
            "AUTHENTICATION_REQUIRED"
        )
        assert missing_authentication.headers["www-authenticate"] == "Bearer"

        invalid_body = await client.post(
            "/api/v1/prototypes",
            json={**prototype_body, "secret": "do-not-echo"},
            headers=_headers(),
        )
        assert invalid_body.status_code == 422
        assert "do-not-echo" not in invalid_body.text

        invalid_correlation = await client.get(
            "/health/live",
            headers={"X-Correlation-ID": "not-a-uuid"},
        )
        generated = invalid_correlation.headers["x-correlation-id"]
        assert invalid_correlation.status_code == 400
        assert invalid_correlation.json()["error"]["code"] == ("INVALID_CORRELATION_ID")
        assert invalid_correlation.json()["error"]["correlation_id"] == generated
        UUID(generated)
        assert app.state.container.correlation_context.get() is None

        route_missing = await client.get("/api/v1/not-a-route")
        method_invalid = await client.get("/api/v1/knowledge")
        assert route_missing.status_code == 404
        assert route_missing.json()["error"]["code"] == "ROUTE_NOT_FOUND"
        assert method_invalid.status_code == 405
        assert method_invalid.json()["error"]["code"] == "METHOD_NOT_ALLOWED"

        missing_prototype = await client.post(
            "/api/v1/prototypes/missing-agent/versions/1.0.0/instances",
            json={},
            headers=_headers(),
        )
        assert missing_prototype.status_code == 404
        assert missing_prototype.json()["error"]["code"] == ("PROTOTYPE_NOT_FOUND")

        first = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
            headers=_headers(),
        )
        duplicate = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
            headers=_headers(),
        )
        assert first.status_code == 201
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "PROTOTYPE_ALREADY_EXISTS"

        async def explode(
            self: FactoryController,
            query: PrototypeListQuery,
        ) -> Page[AgentPrototype]:
            raise RuntimeError("sqlite failure at E:/private/factory.db")

        monkeypatch.setattr(FactoryController, "list_prototypes", explode)
        internal = await client.get("/api/v1/prototypes", headers=_headers())
        assert internal.status_code == 500
        assert internal.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "sqlite" not in internal.text.lower()
        assert "private" not in internal.text.lower()


class ChunkedBody(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'\x7b"content":"' + (b"a" * 700)
        yield b"b" * 700 + b'"\x7d'


@pytest.mark.asyncio
async def test_request_context_limits_declared_and_streamed_bodies(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(
        tmp_path,
        migrations_dir,
        max_request_bytes=1_024,
    )

    async with _running_client(settings) as (client, app):
        declared = await client.post(
            "/api/v1/knowledge",
            content=b"x" * 1_025,
            headers=_headers(),
        )
        streamed = await client.post(
            "/api/v1/knowledge",
            content=ChunkedBody(),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        unread_streamed = await client.request(
            "GET",
            "/health/live",
            content=ChunkedBody(),
        )

        for response in (declared, streamed, unread_streamed):
            assert response.status_code == 413, response.text
            assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
            assert response.json()["error"]["details"] == {"max_bytes": 1_024}
            assert (
                response.headers["x-correlation-id"]
                == (response.json()["error"]["correlation_id"])
            )
        assert app.state.container.correlation_context.get() is None


@pytest.mark.asyncio
async def test_readiness_error_and_configurable_api_prefix(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(
        tmp_path,
        migrations_dir,
        api_prefix="/factory/v2",
    )
    app = create_app(settings)
    assert "/factory/v2/prototypes" in app.openapi()["paths"]
    assert (
        "/factory/v2/instances/{instance_id}/spec-exports" in (app.openapi()["paths"])
    )
    assert (
        "get"
        not in app.openapi()["paths"][
            "/factory/v2/instances/{instance_id}/spec-exports"
        ]
    )
    m2_paths = {
        "/factory/v2/evaluation-suites": {"post"},
        "/factory/v2/evaluation-suites/{suite_id}/versions/{version}": {"get"},
        "/factory/v2/skill-trees": {"post"},
        "/factory/v2/skill-trees/{tree_id}/versions/{version}": {"get"},
        "/factory/v2/instances/{instance_id}/evaluations": {"post"},
        "/factory/v2/evaluation-reports/{report_id}/reviews": {"post"},
        "/factory/v2/instances/{instance_id}/promotions": {"post"},
        "/factory/v2/instances/{instance_id}/task-outcomes": {"post"},
    }
    openapi_paths = app.openapi()["paths"]
    for path, expected_methods in m2_paths.items():
        assert path in openapi_paths
        actual_methods = {
            method
            for method in openapi_paths[path]
            if method in {"get", "post", "put", "patch", "delete"}
        }
        assert actual_methods == expected_methods

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/health/ready")

    assert readiness.status_code == 503
    assert readiness.json()["error"]["code"] == "SERVICE_NOT_READY"
