"""Real FastAPI and SQLite workflows driven exclusively through the SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import (
    AuditEventType,
    EvaluationDecision,
    InstanceStatus,
    PrototypeStatus,
    ReviewDecision,
)
from agent_factory.domain.references import EvaluationSuiteRef
from agent_factory.interfaces.api.main import create_app
from agent_factory.sdk import (
    AgentFactoryApiError,
    AgentFactoryClient,
    BindKnowledgeRequest,
    CloneAgentRequest,
    DeprecatePrototypeRequest,
    EvaluateInstanceRequest,
    ExportSpecRequest,
    PromoteAgentRequest,
    RecordTaskOutcomeRequest,
    RegisterEvaluationSuiteRequest,
    RegisterKnowledgeRequest,
    RegisterPrototypeRequest,
    RegisterSkillTreeRequest,
    ReviewEvaluationRequest,
    TransitionInstanceRequest,
)
from agent_factory.settings import Settings

AUTH_TOKEN = "sdk-integration-token-that-is-at-least-32-characters"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    api_prefix: str = "/api/v1",
    roles: tuple[str, ...] = ("admin",),
) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
            "auth_subject": "sdk-owner",
            "auth_roles": roles,
            "api_prefix": api_prefix,
        }
    )


@asynccontextmanager
async def _running_sdk(
    settings: Settings,
    *,
    token: str = AUTH_TOKEN,
) -> AsyncIterator[AgentFactoryClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with AgentFactoryClient(
            base_url="http://testserver",
            api_prefix=settings.api_prefix,
            token=token,
            transport=transport,
        ) as client:
            yield client


def _suite_request() -> RegisterEvaluationSuiteRequest:
    return RegisterEvaluationSuiteRequest.model_validate(
        {
            "suite_id": "engineer-readiness",
            "version": "1.0.0",
            "rules": [
                {
                    "rule_id": "mentions-pytest",
                    "kind": "required-terms",
                    "parameters": {"terms": ["pytest"]},
                }
            ],
            "cases": [
                {
                    "case_id": "testing-strategy",
                    "input": "Describe the project's testing strategy.",
                }
            ],
            "minimum_soft_score": 0.8,
            "require_manual_review": True,
        }
    )


def _tree_request(
    suite: EvaluationSuiteRef,
) -> RegisterSkillTreeRequest:
    return RegisterSkillTreeRequest.model_validate(
        {
            "tree_id": "engineer-skills",
            "version": "1.0.0",
            "nodes": [
                {
                    "node_id": "junior-engineer",
                    "display_name": "Junior Engineer",
                    "parents": [],
                    "prompt_appendix": "Apply the project testing policy.",
                    "granted_tools": ["document-search"],
                    "added_knowledge_slots": [
                        {
                            "name": "engineering-guide",
                            "required": True,
                            "accepted_kinds": ["document"],
                            "min_version": "1.0.0",
                            "injection_mode": "retrieval",
                            "multiple": False,
                            "max_items": 1,
                        }
                    ],
                    "evaluation_suite": suite.model_dump(mode="json"),
                    "observation_policy": {
                        "window_size": 4,
                        "minimum_samples": 3,
                        "consecutive_failures": 2,
                        "failure_rate_threshold": 0.75,
                    },
                }
            ],
        }
    )


def _prototype_request(tree: object) -> RegisterPrototypeRequest:
    return RegisterPrototypeRequest.model_validate(
        {
            "prototype_id": "engineer-agent",
            "version": "1.0.0",
            "definition": {
                "agent_type": "engineer-agent",
                "role": "Software Engineer",
                "system_prompt": "Produce technically verifiable engineering work.",
                "tools": [],
                "capabilities": ["can-code"],
                "output_schema": {"type": "object"},
                "knowledge_slots": [
                    {
                        "name": "product-docs",
                        "required": True,
                        "accepted_kinds": ["document"],
                        "min_version": "1.0.0",
                        "injection_mode": "retrieval",
                        "multiple": False,
                        "max_items": 1,
                    }
                ],
                "metadata": {},
            },
            "skill_tree": tree,
            "publish": False,
        }
    )


def _knowledge_request(
    knowledge_id: str,
    name: str,
    content: str,
) -> RegisterKnowledgeRequest:
    return RegisterKnowledgeRequest.model_validate(
        {
            "knowledge_id": knowledge_id,
            "version": "1.0.0",
            "name": name,
            "kind": "document",
            "content": content,
            "checksum": checksum_knowledge_content(content),
        }
    )


def _evaluation_request(
    *,
    revision: int,
    suite: EvaluationSuiteRef,
) -> EvaluateInstanceRequest:
    return EvaluateInstanceRequest.model_validate(
        {
            "expected_revision": revision,
            "suite": suite.model_dump(mode="json"),
            "runtime_model": "sdk-test-model",
            "case_results": [
                {
                    "case_id": "testing-strategy",
                    "output_text": "Use pytest with deterministic fixtures.",
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_sdk_executes_every_public_operation_against_real_app(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    suite_request = _suite_request()

    async with _running_sdk(settings) as client:
        assert (await client.check_liveness()).status == "ok"
        assert (await client.check_readiness()).status == "ok"

        suite = await client.register_evaluation_suite(
            suite_request,
            idempotency_key="sdk-register-suite-1",
        )
        suite_replay = await client.register_evaluation_suite(
            suite_request,
            idempotency_key="sdk-register-suite-1",
        )
        assert suite_replay == suite
        assert (
            await client.get_evaluation_suite(suite.suite_id, suite.version)
        ) == suite
        suite_ref = EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        )

        tree = await client.register_skill_tree(
            _tree_request(suite_ref),
            idempotency_key="sdk-register-tree-1",
        )
        assert await client.get_skill_tree(tree.tree_id, tree.version) == tree

        prototype_request = _prototype_request(
            {
                "tree_id": tree.tree_id,
                "version": tree.version,
                "checksum": tree.checksum,
            }
        )
        draft = await client.register_prototype(
            prototype_request,
            idempotency_key="sdk-register-prototype-1",
        )
        page = await client.list_prototypes(
            status=PrototypeStatus.DRAFT,
            agent_type="engineer-agent",
            page=1,
            page_size=10,
        )
        assert page.items == (draft,)

        published = await client.publish_prototype(
            draft.prototype_id,
            draft.version,
            idempotency_key="sdk-publish-prototype-1",
        )
        assert published.status is PrototypeStatus.PUBLISHED

        product_docs = await client.register_knowledge(
            _knowledge_request(
                "product-docs",
                "Product Docs",
                "Agent Factory produces governed Agent specifications.",
            ),
            idempotency_key="sdk-register-product-docs-1",
        )
        engineering_guide = await client.register_knowledge(
            _knowledge_request(
                "engineering-guide",
                "Engineering Guide",
                "Use pytest, deterministic fixtures, and layered tests.",
            ),
            idempotency_key="sdk-register-engineering-guide-1",
        )

        instance = await client.clone_agent(
            published.prototype_id,
            published.version,
            CloneAgentRequest(),
            idempotency_key="sdk-clone-agent-1",
        )
        bound = await client.bind_knowledge(
            instance.instance_id,
            BindKnowledgeRequest.model_validate(
                {
                    "expected_revision": 1,
                    "selections": [
                        {
                            "slot_name": "product-docs",
                            "knowledge_id": product_docs.knowledge_id,
                            "version": product_docs.version,
                        }
                    ],
                }
            ),
            idempotency_key="sdk-bind-product-docs-1",
        )
        assert bound.revision == 2

        spec_two = await client.export_spec(
            instance.instance_id,
            ExportSpecRequest(revision=2),
        )
        assert spec_two.revision == 2

        running = await client.transition_instance(
            instance.instance_id,
            TransitionInstanceRequest(
                expected_revision=2,
                target_status=InstanceStatus.RUNNING,
                reason="Start SDK integration task",
            ),
            idempotency_key="sdk-transition-running-1",
        )
        assert running.revision == 3
        assert running.status is InstanceStatus.RUNNING

        waiting = await client.transition_instance(
            instance.instance_id,
            TransitionInstanceRequest(
                expected_revision=3,
                target_status=InstanceStatus.WAITING,
                reason="Runtime evidence is ready for evaluation",
            ),
            idempotency_key="sdk-transition-waiting-1",
        )
        assert waiting.revision == 4
        assert waiting.status is InstanceStatus.WAITING

        report = await client.evaluate_instance(
            instance.instance_id,
            _evaluation_request(revision=4, suite=suite_ref),
            idempotency_key="sdk-evaluate-promotion-1",
        )
        assert report.decision is EvaluationDecision.REVIEW_REQUIRED
        review = await client.review_evaluation(
            report.report_id,
            ReviewEvaluationRequest(
                decision=ReviewDecision.APPROVED,
                comment="Reviewed through the SDK contract.",
            ),
            idempotency_key="sdk-review-promotion-1",
        )

        promoted = await client.promote_agent(
            instance.instance_id,
            PromoteAgentRequest.model_validate(
                {
                    "expected_revision": 4,
                    "target_node_id": "junior-engineer",
                    "evaluation_report_id": report.report_id,
                    "evaluation_review_id": review.review_id,
                    "knowledge_selections": [
                        {
                            "slot_name": "engineering-guide",
                            "knowledge_id": engineering_guide.knowledge_id,
                            "version": engineering_guide.version,
                        }
                    ],
                }
            ),
            idempotency_key="sdk-promote-agent-1",
        )
        assert promoted.revision == 5

        observation_report = await client.evaluate_instance(
            instance.instance_id,
            _evaluation_request(revision=5, suite=suite_ref),
            idempotency_key="sdk-evaluate-observation-1",
        )
        await client.review_evaluation(
            observation_report.report_id,
            ReviewEvaluationRequest(
                decision=ReviewDecision.APPROVED,
                comment="Observation evidence approved.",
            ),
            idempotency_key="sdk-review-observation-1",
        )
        outcome = await client.record_task_outcome(
            instance.instance_id,
            RecordTaskOutcomeRequest(
                expected_revision=5,
                task_id=UUID("00000000-0000-0000-0000-000000000801"),
                skill_node_id="junior-engineer",
                passed=True,
                evaluation_report_id=observation_report.report_id,
            ),
            idempotency_key="sdk-record-outcome-1",
        )
        assert outcome.degraded is False
        assert outcome.resulting_revision == 5

        audit = await client.query_audit(
            event_types=(
                AuditEventType.PROTOTYPE_REGISTERED,
                AuditEventType.INSTANCE_TRANSITIONED,
            ),
            page=1,
            page_size=100,
        )
        assert {event.event_type for event in audit.items} == {
            AuditEventType.PROTOTYPE_REGISTERED,
            AuditEventType.INSTANCE_TRANSITIONED,
        }
        assert (
            sum(
                event.event_type is AuditEventType.EVALUATION_SUITE_REGISTERED
                for event in (await client.query_audit(page_size=100)).items
            )
            == 1
        )

        resumed = await client.transition_instance(
            instance.instance_id,
            TransitionInstanceRequest(
                expected_revision=5,
                target_status=InstanceStatus.RUNNING,
                reason="Resume after governance review",
            ),
            idempotency_key="sdk-transition-resumed-1",
        )
        assert resumed.revision == 6

        completed = await client.transition_instance(
            instance.instance_id,
            TransitionInstanceRequest(
                expected_revision=6,
                target_status=InstanceStatus.COMPLETED,
                reason="SDK integration task completed",
            ),
            idempotency_key="sdk-transition-completed-1",
        )
        assert completed.revision == 7

        deprecated = await client.deprecate_prototype(
            published.prototype_id,
            published.version,
            DeprecatePrototypeRequest(reason="SDK integration test complete"),
            idempotency_key="sdk-deprecate-prototype-1",
        )
        assert deprecated.status is PrototypeStatus.DEPRECATED


@pytest.mark.asyncio
async def test_sdk_supports_custom_prefix_and_preserves_real_api_errors(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir, api_prefix="/factory/v2")

    async with _running_sdk(settings) as client:
        assert (await client.check_readiness()).status == "ok"
        assert (await client.list_prototypes()).total == 0
        with pytest.raises(AgentFactoryApiError) as captured:
            await client.get_skill_tree(
                "missing-tree",
                "1.0.0",
                correlation_id=UUID("00000000-0000-0000-0000-000000000802"),
            )

    assert captured.value.status_code == 404
    assert captured.value.code == "SKILL_TREE_NOT_FOUND"
    assert captured.value.correlation_id == UUID("00000000-0000-0000-0000-000000000802")

    async with _running_sdk(
        settings, token="wrong-token-that-is-at-least-32-characters"
    ) as client:
        with pytest.raises(AgentFactoryApiError) as unauthorized:
            await client.list_prototypes()

    assert unauthorized.value.status_code == 401
    assert unauthorized.value.code == "AUTHENTICATION_FAILED"
    assert "wrong-token" not in str(unauthorized.value)

    viewer_settings = _settings(
        tmp_path,
        migrations_dir,
        api_prefix="/factory/v2",
        roles=("viewer",),
    )
    async with _running_sdk(viewer_settings) as client:
        with pytest.raises(AgentFactoryApiError) as forbidden:
            await client.register_evaluation_suite(
                _suite_request(),
                idempotency_key="sdk-forbidden-write-1",
            )

    assert forbidden.value.status_code == 403
    assert forbidden.value.code == "AUTHORIZATION_DENIED"
    assert forbidden.value.details["required_permission"] == "factory:write"
