"""Factory Tool workflows against the real Controller and SQLite backend."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
import pytest

from agent_factory.application.commands import (
    EvaluateInstanceCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
)
from agent_factory.application.queries import AuditQuery, Page
from agent_factory.application.security import FactoryRole, Principal
from agent_factory.container import Container, build_container
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import AuditEventType
from agent_factory.domain.evaluation import (
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    DomainKnowledge,
    DomainKnowledgeDraft,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillTreeDraft
from agent_factory.interfaces.api.contracts import CloneAgentRequest
from agent_factory.interfaces.api.main import create_app
from agent_factory.interfaces.factory_tools import FactoryToolCallContext
from agent_factory.sdk import AgentFactoryClient
from agent_factory.settings import Settings

AUTH_TOKEN = "factory-tool-token-that-is-at-least-32-characters"
ACTOR = "interface-owner"


def _settings(tmp_path: Path, migrations_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": AUTH_TOKEN,
            "auth_subject": ACTOR,
            "auth_roles": ["admin"],
        }
    )


def _context(
    request_id: str,
    correlation_id: str,
    *,
    idempotency_key: str | None = None,
) -> FactoryToolCallContext:
    return FactoryToolCallContext(
        request_id=UUID(request_id),
        correlation_id=UUID(correlation_id),
        principal=Principal(
            subject=ACTOR,
            roles=frozenset({FactoryRole.ADMIN}),
        ),
        idempotency_key=idempotency_key,
    )


async def _register_governed_assets(
    container: Container,
) -> tuple[AgentPrototype, DomainKnowledge, EvaluationSuite]:
    suite = await container.controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=EvaluationSuiteDraft.model_validate(
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
                            "input": "Describe the testing strategy.",
                        }
                    ],
                }
            ),
            actor=ACTOR,
        )
    )
    suite_ref = EvaluationSuiteRef(
        suite_id=suite.suite_id,
        version=suite.version,
        checksum=suite.checksum,
    )
    tree = await container.controller.register_skill_tree(
        RegisterSkillTreeCommand(
            tree=SkillTreeDraft.model_validate(
                {
                    "tree_id": "engineer-skills",
                    "version": "1.0.0",
                    "nodes": [
                        {
                            "node_id": "junior-engineer",
                            "display_name": "Junior Engineer",
                            "prompt_appendix": (
                                "Use deterministic engineering evidence."
                            ),
                            "evaluation_suite": suite_ref.model_dump(mode="json"),
                        }
                    ],
                }
            ),
            actor=ACTOR,
        )
    )
    prototype = await container.controller.register_prototype(
        RegisterPrototypeCommand.model_validate(
            {
                "prototype_id": "engineer-agent",
                "version": "1.0.0",
                "definition": {
                    "agent_type": "engineer-agent",
                    "role": "Software Engineer",
                    "system_prompt": "Produce technically verifiable work.",
                    "knowledge_slots": [
                        {
                            "name": "product-docs",
                            "required": True,
                            "accepted_kinds": ["document"],
                            "min_version": "1.0.0",
                            "injection_mode": "retrieval",
                        }
                    ],
                },
                "skill_tree": SkillTreeRef(
                    tree_id=tree.tree_id,
                    version=tree.version,
                    checksum=tree.checksum,
                ).model_dump(mode="json"),
                "publish": True,
                "actor": ACTOR,
            }
        )
    )
    content = "Agent Factory uses pytest and deterministic fixtures."
    knowledge = await container.controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=DomainKnowledgeDraft.model_validate(
                {
                    "knowledge_id": "product-docs",
                    "version": "1.0.0",
                    "name": "Product Documentation",
                    "kind": "document",
                    "content": content,
                    "checksum": checksum_knowledge_content(content),
                }
            ),
            actor=ACTOR,
        )
    )
    return prototype, knowledge, suite


@pytest.mark.asyncio
async def test_all_five_factory_tools_execute_real_governance_workflow(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = build_container(_settings(tmp_path, migrations_dir))
    await container.start()
    try:
        prototype, knowledge, suite = await _register_governed_assets(container)
        adapter = container.factory_tools

        listed_result = await adapter.invoke(
            "list_prototypes",
            {"status": "published", "agent_type": "engineer-agent"},
            _context(
                "00000000-0000-0000-0000-000000000921",
                "00000000-0000-0000-0000-000000000931",
            ),
        )
        assert listed_result.output is not None
        listed = Page[AgentPrototype].model_validate(listed_result.output)
        assert listed.items == (prototype,)

        clone_context = _context(
            "00000000-0000-0000-0000-000000000922",
            "00000000-0000-0000-0000-000000000932",
        )
        clone_arguments = {
            "prototype_id": prototype.prototype_id,
            "version": prototype.version,
            "runtime_target": "local-runtime",
        }
        clone_result = await adapter.invoke(
            "clone_agent", clone_arguments, clone_context
        )
        clone_replay = await adapter.invoke(
            "clone_agent", clone_arguments, clone_context
        )
        assert clone_result.output is not None
        assert clone_replay == clone_result
        instance = AgentInstance.model_validate(clone_result.output)

        bind_result = await adapter.invoke(
            "bind_knowledge",
            {
                "instance_id": str(instance.instance_id),
                "expected_revision": instance.revision,
                "selections": [
                    {
                        "slot_name": "product-docs",
                        "knowledge_id": knowledge.knowledge_id,
                        "version": knowledge.version,
                    }
                ],
            },
            _context(
                "00000000-0000-0000-0000-000000000923",
                "00000000-0000-0000-0000-000000000933",
            ),
        )
        assert bind_result.output is not None
        bound = AgentInstance.model_validate(bind_result.output)
        suite_ref = EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        )
        report = await container.controller.evaluate_instance(
            EvaluateInstanceCommand(
                submission=EvaluationSubmission(
                    instance_id=bound.instance_id,
                    instance_revision=bound.revision,
                    suite=suite_ref,
                    runtime_model="factory-tool-integration-model",
                    case_results=(
                        SubmittedCaseResult(
                            case_id="testing-strategy",
                            output_text="Use pytest with deterministic fixtures.",
                        ),
                    ),
                ),
                actor=ACTOR,
            )
        )

        promotion_result = await adapter.invoke(
            "apply_promotion",
            {
                "instance_id": str(bound.instance_id),
                "expected_revision": bound.revision,
                "target_node_id": "junior-engineer",
                "evaluation_report_id": str(report.report_id),
            },
            _context(
                "00000000-0000-0000-0000-000000000924",
                "00000000-0000-0000-0000-000000000934",
            ),
        )
        assert promotion_result.output is not None
        promoted = AgentInstance.model_validate(promotion_result.output)
        assert promoted.active_skill_nodes == frozenset({"junior-engineer"})

        audit_result = await adapter.invoke(
            "query_audit_log",
            {
                "entity_id": str(instance.instance_id),
                "event_types": ["instance.cloned", "skill.promoted"],
                "page_size": 100,
            },
            _context(
                "00000000-0000-0000-0000-000000000925",
                "00000000-0000-0000-0000-000000000935",
            ),
        )
        assert audit_result.output is not None
        audit = Page[AuditEvent].model_validate(audit_result.output)
        assert {event.event_type for event in audit.items} == {
            AuditEventType.INSTANCE_CLONED,
            AuditEventType.SKILL_PROMOTED,
        }
        cloned_event = next(
            event
            for event in audit.items
            if event.event_type is AuditEventType.INSTANCE_CLONED
        )
        assert cloned_event.correlation_id == clone_context.correlation_id
        assert cloned_event.actor == ACTOR
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_rest_sdk_and_factory_tool_replay_the_exact_clone_result(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        container: Container = app.state.container
        prototype, _, _ = await _register_governed_assets(container)
        idempotency_key = "cross-interface-clone-key"
        path = (
            f"{settings.api_prefix}/prototypes/{prototype.prototype_id}"
            f"/versions/{prototype.version}/instances"
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as http_client:
            response = await http_client.post(
                path,
                json={},
                headers={
                    "Authorization": f"Bearer {AUTH_TOKEN}",
                    "Idempotency-Key": idempotency_key,
                    "X-Correlation-ID": ("00000000-0000-0000-0000-000000000941"),
                },
            )
        assert response.status_code == 201
        rest_instance = AgentInstance.model_validate(response.json())

        sdk_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with AgentFactoryClient(
            base_url="http://testserver",
            token=AUTH_TOKEN,
            transport=sdk_transport,
        ) as client:
            sdk_instance = await client.clone_agent(
                prototype.prototype_id,
                prototype.version,
                CloneAgentRequest(),
                idempotency_key=idempotency_key,
                correlation_id=UUID("00000000-0000-0000-0000-000000000942"),
            )

        tool_result = await container.factory_tools.invoke(
            "clone_agent",
            {
                "prototype_id": prototype.prototype_id,
                "version": prototype.version,
            },
            _context(
                "00000000-0000-0000-0000-000000000943",
                "00000000-0000-0000-0000-000000000944",
                idempotency_key=idempotency_key,
            ),
        )
        assert tool_result.output is not None
        tool_instance = AgentInstance.model_validate(tool_result.output)

        assert sdk_instance == rest_instance
        assert tool_instance == rest_instance
        audit = await container.controller.query_audit(AuditQuery(page_size=100))
        assert (
            sum(
                event.event_type is AuditEventType.INSTANCE_CLONED
                for event in audit.items
            )
            == 1
        )
