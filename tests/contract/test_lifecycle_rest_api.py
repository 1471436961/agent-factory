"""Authenticated REST contracts for M3.2 lifecycle transitions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from agent_factory.application.security import FactoryRole
from agent_factory.interfaces.api.main import create_app
from agent_factory.settings import Settings

AUTH_TOKEN = "lifecycle-contract-token-at-least-32-characters"
CORRELATION_ID = "00000000-0000-0000-0000-000000000821"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    roles: frozenset[FactoryRole] = frozenset({FactoryRole.ADMIN}),
) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
            "auth_subject": "lifecycle-owner",
            "auth_roles": roles,
        }
    )


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


async def _create_instance(client: httpx.AsyncClient) -> str:
    prototype = await client.post(
        "/api/v1/prototypes",
        json={
            "prototype_id": "minimal-agent",
            "version": "1.0.0",
            "definition": {
                "agent_type": "minimal-agent",
                "role": "Minimal Agent",
                "system_prompt": "Return a deterministic response.",
            },
            "publish": True,
        },
        headers=_headers(idempotency_key="register-minimal-agent"),
    )
    assert prototype.status_code == 201, prototype.text
    cloned = await client.post(
        "/api/v1/prototypes/minimal-agent/versions/1.0.0/instances",
        json={"runtime_target": "demo-runtime"},
        headers=_headers(idempotency_key="clone-minimal-agent"),
    )
    assert cloned.status_code == 201, cloned.text
    return str(cloned.json()["instance_id"])


@pytest.mark.asyncio
async def test_rest_lifecycle_retry_audit_and_replay_survive_restart(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    retry_body = {
        "expected_revision": 3,
        "target_status": "running",
        "reason": "retry failed runtime",
        "retry": True,
    }

    async with _running_client(settings) as client:
        instance_id = await _create_instance(client)
        start_body = {
            "expected_revision": 1,
            "target_status": "running",
            "reason": "start deterministic runtime",
        }
        started = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json=start_body,
            headers=_headers(
                idempotency_key="transition-start-runtime",
                correlation_id=CORRELATION_ID,
            ),
        )
        start_replay = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json=start_body,
            headers=_headers(idempotency_key="transition-start-runtime"),
        )
        assert started.status_code == 200, started.text
        assert start_replay.json() == started.json()
        assert started.json()["revision"] == 2
        assert started.json()["status"] == "running"

        failed = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={
                "expected_revision": 2,
                "target_status": "failed",
                "reason": "runtime adapter failed",
            },
            headers=_headers(idempotency_key="transition-runtime-failed"),
        )
        missing_retry = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={**retry_body, "retry": False},
            headers=_headers(idempotency_key="transition-missing-retry"),
        )
        retried = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json=retry_body,
            headers=_headers(idempotency_key="transition-retry-runtime"),
        )
        manual_degradation = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={
                "expected_revision": 4,
                "target_status": "degraded",
                "reason": "must not bypass degradation evidence",
            },
            headers=_headers(idempotency_key="transition-manual-degraded"),
        )

        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert missing_retry.status_code == 409
        assert missing_retry.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
        assert missing_retry.json()["error"]["details"]["reason"] == "retry-required"
        assert retried.status_code == 200
        assert retried.json()["revision"] == 4
        assert manual_degradation.status_code == 409
        assert manual_degradation.json()["error"]["details"]["reason"] == (
            "degraded-status-is-policy-owned"
        )

        exported = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 4},
            headers=_headers(),
        )
        audit = await client.get(
            "/api/v1/audit-events",
            params={"entity_id": instance_id, "page_size": 100},
            headers=_headers(),
        )
        assert exported.status_code == 200, exported.text
        transition_events = [
            event
            for event in audit.json()["items"]
            if event["event_type"] == "instance.transitioned"
        ]
        assert len(transition_events) == 3
        started_event = next(
            event
            for event in transition_events
            if event["payload"]["to_status"] == "running"
            and event["payload"]["retry"] is False
        )
        assert started_event["actor"] == "lifecycle-owner"
        assert started_event["correlation_id"] == CORRELATION_ID
        retried_record = retried.json()
        exported_record = exported.json()

    async with _running_client(settings) as client:
        replay = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json=retry_body,
            headers=_headers(idempotency_key="transition-retry-runtime"),
        )
        persisted_spec = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 4},
            headers=_headers(),
        )
        persisted_audit = await client.get(
            "/api/v1/audit-events",
            params={"entity_id": instance_id, "page_size": 100},
            headers=_headers(),
        )

        assert replay.status_code == 200
        assert replay.json() == retried_record
        assert persisted_spec.json() == exported_record
        assert (
            sum(
                event["event_type"] == "instance.transitioned"
                for event in persisted_audit.json()["items"]
            )
            == 3
        )


@pytest.mark.asyncio
async def test_transition_contract_rejects_untrusted_or_invalid_inputs(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    admin_settings = _settings(tmp_path, migrations_dir)
    async with _running_client(admin_settings) as client:
        instance_id = await _create_instance(client)
        blank_reason = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={
                "expected_revision": 1,
                "target_status": "running",
                "reason": "   ",
                "private_input": "must-not-be-echoed",
            },
            headers=_headers(),
        )
        forged_actor = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={
                "expected_revision": 1,
                "target_status": "terminated",
                "reason": "stop",
                "actor": "forged-owner",
            },
            headers=_headers(),
        )

        assert blank_reason.status_code == 422
        assert blank_reason.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
        assert "must-not-be-echoed" not in blank_reason.text
        assert forged_actor.status_code == 422
        assert "forged-owner" not in forged_actor.text

        openapi = create_app(admin_settings).openapi()
        operation = openapi["paths"]["/api/v1/instances/{instance_id}/transitions"][
            "post"
        ]
        assert operation["security"] == [{"BearerAuth": []}]

    viewer_settings = _settings(
        tmp_path,
        migrations_dir,
        roles=frozenset({FactoryRole.VIEWER}),
    )
    async with _running_client(viewer_settings) as client:
        denied = await client.post(
            f"/api/v1/instances/{instance_id}/transitions",
            json={
                "expected_revision": 1,
                "target_status": "terminated",
                "reason": "viewer cannot mutate",
            },
            headers=_headers(),
        )

        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "AUTHORIZATION_DENIED"
        assert denied.json()["error"]["details"] == {
            "required_permission": "factory:write"
        }
