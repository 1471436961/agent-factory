"""Validation tests for application persistence contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, Page


def test_page_and_idempotency_record_accept_valid_values() -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)

    page = Page[str](items=("first",), page=1, page_size=20, total=1)
    record = IdempotencyRecord(
        idempotency_key="register-prototype-1",
        operation="register-prototype",
        request_hash="a" * 64,
        response={"prototype_id": "writer-agent"},
        created_at=now,
        expires_at=now + timedelta(days=1),
    )

    assert page.items == ("first",)
    assert record.response["prototype_id"] == "writer-agent"


def test_audit_query_rejects_reversed_time_range() -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)

    with pytest.raises(ValidationError, match="must not exceed"):
        AuditQuery(
            created_from=now,
            created_to=now - timedelta(seconds=1),
        )


def test_idempotency_record_requires_future_expiry() -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)

    with pytest.raises(ValidationError, match="later than"):
        IdempotencyRecord(
            idempotency_key="register-prototype-1",
            operation="register-prototype",
            request_hash="a" * 64,
            response={},
            created_at=now,
            expires_at=now,
        )
