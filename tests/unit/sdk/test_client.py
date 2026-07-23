"""Unit tests for SDK transport, lifecycle, and error boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from agent_factory.domain.models import DomainKnowledge, DomainKnowledgeDraft
from agent_factory.interfaces.api.contracts import RegisterKnowledgeRequest
from agent_factory.sdk import (
    AgentFactoryApiError,
    AgentFactoryClient,
    AgentFactoryClientClosedError,
    AgentFactoryProtocolError,
    AgentFactoryTransportError,
)

AUTH_TOKEN = "sdk-test-token-that-is-at-least-32-characters"
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000701")


def _correlated_response(
    request: httpx.Request,
    *,
    status_code: int,
    payload: object,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        headers={"X-Correlation-ID": request.headers["X-Correlation-ID"]},
    )


@pytest.mark.asyncio
async def test_context_manager_health_and_close_are_explicit() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _correlated_response(
            request,
            status_code=200,
            payload={"status": "ok"},
        )

    client = AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    assert AUTH_TOKEN not in repr(client)

    async with client as entered:
        assert entered is client
        response = await client.check_liveness(correlation_id=CORRELATION_ID)
        assert response.status == "ok"

    assert client.is_closed is True
    assert requests[0].url.path == "/health/live"
    assert "Authorization" not in requests[0].headers
    assert requests[0].headers["X-Correlation-ID"] == str(CORRELATION_ID)

    await client.close()
    with pytest.raises(AgentFactoryClientClosedError):
        await client.check_liveness()


@pytest.mark.asyncio
async def test_authenticated_write_uses_prefix_headers_and_shared_dto(
    product_knowledge_draft: DomainKnowledgeDraft,
) -> None:
    captured: list[httpx.Request] = []
    record = DomainKnowledge.model_validate(
        {
            **product_knowledge_draft.model_dump(mode="python"),
            "created_at": datetime(2026, 7, 23, tzinfo=UTC),
            "created_by": "local-owner",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _correlated_response(
            request,
            status_code=201,
            payload=record.model_dump(mode="json"),
        )

    request = RegisterKnowledgeRequest.model_validate(
        product_knowledge_draft.model_dump(mode="python")
    )
    async with AgentFactoryClient(
        base_url="http://testserver/root",
        api_prefix="/factory/v2/",
        token=AUTH_TOKEN,
        timeout=2.5,
        transport=httpx.MockTransport(handler),
    ) as client:
        actual = await client.register_knowledge(
            request,
            idempotency_key="register-knowledge-sdk-1",
            correlation_id=CORRELATION_ID,
        )

    sent = captured[0]
    assert actual == record
    assert sent.url.path == "/root/factory/v2/knowledge"
    assert sent.headers["Authorization"] == f"Bearer {AUTH_TOKEN}"
    assert sent.headers["Idempotency-Key"] == "register-knowledge-sdk-1"
    assert "X-Actor-ID" not in sent.headers
    assert json.loads(sent.content) == request.model_dump(mode="json")
    assert sent.extensions["timeout"] == {
        "connect": 2.5,
        "read": 2.5,
        "write": 2.5,
        "pool": 2.5,
    }


@pytest.mark.asyncio
async def test_standard_api_error_preserves_structured_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        correlation_id = request.headers["X-Correlation-ID"]
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "REVISION_CONFLICT",
                    "message": "Instance revision no longer matches",
                    "details": {"expected_revision": 1, "actual_revision": 2},
                    "correlation_id": correlation_id,
                }
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryApiError) as captured:
            await client.list_prototypes(correlation_id=CORRELATION_ID)

    error = captured.value
    assert error.status_code == 409
    assert error.code == "REVISION_CONFLICT"
    assert error.details["actual_revision"] == 2
    assert error.correlation_id == CORRELATION_ID
    assert AUTH_TOKEN not in str(error)
    assert AUTH_TOKEN not in repr(error)


@pytest.mark.asyncio
async def test_nonstandard_error_does_not_copy_response_body() -> None:
    leaked_body = f"upstream error containing {AUTH_TOKEN}"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text=leaked_body)

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryApiError) as captured:
            await client.list_prototypes(correlation_id=CORRELATION_ID)

    error = captured.value
    assert error.code == "SDK_HTTP_ERROR"
    assert error.status_code == 502
    assert error.correlation_id == CORRELATION_ID
    assert leaked_body not in str(error)
    assert AUTH_TOKEN not in vars(error).values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "headers"),
    [
        ({"status": "unexpected"}, {"X-Correlation-ID": str(CORRELATION_ID)}),
        ({"status": "ok"}, {}),
        (
            {"status": "ok"},
            {"X-Correlation-ID": "00000000-0000-0000-0000-000000000702"},
        ),
    ],
)
async def test_success_protocol_violations_are_rejected(
    payload: object,
    headers: dict[str, str],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers=headers)

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryProtocolError) as captured:
            await client.check_liveness(correlation_id=CORRELATION_ID)

    assert captured.value.status_code == 200
    assert captured.value.correlation_id == CORRELATION_ID


@pytest.mark.asyncio
async def test_non_json_success_is_a_protocol_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="not-json",
            headers={"X-Correlation-ID": str(CORRELATION_ID)},
        )

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryProtocolError):
            await client.check_liveness(correlation_id=CORRELATION_ID)


@pytest.mark.asyncio
async def test_standard_error_with_conflicting_correlation_is_protocol_error() -> None:
    other_id = "00000000-0000-0000-0000-000000000702"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "PROTOTYPE_NOT_FOUND",
                    "message": "Prototype not found",
                    "details": {},
                    "correlation_id": other_id,
                }
            },
            headers={"X-Correlation-ID": other_id},
        )

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryProtocolError):
            await client.list_prototypes(correlation_id=CORRELATION_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_error_is_redacted_and_never_retried(
    error_type: type[httpx.RequestError],
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise error_type(
            f"transport details containing {AUTH_TOKEN}",
            request=request,
        )

    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AgentFactoryTransportError) as captured:
            await client.list_prototypes(correlation_id=CORRELATION_ID)

    assert attempts == 1
    assert captured.value.cause_type == error_type.__name__
    assert captured.value.correlation_id == CORRELATION_ID
    assert AUTH_TOKEN not in str(captured.value)


@pytest.mark.asyncio
async def test_concurrent_requests_keep_correlation_local() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        correlation = request.headers["X-Correlation-ID"]
        seen[request.url.path] = correlation
        return _correlated_response(
            request,
            status_code=200,
            payload={"status": "ok"},
        )

    first = UUID("00000000-0000-0000-0000-000000000711")
    second = UUID("00000000-0000-0000-0000-000000000712")
    async with AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.MockTransport(handler),
    ) as client:
        await asyncio.gather(
            client.check_liveness(correlation_id=first),
            client.check_readiness(correlation_id=second),
        )

    assert seen == {
        "/health/live": str(first),
        "/health/ready": str(second),
    }


@pytest.mark.parametrize(
    ("base_url", "api_prefix", "token"),
    [
        ("ftp://example.com", "/api/v1", AUTH_TOKEN),
        ("http://user@example.com", "/api/v1", AUTH_TOKEN),
        ("http://example.com?debug=true", "/api/v1", AUTH_TOKEN),
        ("http://example.com", "api/v1", AUTH_TOKEN),
        ("http://example.com", "/api//v1", AUTH_TOKEN),
        ("http://example.com", "/api/v1", "contains whitespace"),
    ],
)
def test_invalid_client_configuration_is_rejected(
    base_url: str,
    api_prefix: str,
    token: str,
) -> None:
    with pytest.raises(ValueError):
        AgentFactoryClient(
            base_url=base_url,
            api_prefix=api_prefix,
            token=token,
        )
