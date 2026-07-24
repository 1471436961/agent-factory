"""Contract checks preventing SDK and OpenAPI operation drift."""

from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest as RestBindKnowledgeRequest,
)
from agent_factory.interfaces.api.contracts import (
    RegisterPrototypeRequest as RestRegisterPrototypeRequest,
)
from agent_factory.interfaces.api.main import create_app
from agent_factory.sdk import BindKnowledgeRequest, RegisterPrototypeRequest
from agent_factory.sdk.client import AgentFactoryClient
from agent_factory.sdk.operations import SDK_OPERATIONS


def test_sdk_manifest_exactly_matches_public_openapi_operations() -> None:
    openapi = create_app().openapi()
    actual = {
        (method.upper(), path): any(
            requirement.get("BearerAuth") == []
            for requirement in operation.get("security", [])
        )
        for path, path_item in openapi["paths"].items()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    expected = {
        (
            operation.method,
            (f"/api/v1{operation.path}" if operation.api_scoped else operation.path),
        ): operation.authenticated
        for operation in SDK_OPERATIONS.values()
    }

    assert len(SDK_OPERATIONS) == 20
    assert len(actual) == len(expected) == len(SDK_OPERATIONS)
    assert actual == expected
    assert all(hasattr(AgentFactoryClient, name) for name in SDK_OPERATIONS)


def test_sdk_reexports_the_exact_rest_request_models() -> None:
    assert BindKnowledgeRequest is RestBindKnowledgeRequest
    assert RegisterPrototypeRequest is RestRegisterPrototypeRequest
