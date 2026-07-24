"""Cross-process exit evidence for the complete M3 public workflow.

This test deliberately composes the real FastAPI application, asynchronous SDK,
file-backed SQLite database and deterministic Demo Runtime. It verifies contracts
that are weaker when tested inside one process: exact idempotent replay after a
restart, audit recovery, and stable revision-5 AgentSpec reconstruction.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from agent_factory.domain.enums import AuditEventType, InstanceStatus
from agent_factory.domain.models import AgentInstance
from agent_factory.interfaces.api.main import create_app
from agent_factory.interfaces.demo.contracts import DemoPhase, DemoSession
from agent_factory.interfaces.demo.fixtures import (
    KNOWLEDGE_ID,
    KNOWLEDGE_VERSION,
    PROTOTYPE_ID,
    PROTOTYPE_VERSION,
    SUITE_ID,
    SUITE_VERSION,
    TARGET_NODE_ID,
    TOOL_NAME,
    TREE_ID,
    TREE_VERSION,
    promotion_request,
)
from agent_factory.interfaces.demo.workflow import DemoWorkflow
from agent_factory.sdk import AgentFactoryClient, ExportSpecRequest
from agent_factory.settings import Settings

AUTH_TOKEN = "m3-exit-token-that-is-at-least-32-characters"
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000003700")


def _settings(tmp_path: Path, migrations_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'm3-exit.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
            "auth_subject": "m3-exit-owner",
            "auth_roles": ["admin"],
        }
    )


def _client(app: FastAPI) -> AgentFactoryClient:
    return AgentFactoryClient(
        base_url="http://testserver",
        token=AUTH_TOKEN,
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
    )


async def _replay_promotion(
    client: AgentFactoryClient,
    session: DemoSession,
) -> AgentInstance:
    assert session.instance_id is not None
    assert session.report_id is not None
    assert session.review_id is not None
    return await client.promote_agent(
        session.instance_id,
        promotion_request(
            expected_revision=4,
            report_id=session.report_id,
            review_id=session.review_id,
        ),
        idempotency_key=f"demo:{session.workflow_id}:skill.promoted",
        correlation_id=session.workflow_id,
    )


@pytest.mark.asyncio
async def test_m3_public_workflow_survives_two_process_rebuilds(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    first_app = create_app(settings)

    async with first_app.router.lifespan_context(first_app):
        workflow = DemoWorkflow(
            client_factory=lambda: _client(first_app),
            runtime=first_app.state.container.demo_runtime,
        )
        initialized = await workflow.initialize_factory(
            DemoSession(workflow_id=WORKFLOW_ID)
        )
        assert initialized.error is None
        evaluated = await workflow.run_and_evaluate(initialized.session)
        assert evaluated.error is None
        promoted = await workflow.approve_and_promote(evaluated.session)
        assert promoted.error is None
        assert promoted.session.phase is DemoPhase.PROMOTED
        assert promoted.session.revision == 5

        async with _client(first_app) as client:
            promotion_before_restart = await _replay_promotion(client, promoted.session)
            suite_before_restart = await client.get_evaluation_suite(
                SUITE_ID,
                SUITE_VERSION,
                correlation_id=WORKFLOW_ID,
            )
            tree_before_restart = await client.get_skill_tree(
                TREE_ID,
                TREE_VERSION,
                correlation_id=WORKFLOW_ID,
            )
            prototypes_before_restart = await client.list_prototypes(
                page=1,
                page_size=100,
                correlation_id=WORKFLOW_ID,
            )
            audit_before_restart = await client.query_audit(
                page=1,
                page_size=100,
                correlation_id=WORKFLOW_ID,
            )

    second_app = create_app(settings)
    async with second_app.router.lifespan_context(second_app):
        async with _client(second_app) as client:
            assert await client.check_readiness(correlation_id=WORKFLOW_ID)
            assert (
                await client.get_evaluation_suite(
                    SUITE_ID,
                    SUITE_VERSION,
                    correlation_id=WORKFLOW_ID,
                )
                == suite_before_restart
            )
            assert (
                await client.get_skill_tree(
                    TREE_ID,
                    TREE_VERSION,
                    correlation_id=WORKFLOW_ID,
                )
                == tree_before_restart
            )
            assert (
                await client.list_prototypes(
                    page=1,
                    page_size=100,
                    correlation_id=WORKFLOW_ID,
                )
                == prototypes_before_restart
            )
            assert (
                await _replay_promotion(client, promoted.session)
                == promotion_before_restart
            )
            assert (
                await client.query_audit(
                    page=1,
                    page_size=100,
                    correlation_id=WORKFLOW_ID,
                )
                == audit_before_restart
            )

            assert promoted.session.instance_id is not None
            revision_five_spec = await client.export_spec(
                promoted.session.instance_id,
                ExportSpecRequest(revision=5),
                correlation_id=WORKFLOW_ID,
            )
            sources = {
                source.source_type: source for source in promoted.session.sources
            }
            assert revision_five_spec.revision == 5
            assert revision_five_spec.instance_id == promoted.session.instance_id
            assert revision_five_spec.prototype.prototype_id == PROTOTYPE_ID
            assert revision_five_spec.prototype.version == PROTOTYPE_VERSION
            assert (
                revision_five_spec.prototype.checksum == sources["prototype"].checksum
            )
            assert revision_five_spec.active_skill_nodes == frozenset({TARGET_NODE_ID})
            assert revision_five_spec.skill_tree is not None
            assert revision_five_spec.skill_tree.tree_id == TREE_ID
            assert revision_five_spec.skill_tree.version == TREE_VERSION
            assert (
                revision_five_spec.skill_tree.checksum == sources["skill-tree"].checksum
            )
            assert len(revision_five_spec.knowledge) == 1
            assert revision_five_spec.knowledge[0].knowledge_id == KNOWLEDGE_ID
            assert revision_five_spec.knowledge[0].version == KNOWLEDGE_VERSION
            assert (
                revision_five_spec.knowledge[0].checksum
                == sources["knowledge"].checksum
            )
            assert tuple(tool.name for tool in revision_five_spec.tools) == (TOOL_NAME,)

            audit_with_revision_five_spec = await client.query_audit(
                page=1,
                page_size=100,
                correlation_id=WORKFLOW_ID,
            )
            assert audit_with_revision_five_spec.total == (
                audit_before_restart.total + 1
            )
            revision_five_exports = tuple(
                event
                for event in audit_with_revision_five_spec.items
                if event.event_type is AuditEventType.SPEC_EXPORTED
                and event.entity_revision == 5
            )
            assert len(revision_five_exports) == 1

    third_app = create_app(settings)
    async with third_app.router.lifespan_context(third_app):
        async with _client(third_app) as client:
            replayed_spec = await client.export_spec(
                promotion_before_restart.instance_id,
                ExportSpecRequest(revision=5),
                correlation_id=WORKFLOW_ID,
            )
            assert replayed_spec == revision_five_spec
            assert (
                await client.query_audit(
                    page=1,
                    page_size=100,
                    correlation_id=WORKFLOW_ID,
                )
                == audit_with_revision_five_spec
            )

    assert promotion_before_restart.status is InstanceStatus.WAITING
    assert promotion_before_restart.revision == 5
    assert promotion_before_restart.active_skill_nodes == frozenset({TARGET_NODE_ID})
