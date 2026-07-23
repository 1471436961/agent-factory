"""Asynchronous HTTP client for every public Agent Factory operation."""

from __future__ import annotations

import re
from datetime import datetime
from types import TracebackType
from typing import NoReturn, Self, TypeVar
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from agent_factory.application.queries import Page
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import (
    AuditEntityType,
    AuditEventType,
    PrototypeStatus,
)
from agent_factory.domain.evaluation import (
    EvaluationReport,
    EvaluationReview,
    EvaluationSuite,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
)
from agent_factory.domain.skills import DegradationCheckResult, SkillTree
from agent_factory.interfaces.api.contracts import (
    BindKnowledgeRequest,
    CloneAgentRequest,
    DeprecatePrototypeRequest,
    ErrorResponse,
    EvaluateInstanceRequest,
    ExportSpecRequest,
    HealthResponse,
    PromoteAgentRequest,
    RecordTaskOutcomeRequest,
    RegisterEvaluationSuiteRequest,
    RegisterKnowledgeRequest,
    RegisterPrototypeRequest,
    RegisterSkillTreeRequest,
    ReviewEvaluationRequest,
    TransitionInstanceRequest,
)
from agent_factory.sdk.errors import (
    AgentFactoryApiError,
    AgentFactoryClientClosedError,
    AgentFactoryProtocolError,
    AgentFactoryTransportError,
)
from agent_factory.sdk.operations import SDK_OPERATIONS, SdkOperation

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
QueryParamValue = str | int | float | bool | None
QueryParams = list[tuple[str, QueryParamValue]]

_API_PREFIX_PATTERN = re.compile(r"/(?:[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*)?")


class AgentFactoryClient:
    """Own one HTTPX client and expose validated Agent Factory operations."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        api_prefix: str = "/api/v1",
        timeout: float | httpx.Timeout = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_base_url = _normalize_base_url(base_url)
        self._api_prefix = _normalize_api_prefix(api_prefix)
        self._token = SecretStr(_validate_token(token))
        self._client = httpx.AsyncClient(
            base_url=normalized_base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._base_url = normalized_base_url
        self._closed = False

    def __repr__(self) -> str:
        shown_prefix = self._api_prefix or "/"
        return (
            f"{type(self).__name__}(base_url={self._base_url!r}, "
            f"api_prefix={shown_prefix!r}, closed={self._closed!r})"
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True

    async def check_liveness(
        self,
        *,
        correlation_id: UUID | None = None,
    ) -> HealthResponse:
        return await self._request(
            "check_liveness",
            response_model=HealthResponse,
            correlation_id=correlation_id,
        )

    async def check_readiness(
        self,
        *,
        correlation_id: UUID | None = None,
    ) -> HealthResponse:
        return await self._request(
            "check_readiness",
            response_model=HealthResponse,
            correlation_id=correlation_id,
        )

    async def register_prototype(
        self,
        request: RegisterPrototypeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentPrototype:
        return await self._request(
            "register_prototype",
            response_model=AgentPrototype,
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def list_prototypes(
        self,
        *,
        status: PrototypeStatus | None = None,
        agent_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
        correlation_id: UUID | None = None,
    ) -> Page[AgentPrototype]:
        params: QueryParams = [
            ("page", str(page)),
            ("page_size", str(page_size)),
        ]
        if status is not None:
            params.append(("status", status.value))
        if agent_type is not None:
            params.append(("agent_type", agent_type))
        return await self._request(
            "list_prototypes",
            response_model=Page[AgentPrototype],
            params=params,
            correlation_id=correlation_id,
        )

    async def publish_prototype(
        self,
        prototype_id: str,
        version: str,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentPrototype:
        return await self._request(
            "publish_prototype",
            response_model=AgentPrototype,
            path_params={"prototype_id": prototype_id, "version": version},
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def deprecate_prototype(
        self,
        prototype_id: str,
        version: str,
        request: DeprecatePrototypeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentPrototype:
        return await self._request(
            "deprecate_prototype",
            response_model=AgentPrototype,
            path_params={"prototype_id": prototype_id, "version": version},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def clone_agent(
        self,
        prototype_id: str,
        version: str,
        request: CloneAgentRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentInstance:
        return await self._request(
            "clone_agent",
            response_model=AgentInstance,
            path_params={"prototype_id": prototype_id, "version": version},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def register_knowledge(
        self,
        request: RegisterKnowledgeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> DomainKnowledge:
        return await self._request(
            "register_knowledge",
            response_model=DomainKnowledge,
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def bind_knowledge(
        self,
        instance_id: UUID,
        request: BindKnowledgeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentInstance:
        return await self._request(
            "bind_knowledge",
            response_model=AgentInstance,
            path_params={"instance_id": instance_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def export_spec(
        self,
        instance_id: UUID,
        request: ExportSpecRequest,
        *,
        correlation_id: UUID | None = None,
    ) -> AgentSpec:
        return await self._request(
            "export_spec",
            response_model=AgentSpec,
            path_params={"instance_id": instance_id},
            body=request,
            correlation_id=correlation_id,
        )

    async def transition_instance(
        self,
        instance_id: UUID,
        request: TransitionInstanceRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentInstance:
        return await self._request(
            "transition_instance",
            response_model=AgentInstance,
            path_params={"instance_id": instance_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def register_evaluation_suite(
        self,
        request: RegisterEvaluationSuiteRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> EvaluationSuite:
        return await self._request(
            "register_evaluation_suite",
            response_model=EvaluationSuite,
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def get_evaluation_suite(
        self,
        suite_id: str,
        version: str,
        *,
        correlation_id: UUID | None = None,
    ) -> EvaluationSuite:
        return await self._request(
            "get_evaluation_suite",
            response_model=EvaluationSuite,
            path_params={"suite_id": suite_id, "version": version},
            correlation_id=correlation_id,
        )

    async def register_skill_tree(
        self,
        request: RegisterSkillTreeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> SkillTree:
        return await self._request(
            "register_skill_tree",
            response_model=SkillTree,
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def get_skill_tree(
        self,
        tree_id: str,
        version: str,
        *,
        correlation_id: UUID | None = None,
    ) -> SkillTree:
        return await self._request(
            "get_skill_tree",
            response_model=SkillTree,
            path_params={"tree_id": tree_id, "version": version},
            correlation_id=correlation_id,
        )

    async def evaluate_instance(
        self,
        instance_id: UUID,
        request: EvaluateInstanceRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> EvaluationReport:
        return await self._request(
            "evaluate_instance",
            response_model=EvaluationReport,
            path_params={"instance_id": instance_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def review_evaluation(
        self,
        report_id: UUID,
        request: ReviewEvaluationRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> EvaluationReview:
        return await self._request(
            "review_evaluation",
            response_model=EvaluationReview,
            path_params={"report_id": report_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def promote_agent(
        self,
        instance_id: UUID,
        request: PromoteAgentRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentInstance:
        return await self._request(
            "promote_agent",
            response_model=AgentInstance,
            path_params={"instance_id": instance_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def record_task_outcome(
        self,
        instance_id: UUID,
        request: RecordTaskOutcomeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> DegradationCheckResult:
        return await self._request(
            "record_task_outcome",
            response_model=DegradationCheckResult,
            path_params={"instance_id": instance_id},
            body=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def query_audit(
        self,
        *,
        entity_type: AuditEntityType | None = None,
        entity_id: str | None = None,
        event_types: tuple[AuditEventType, ...] = (),
        actor: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        correlation_id: UUID | None = None,
    ) -> Page[AuditEvent]:
        params: QueryParams = [
            ("page", str(page)),
            ("page_size", str(page_size)),
        ]
        if entity_type is not None:
            params.append(("entity_type", entity_type.value))
        if entity_id is not None:
            params.append(("entity_id", entity_id))
        params.extend(("event_type", item.value) for item in event_types)
        if actor is not None:
            params.append(("actor", actor))
        if created_from is not None:
            params.append(("created_from", created_from.isoformat()))
        if created_to is not None:
            params.append(("created_to", created_to.isoformat()))
        return await self._request(
            "query_audit",
            response_model=Page[AuditEvent],
            params=params,
            correlation_id=correlation_id,
        )

    async def _request(
        self,
        operation_name: str,
        *,
        response_model: type[ResponseModelT],
        path_params: dict[str, object] | None = None,
        body: BaseModel | None = None,
        params: QueryParams | None = None,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> ResponseModelT:
        self._ensure_open()
        operation = SDK_OPERATIONS[operation_name]
        request_correlation_id = correlation_id or uuid4()
        headers = self._headers(
            operation,
            request_correlation_id,
            idempotency_key=idempotency_key,
        )
        path = self._path(operation, path_params or {})

        try:
            if body is None:
                response = await self._client.request(
                    operation.method,
                    path,
                    headers=headers,
                    params=params,
                )
            else:
                response = await self._client.request(
                    operation.method,
                    path,
                    headers=headers,
                    params=params,
                    json=body.model_dump(mode="json"),
                )
        except httpx.RequestError as exc:
            raise AgentFactoryTransportError(
                correlation_id=request_correlation_id,
                cause_type=type(exc).__name__,
            ) from None

        if response.is_success:
            self._validate_response_correlation(response, request_correlation_id)
            try:
                payload = response.json()
                return response_model.model_validate(payload)
            except (TypeError, ValueError, ValidationError):
                raise AgentFactoryProtocolError(
                    status_code=response.status_code,
                    correlation_id=request_correlation_id,
                ) from None

        self._raise_api_error(response, request_correlation_id)

    def _headers(
        self,
        operation: SdkOperation,
        correlation_id: UUID,
        *,
        idempotency_key: str | None,
    ) -> dict[str, str]:
        headers = {"X-Correlation-ID": str(correlation_id)}
        if operation.authenticated:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _path(
        self,
        operation: SdkOperation,
        path_params: dict[str, object],
    ) -> str:
        encoded = {
            key: quote(str(value), safe="") for key, value in path_params.items()
        }
        rendered = operation.path.format_map(encoded)
        if operation.api_scoped:
            rendered = f"{self._api_prefix}{rendered}"
        return rendered.lstrip("/")

    @staticmethod
    def _validate_response_correlation(
        response: httpx.Response,
        expected: UUID,
    ) -> None:
        value = response.headers.get("X-Correlation-ID")
        try:
            actual = UUID(value) if value is not None else None
        except ValueError:
            actual = None
        if actual != expected:
            raise AgentFactoryProtocolError(
                status_code=response.status_code,
                correlation_id=expected,
            )

    @staticmethod
    def _raise_api_error(response: httpx.Response, expected: UUID) -> NoReturn:
        try:
            body = ErrorResponse.model_validate(response.json())
        except (TypeError, ValueError, ValidationError):
            raise AgentFactoryApiError(
                status_code=response.status_code,
                code="SDK_HTTP_ERROR",
                message="Agent Factory returned a non-standard error response",
                details=None,
                correlation_id=expected,
            ) from None

        AgentFactoryClient._validate_response_correlation(response, expected)
        if body.error.correlation_id != expected:
            raise AgentFactoryProtocolError(
                status_code=response.status_code,
                correlation_id=expected,
            )
        raise AgentFactoryApiError(
            status_code=response.status_code,
            code=body.error.code,
            message=body.error.message,
            details=body.error.details,
            correlation_id=body.error.correlation_id,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AgentFactoryClientClosedError


def _normalize_base_url(value: str) -> str:
    try:
        url = httpx.URL(value)
    except (TypeError, ValueError):
        raise ValueError("base_url must be a valid HTTP or HTTPS URL") from None
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "base_url must contain only an HTTP(S) origin and optional path"
        )
    path = url.path.rstrip("/") + "/"
    return str(url.copy_with(path=path))


def _normalize_api_prefix(value: str) -> str:
    raw = value.strip()
    if not raw.startswith("/"):
        raise ValueError("api_prefix must be an absolute path")
    normalized = "/" + raw.strip("/")
    if _API_PREFIX_PATTERN.fullmatch(normalized) is None:
        raise ValueError("api_prefix must contain URL-safe path segments")
    return "" if normalized == "/" else normalized


def _validate_token(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise ValueError("token must be non-empty and contain no whitespace")
    return value
