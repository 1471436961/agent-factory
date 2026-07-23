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
        (method.upper(), path)
        for path, path_item in openapi["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    expected = {
        (
            operation.method,
            (f"/api/v1{operation.path}" if operation.api_scoped else operation.path),
        )
        for operation in SDK_OPERATIONS.values()
    }

    assert len(SDK_OPERATIONS) == 20
    assert actual == expected
    assert all(hasattr(AgentFactoryClient, name) for name in SDK_OPERATIONS)


def test_sdk_reexports_the_exact_rest_request_models() -> None:
    assert BindKnowledgeRequest is RestBindKnowledgeRequest
    assert RegisterPrototypeRequest is RestRegisterPrototypeRequest
