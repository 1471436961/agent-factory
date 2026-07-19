"""Unit tests for REST DTO and error mapping completeness."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.application.commands import KnowledgeSelection
from agent_factory.domain.errors import FactoryError
from agent_factory.domain.models import AgentDefinition
from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest,
    RegisterPrototypeRequest,
)
from agent_factory.interfaces.api.errors import ERROR_STATUS_BY_CODE, error_response


def test_every_current_factory_error_has_an_http_status() -> None:
    implemented_codes = {
        error_type.code for error_type in FactoryError.__subclasses__()
    }

    assert set(ERROR_STATUS_BY_CODE) == implemented_codes


def test_request_contracts_reject_extra_and_duplicate_selections(
    writer_definition: AgentDefinition,
) -> None:
    with pytest.raises(ValidationError, match="extra"):
        RegisterPrototypeRequest.model_validate(
            {
                "prototype_id": "writer-agent",
                "version": "1.0.0",
                "definition": writer_definition.model_dump(mode="json"),
                "unexpected": True,
            }
        )

    selection = KnowledgeSelection(
        slot_name="product-docs",
        knowledge_id="agent-factory-docs",
        version="1.0.0",
    )
    with pytest.raises(ValidationError, match="duplicate knowledge references"):
        BindKnowledgeRequest(
            expected_revision=1,
            selections=(selection, selection),
        )


def test_error_response_uses_stable_correlation_envelope() -> None:
    correlation_id = UUID("00000000-0000-0000-0000-000000000301")

    response = error_response(
        status_code=409,
        code="REVISION_CONFLICT",
        message="Instance revision no longer matches",
        details={"expected_revision": 1, "actual_revision": 2},
        correlation_id=correlation_id,
    )

    assert response.status_code == 409
    assert response.headers["x-correlation-id"] == str(correlation_id)
    assert b'"code":"REVISION_CONFLICT"' in response.body
    assert b'"correlation_id":"00000000-0000-0000-0000-000000000301"' in (response.body)
