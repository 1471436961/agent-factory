"""Stable HTTP error envelopes and transport-specific status mapping."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from types import MappingProxyType
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_factory.domain.errors import FactoryError
from agent_factory.interfaces.api.contracts import ErrorBody, ErrorResponse

logger = logging.getLogger("agent_factory.api")

ERROR_STATUS_BY_CODE: Mapping[str, int] = MappingProxyType(
    {
        "INVALID_OUTPUT_SCHEMA": HTTPStatus.BAD_REQUEST,
        "PROTOTYPE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "PROTOTYPE_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "PROTOTYPE_NOT_PUBLISHED": HTTPStatus.CONFLICT,
        "INVALID_PROTOTYPE_STATUS": HTTPStatus.CONFLICT,
        "KNOWLEDGE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "KNOWLEDGE_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "KNOWLEDGE_ALREADY_BOUND": HTTPStatus.CONFLICT,
        "UNKNOWN_KNOWLEDGE_SLOT": HTTPStatus.UNPROCESSABLE_ENTITY,
        "MISSING_KNOWLEDGE_BINDING": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_KIND_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_VERSION_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_INJECTION_MODE_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_CARDINALITY_INVALID": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_CHECKSUM_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "KNOWLEDGE_PAYLOAD_TOO_LARGE": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        "INSTANCE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "INSTANCE_BUSY": HTTPStatus.CONFLICT,
        "INSTANCE_NOT_READY": HTTPStatus.UNPROCESSABLE_ENTITY,
        "INVALID_STATE_TRANSITION": HTTPStatus.CONFLICT,
        "REVISION_CONFLICT": HTTPStatus.CONFLICT,
        "IDEMPOTENCY_KEY_REUSED": HTTPStatus.CONFLICT,
        "TOOL_NOT_GRANTED": HTTPStatus.FORBIDDEN,
        "UNKNOWN_TOOL": HTTPStatus.UNPROCESSABLE_ENTITY,
        "TOOL_PERMISSION_DENIED": HTTPStatus.FORBIDDEN,
        "SKILL_TREE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "SKILL_TREE_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "SKILL_TREE_NOT_BOUND": HTTPStatus.UNPROCESSABLE_ENTITY,
        "SKILL_NODE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "SKILL_DEPENDENCY_MISSING": HTTPStatus.UNPROCESSABLE_ENTITY,
        "SKILL_ALREADY_ACTIVE": HTTPStatus.CONFLICT,
        "SKILL_NOT_ACTIVE": HTTPStatus.CONFLICT,
        "SKILL_CONFIGURATION_CONFLICT": HTTPStatus.CONFLICT,
        "SKILL_TREE_CYCLE": HTTPStatus.UNPROCESSABLE_ENTITY,
        "EVALUATION_SUITE_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "EVALUATION_SUITE_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "EVALUATION_REPORT_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "EVALUATION_REPORT_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "EVALUATION_SUITE_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "EVALUATION_SUBMISSION_INVALID": HTTPStatus.UNPROCESSABLE_ENTITY,
        "EVALUATION_RULE_TIMEOUT": HTTPStatus.UNPROCESSABLE_ENTITY,
        "EVALUATION_REVIEW_CONFLICT": HTTPStatus.CONFLICT,
        "EVALUATION_REVIEW_NOT_REQUIRED": HTTPStatus.CONFLICT,
        "TASK_OUTCOME_ALREADY_EXISTS": HTTPStatus.CONFLICT,
        "TASK_OUTCOME_MISMATCH": HTTPStatus.UNPROCESSABLE_ENTITY,
        "STALE_EVALUATION_REPORT": HTTPStatus.CONFLICT,
        "PROMOTION_REJECTED": HTTPStatus.UNPROCESSABLE_ENTITY,
        "REPOSITORY_UNAVAILABLE": HTTPStatus.SERVICE_UNAVAILABLE,
    }
)


class ApiContractError(Exception):
    """Transport-only error used before a domain command is constructed."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def correlation_id_for(request: Request) -> UUID:
    value = getattr(request.state, "correlation_id", None)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    return uuid4()


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    correlation_id: UUID,
    details: Mapping[str, object] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or {},
            correlation_id=correlation_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FactoryError)
    async def handle_factory_error(
        request: Request,
        exc: FactoryError,
    ) -> JSONResponse:
        status_code = ERROR_STATUS_BY_CODE.get(exc.code)
        if status_code is None:
            logger.error(
                "unmapped_factory_error",
                extra={
                    "correlation_id": str(correlation_id_for(request)),
                    "error_code": exc.code,
                },
            )
            return _internal_error(request)
        return error_response(
            status_code=status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            correlation_id=correlation_id_for(request),
        )

    @app.exception_handler(ApiContractError)
    async def handle_api_contract_error(
        request: Request,
        exc: ApiContractError,
    ) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            correlation_id=correlation_id_for(request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        safe_errors = [
            {
                "location": list(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return error_response(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed",
            details={"errors": safe_errors},
            correlation_id=correlation_id_for(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            code, message = "ROUTE_NOT_FOUND", "Route was not found"
        elif exc.status_code == HTTPStatus.METHOD_NOT_ALLOWED:
            code, message = "METHOD_NOT_ALLOWED", "HTTP method is not allowed"
        else:
            code, message = "HTTP_ERROR", "HTTP request failed"
        return error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            correlation_id=correlation_id_for(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        correlation_id = correlation_id_for(request)
        logger.exception(
            "unhandled_error",
            extra={"correlation_id": str(correlation_id)},
        )
        return _internal_error(request, correlation_id=correlation_id)


def _internal_error(
    request: Request,
    *,
    correlation_id: UUID | None = None,
) -> JSONResponse:
    return error_response(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="Internal server error",
        correlation_id=correlation_id or correlation_id_for(request),
    )
