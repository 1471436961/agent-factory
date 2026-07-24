"""Real FastAPI, SDK, SQLite and Runtime evidence for the M3.6 workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_factory.application.runtime import RunRequest, RunResult
from agent_factory.domain.models import DomainKnowledge
from agent_factory.interfaces.api.contracts import RegisterKnowledgeRequest
from agent_factory.interfaces.api.main import create_app
from agent_factory.interfaces.demo.contracts import DemoPhase, DemoSession
from agent_factory.interfaces.demo.fixtures import KNOWLEDGE_CONTENT
from agent_factory.interfaces.demo.workflow import DemoWorkflow
from agent_factory.sdk import (
    AgentFactoryClient,
    AgentFactoryTransportError,
)
from agent_factory.settings import Settings

AUTH_TOKEN = "m3-demo-test-token-that-is-at-least-32-characters"


def _settings(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    database_name: str = "demo.db",
) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / database_name).as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
            "auth_subject": "demo-owner",
            "auth_roles": ["admin"],
        }
    )


def _client_factory(app: FastAPI) -> AgentFactoryClient:
    return AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
    )


@pytest.mark.asyncio
async def test_fixed_demo_reaches_reviewed_revision_five_from_empty_database(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    app = create_app(_settings(tmp_path, migrations_dir))
    async with app.router.lifespan_context(app):
        workflow = DemoWorkflow(
            client_factory=lambda: _client_factory(app),
            runtime=app.state.container.demo_runtime,
        )

        initialized = await workflow.initialize_factory(DemoSession())
        assert initialized.error is None
        assert initialized.session.phase is DemoPhase.READY_TO_RUN
        assert initialized.session.revision == 3
        assert {source.source_type for source in initialized.session.sources} == {
            "prototype",
            "knowledge",
            "skill-tree",
            "evaluation-suite",
        }

        evaluated = await workflow.run_and_evaluate(initialized.session)
        assert evaluated.error is None
        assert evaluated.session.phase is DemoPhase.AWAITING_REVIEW
        assert evaluated.session.revision == 4
        assert evaluated.session.report_id is not None
        assert evaluated.session.run_view is not None
        assert evaluated.session.run_view.status == "completed"
        assert evaluated.session.run_view.tool_call_count == 1
        assert KNOWLEDGE_CONTENT not in evaluated.session.run_view.content_preview
        assert "Verified knowledge" not in evaluated.session.run_view.content_preview

        promoted = await workflow.approve_and_promote(evaluated.session)
        assert promoted.error is None
        assert promoted.session.phase is DemoPhase.PROMOTED
        assert promoted.session.revision == 5
        assert promoted.session.active_nodes == ("mid-writer",)
        assert promoted.session.review_id is not None

        event_types = [row.event_type for row in promoted.session.audit_rows]
        assert {
            "evaluation-suite.registered",
            "skill-tree.registered",
            "prototype.registered",
            "prototype.published",
            "knowledge.registered",
            "instance.cloned",
            "knowledge.bound",
            "spec.exported",
            "instance.transitioned",
            "tool.called",
            "evaluation.completed",
            "evaluation.reviewed",
            "skill.promoted",
        } <= set(event_types)

        invalid_repeat = await workflow.approve_and_promote(promoted.session)
        assert invalid_repeat.error is not None
        assert invalid_repeat.error.code == "DEMO_INVALID_PHASE"
        assert invalid_repeat.session == promoted.session


class _FailOnceKnowledgeClient(AgentFactoryClient):
    failures: list[bool]

    async def register_knowledge(
        self,
        request: RegisterKnowledgeRequest,
        *,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> DomainKnowledge:
        if self.failures and self.failures.pop():
            raise AgentFactoryTransportError(
                correlation_id=correlation_id or uuid4(),
                cause_type="InjectedFailure",
            )
        return await super().register_knowledge(
            request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )


@pytest.mark.asyncio
async def test_initialize_resumes_from_checkpoint_without_duplicate_audit(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    app = create_app(_settings(tmp_path, migrations_dir, database_name="retry.db"))
    failures = [True]

    def client_factory() -> AgentFactoryClient:
        client = _FailOnceKnowledgeClient(
            base_url="http://testserver",
            token=AUTH_TOKEN,
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        )
        client.failures = failures
        return client

    async with app.router.lifespan_context(app):
        workflow = DemoWorkflow(
            client_factory=client_factory,
            runtime=app.state.container.demo_runtime,
        )

        failed = await workflow.initialize_factory(DemoSession())
        assert failed.error is not None
        assert failed.error.code == "DEMO_API_UNAVAILABLE"
        assert failed.session.is_completed("prototype.published") is True
        assert failed.session.is_completed("knowledge.registered") is False

        resumed = await workflow.initialize_factory(failed.session)
        assert resumed.error is None
        assert resumed.session.phase is DemoPhase.READY_TO_RUN
        event_types = [row.event_type for row in resumed.session.audit_rows]
        assert event_types.count("evaluation-suite.registered") == 1
        assert event_types.count("skill-tree.registered") == 1
        assert event_types.count("prototype.registered") == 1
        assert event_types.count("prototype.published") == 1
        assert event_types.count("knowledge.registered") == 1
        assert event_types.count("instance.cloned") == 1


class _ExplodingRuntime:
    async def run(self, request: RunRequest) -> RunResult:
        del request
        raise RuntimeError("secret-runtime-detail")


@pytest.mark.asyncio
async def test_unexpected_runtime_error_is_redacted_from_result_and_log(
    tmp_path: Path,
    migrations_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(_settings(tmp_path, migrations_dir, database_name="redact.db"))
    async with app.router.lifespan_context(app):
        initialize_workflow = DemoWorkflow(
            client_factory=lambda: _client_factory(app),
            runtime=app.state.container.demo_runtime,
        )
        initialized = await initialize_workflow.initialize_factory(DemoSession())
        assert initialized.error is None

        caplog.set_level(logging.ERROR, logger="agent_factory.demo")
        failing_workflow = DemoWorkflow(
            client_factory=lambda: _client_factory(app),
            runtime=_ExplodingRuntime(),
        )
        failed = await failing_workflow.run_and_evaluate(initialized.session)

    assert failed.error is not None
    assert failed.error.code == "DEMO_INTERNAL_ERROR"
    assert "secret-runtime-detail" not in failed.error.message
    assert "secret-runtime-detail" not in caplog.text
    assert any(
        getattr(record, "exception_type", None) == "RuntimeError"
        for record in caplog.records
    )
