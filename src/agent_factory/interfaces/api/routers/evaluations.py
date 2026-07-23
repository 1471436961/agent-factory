"""Evaluation suite registration, lookup, and final review routes."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter

from agent_factory.application.commands import (
    RegisterEvaluationSuiteCommand,
    ReviewEvaluationCommand,
)
from agent_factory.domain.common import SemVer, Slug
from agent_factory.domain.evaluation import (
    EvaluationReview,
    EvaluationSuite,
    EvaluationSuiteDraft,
)
from agent_factory.interfaces.api.contracts import (
    RegisterEvaluationSuiteRequest,
    ReviewEvaluationRequest,
)
from agent_factory.interfaces.api.dependencies import (
    ControllerDep,
    FactoryReadPrincipalDep,
    FactoryWritePrincipalDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(tags=["evaluations"])


@router.post(
    "/evaluation-suites",
    response_model=EvaluationSuite,
    status_code=HTTPStatus.CREATED,
)
async def register_evaluation_suite(
    body: RegisterEvaluationSuiteRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> EvaluationSuite:
    command = validate_command(
        RegisterEvaluationSuiteCommand,
        {
            "suite": EvaluationSuiteDraft.model_validate(
                body.model_dump(mode="python")
            ),
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.register_evaluation_suite(command)


@router.get(
    "/evaluation-suites/{suite_id}/versions/{version}",
    response_model=EvaluationSuite,
)
async def get_evaluation_suite(
    suite_id: Slug,
    version: SemVer,
    controller: ControllerDep,
    _principal: FactoryReadPrincipalDep,
) -> EvaluationSuite:
    return await controller.get_evaluation_suite(suite_id, version)


@router.post(
    "/evaluation-reports/{report_id}/reviews",
    response_model=EvaluationReview,
    status_code=HTTPStatus.CREATED,
)
async def review_evaluation(
    report_id: UUID,
    body: ReviewEvaluationRequest,
    controller: ControllerDep,
    principal: FactoryWritePrincipalDep,
    idempotency_key: IdempotencyHeader = None,
) -> EvaluationReview:
    command = validate_command(
        ReviewEvaluationCommand,
        {
            "report_id": report_id,
            "decision": body.decision,
            "comment": body.comment,
            "actor": principal.subject,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.review_evaluation(command)
