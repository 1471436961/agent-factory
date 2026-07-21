"""Real SQLite integration tests for the M2.4 promotion transaction."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest

from agent_factory.application.commands import (
    CloneAgentCommand,
    EvaluateInstanceCommand,
    KnowledgeSelection,
    PromoteAgentCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
    ReviewEvaluationCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    EvaluationDecision,
    InjectionMode,
    KnowledgeKind,
    ReviewDecision,
    RuleKind,
)
from agent_factory.domain.errors import (
    EvaluationSuiteMismatchError,
    MissingKnowledgeBindingError,
    PromotionRejectedError,
    RevisionConflictError,
    SkillDependencyError,
    StaleEvaluationReportError,
    UnknownToolError,
)
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationReport,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    DomainKnowledge,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillNode, SkillTree, SkillTreeDraft
from agent_factory.settings import Settings


@dataclass(frozen=True, slots=True)
class PromotionSetup:
    suite: EvaluationSuite
    manual_suite: EvaluationSuite
    tree: SkillTree
    instance: AgentInstance
    knowledge: DomainKnowledge


def _fail_promotion_audit(*args: object, **kwargs: object) -> Never:
    del args, kwargs
    raise RuntimeError("injected promotion audit failure")


async def _container(tmp_path: Path, migrations_dir: Path) -> Container:
    settings = Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
        }
    )
    container = build_container(settings)
    await container.start()
    return container


def _suite_draft(
    suite_id: str,
    *,
    require_manual_review: bool = False,
) -> EvaluationSuiteDraft:
    return EvaluationSuiteDraft(
        suite_id=suite_id,
        version="1.0.0",
        rules=(
            EvaluationRule(
                rule_id="mentions-pytest",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ("pytest",)},
            ),
        ),
        cases=(
            EvaluationCase(
                case_id="testing-strategy",
                input="Describe the project's testing strategy.",
            ),
        ),
        require_manual_review=require_manual_review,
    )


def _suite_ref(suite: EvaluationSuite) -> EvaluationSuiteRef:
    return EvaluationSuiteRef(
        suite_id=suite.suite_id,
        version=suite.version,
        checksum=suite.checksum,
    )


def _tree_ref(tree: SkillTree) -> SkillTreeRef:
    return SkillTreeRef(
        tree_id=tree.tree_id,
        version=tree.version,
        checksum=tree.checksum,
    )


def _definition() -> AgentDefinition:
    return AgentDefinition(
        agent_type="engineer-agent",
        role="Software Engineer",
        system_prompt="Produce technically verifiable engineering work.",
        capabilities=frozenset({Capability.CODE}),
        output_schema={"type": "object"},
    )


async def _prepare(container: Container) -> PromotionSetup:
    controller = container.controller
    suite = await controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=_suite_draft("engineer-readiness"),
            actor="owner",
        )
    )
    manual_suite = await controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=_suite_draft(
                "security-readiness",
                require_manual_review=True,
            ),
            actor="owner",
        )
    )
    guide_slot = KnowledgeSlot(
        name="engineering-guide",
        required=True,
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        min_version="1.0.0",
        injection_mode=InjectionMode.RETRIEVAL,
    )
    tree = await controller.register_skill_tree(
        RegisterSkillTreeCommand(
            tree=SkillTreeDraft(
                tree_id="engineer-skills",
                version="1.0.0",
                nodes=(
                    SkillNode(
                        node_id="junior-engineer",
                        display_name="Junior Engineer",
                        prompt_appendix="Apply the project testing policy.",
                        granted_tools=frozenset({"document-search"}),
                        added_knowledge_slots=(guide_slot,),
                        evaluation_suite=_suite_ref(suite),
                    ),
                    SkillNode(
                        node_id="mid-engineer",
                        display_name="Mid Engineer",
                        parents=frozenset({"junior-engineer"}),
                        prompt_appendix="Explain architectural trade-offs.",
                        evaluation_suite=_suite_ref(suite),
                    ),
                    SkillNode(
                        node_id="security-engineer",
                        display_name="Security Engineer",
                        prompt_appendix="Apply secure coding review.",
                        evaluation_suite=_suite_ref(manual_suite),
                    ),
                ),
            ),
            actor="owner",
        )
    )
    prototype = await controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="engineer-agent",
            version="1.0.0",
            definition=_definition(),
            skill_tree=_tree_ref(tree),
            publish=True,
            actor="owner",
        )
    )
    instance = await controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            actor="owner",
        )
    )
    content = "Use pytest, deterministic fixtures, and layered tests."
    knowledge = await controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=DomainKnowledgeDraft(
                knowledge_id="engineering-guide",
                version="1.0.0",
                name="Engineering Guide",
                kind=KnowledgeKind.DOCUMENT,
                content=content,
                checksum=checksum_knowledge_content(content),
            ),
            actor="owner",
        )
    )
    return PromotionSetup(
        suite=suite,
        manual_suite=manual_suite,
        tree=tree,
        instance=instance,
        knowledge=knowledge,
    )


async def _clone(container: Container) -> AgentInstance:
    return await container.controller.clone_agent(
        CloneAgentCommand(
            prototype_id="engineer-agent",
            prototype_version="1.0.0",
            actor="owner",
        )
    )


async def _evaluate(
    container: Container,
    *,
    instance: AgentInstance,
    suite: EvaluationSuite,
    output: str = "Use pytest with deterministic fixtures.",
) -> EvaluationReport:
    return await container.controller.evaluate_instance(
        EvaluateInstanceCommand(
            submission=EvaluationSubmission(
                instance_id=instance.instance_id,
                instance_revision=instance.revision,
                suite=_suite_ref(suite),
                runtime_model="test-model-1",
                case_results=(
                    SubmittedCaseResult(
                        case_id="testing-strategy",
                        output_text=output,
                    ),
                ),
            ),
            actor="owner",
        )
    )


def _promotion_command(
    setup: PromotionSetup,
    *,
    instance: AgentInstance,
    report_id: UUID,
    target_node_id: str = "junior-engineer",
    evaluation_review_id: UUID | None = None,
    include_knowledge: bool = True,
    idempotency_key: str | None = None,
) -> PromoteAgentCommand:
    selections = (
        (
            KnowledgeSelection(
                slot_name="engineering-guide",
                knowledge_id=setup.knowledge.knowledge_id,
                version=setup.knowledge.version,
            ),
        )
        if include_knowledge
        else ()
    )
    return PromoteAgentCommand(
        instance_id=instance.instance_id,
        expected_revision=instance.revision,
        target_node_id=target_node_id,
        evaluation_report_id=report_id,
        evaluation_review_id=evaluation_review_id,
        knowledge_selections=selections,
        actor="owner",
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_promotion_rebuilds_configuration_and_preserves_binding_provenance(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        setup = await _prepare(container)
        report = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
        )
        command = _promotion_command(
            setup,
            instance=setup.instance,
            report_id=report.report_id,
            idempotency_key="promote-junior-1",
        )

        promoted = await controller.promote_agent(command)
        assert await controller.promote_agent(command) == promoted
        assert promoted.revision == 2
        assert promoted.active_skill_nodes == frozenset({"junior-engineer"})
        assert promoted.configuration.tools == ("document-search",)
        assert (
            promoted.configuration.system_prompt.count(
                "Apply the project testing policy."
            )
            == 1
        )
        assert promoted.knowledge_bindings[0].bound_by == "owner"
        first_binding = promoted.knowledge_bindings[0]

        spec = await controller.export_spec(promoted.instance_id, actor="owner")
        assert spec.revision == 2
        assert spec.active_skill_nodes == promoted.active_skill_nodes
        assert spec.tools[0].name == "document-search"

        mid_report = await _evaluate(
            container,
            instance=promoted,
            suite=setup.suite,
        )
        mid = await controller.promote_agent(
            _promotion_command(
                setup,
                instance=promoted,
                report_id=mid_report.report_id,
                target_node_id="mid-engineer",
                include_knowledge=False,
            )
        )
        assert mid.revision == 3
        assert mid.active_skill_nodes == frozenset({"junior-engineer", "mid-engineer"})
        assert (
            mid.configuration.system_prompt.count("Apply the project testing policy.")
            == 1
        )
        assert (
            mid.configuration.system_prompt.count("Explain architectural trade-offs.")
            == 1
        )
        assert mid.knowledge_bindings[0] == first_binding

        audit = await controller.query_audit(AuditQuery(page_size=100))
        promoted_events = tuple(
            event
            for event in audit.items
            if event.event_type is AuditEventType.SKILL_PROMOTED
        )
        assert len(promoted_events) == 2
        junior_event = next(
            event
            for event in promoted_events
            if event.payload["node_id"] == "junior-engineer"
        )
        assert junior_event.payload == {
            "from_revision": 1,
            "to_revision": 2,
            "node_id": "junior-engineer",
            "report_id": str(report.report_id),
        }
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_promotion_rejects_dependency_failed_suite_and_stale_evidence(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        setup = await _prepare(container)
        passed = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
        )
        with pytest.raises(MissingKnowledgeBindingError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=setup.instance,
                    report_id=passed.report_id,
                    include_knowledge=False,
                )
            )
        with pytest.raises(SkillDependencyError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=setup.instance,
                    report_id=passed.report_id,
                    target_node_id="mid-engineer",
                    include_knowledge=False,
                )
            )
        with pytest.raises(EvaluationSuiteMismatchError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=setup.instance,
                    report_id=passed.report_id,
                    target_node_id="security-engineer",
                    include_knowledge=False,
                )
            )

        failed = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
            output="Use layered tests.",
        )
        assert failed.decision is EvaluationDecision.FAIL
        with pytest.raises(PromotionRejectedError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=setup.instance,
                    report_id=failed.report_id,
                )
            )

        manual = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.manual_suite,
        )
        promoted = await controller.promote_agent(
            _promotion_command(
                setup,
                instance=setup.instance,
                report_id=passed.report_id,
            )
        )
        with pytest.raises(StaleEvaluationReportError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=promoted,
                    report_id=manual.report_id,
                    target_node_id="security-engineer",
                    include_knowledge=False,
                )
            )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_promotion_requires_matching_approved_final_review(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        setup = await _prepare(container)
        first_instance = setup.instance
        report = await _evaluate(
            container,
            instance=first_instance,
            suite=setup.manual_suite,
        )
        assert report.decision is EvaluationDecision.REVIEW_REQUIRED
        with pytest.raises(PromotionRejectedError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=first_instance,
                    report_id=report.report_id,
                    target_node_id="security-engineer",
                    include_knowledge=False,
                )
            )
        with pytest.raises(PromotionRejectedError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=first_instance,
                    report_id=report.report_id,
                    target_node_id="security-engineer",
                    evaluation_review_id=container.id_generator.new(),
                    include_knowledge=False,
                )
            )
        rejected = await controller.review_evaluation(
            ReviewEvaluationCommand(
                report_id=report.report_id,
                decision=ReviewDecision.REJECTED,
                actor="reviewer",
            )
        )
        with pytest.raises(PromotionRejectedError):
            await controller.promote_agent(
                _promotion_command(
                    setup,
                    instance=first_instance,
                    report_id=report.report_id,
                    target_node_id="security-engineer",
                    evaluation_review_id=rejected.review_id,
                    include_knowledge=False,
                )
            )

        second_instance = await _clone(container)
        approved_report = await _evaluate(
            container,
            instance=second_instance,
            suite=setup.manual_suite,
        )
        approved = await controller.review_evaluation(
            ReviewEvaluationCommand(
                report_id=approved_report.report_id,
                decision=ReviewDecision.APPROVED,
                actor="reviewer",
            )
        )
        promoted = await controller.promote_agent(
            _promotion_command(
                setup,
                instance=second_instance,
                report_id=approved_report.report_id,
                target_node_id="security-engineer",
                evaluation_review_id=approved.review_id,
                include_knowledge=False,
            )
        )
        assert promoted.active_skill_nodes == frozenset({"security-engineer"})
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_concurrent_promotions_with_same_revision_allow_one_success(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)

    try:
        setup = await _prepare(container)
        report = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
        )
        command = _promotion_command(
            setup,
            instance=setup.instance,
            report_id=report.report_id,
        )

        results = await asyncio.gather(
            container.controller.promote_agent(command),
            container.controller.promote_agent(command),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, AgentInstance)]
        conflicts = [
            item for item in results if isinstance(item, RevisionConflictError)
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0].revision == 2
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_promotion_rejects_unregistered_skill_tool_before_snapshot_write(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        setup = await _prepare(container)
        tree = await controller.register_skill_tree(
            RegisterSkillTreeCommand(
                tree=SkillTreeDraft(
                    tree_id="unsafe-tool-skills",
                    version="1.0.0",
                    nodes=(
                        SkillNode(
                            node_id="unsafe-tool-user",
                            display_name="Unsafe Tool User",
                            granted_tools=frozenset({"unregistered-tool"}),
                            evaluation_suite=_suite_ref(setup.suite),
                        ),
                    ),
                ),
                actor="owner",
            )
        )
        prototype = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="unsafe-tool-agent",
                version="1.0.0",
                definition=_definition(),
                skill_tree=_tree_ref(tree),
                publish=True,
                actor="owner",
            )
        )
        instance = await controller.clone_agent(
            CloneAgentCommand(
                prototype_id=prototype.prototype_id,
                prototype_version=prototype.version,
                actor="owner",
            )
        )
        report = await _evaluate(
            container,
            instance=instance,
            suite=setup.suite,
        )

        with pytest.raises(UnknownToolError):
            await controller.promote_agent(
                PromoteAgentCommand(
                    instance_id=instance.instance_id,
                    expected_revision=1,
                    target_node_id="unsafe-tool-user",
                    evaluation_report_id=report.report_id,
                    actor="owner",
                )
            )
        async with container.uow_factory(read_only=True) as uow:
            assert await uow.instances.get(instance.instance_id) == instance
            assert await uow.instances.get(instance.instance_id, 2) is None
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_promotion_rolls_back_snapshot_audit_and_idempotency_on_failure(
    tmp_path: Path,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        setup = await _prepare(container)
        report = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
        )
        command = _promotion_command(
            setup,
            instance=setup.instance,
            report_id=report.report_id,
            idempotency_key="rollback-promotion-1",
        )
        monkeypatch.setattr(
            controller._audit_factory,
            "skill_promoted",
            _fail_promotion_audit,
        )

        with pytest.raises(RuntimeError, match="injected promotion audit failure"):
            await controller.promote_agent(command)
        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(setup.instance.instance_id)
            assert current == setup.instance
            assert await uow.instances.get(setup.instance.instance_id, 2) is None
        audit = await controller.query_audit(AuditQuery(page_size=100))
        assert all(
            event.event_type is not AuditEventType.SKILL_PROMOTED
            for event in audit.items
        )

        monkeypatch.undo()
        promoted = await controller.promote_agent(command)
        assert promoted.revision == 2
    finally:
        await container.close()
