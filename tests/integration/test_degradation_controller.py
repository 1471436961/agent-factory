"""Real SQLite integration tests for M2.5 observation and degradation."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    EvaluateInstanceCommand,
    KnowledgeSelection,
    PromoteAgentCommand,
    RecordTaskOutcomeCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    InjectionMode,
    InstanceStatus,
    KnowledgeKind,
    RuleKind,
)
from agent_factory.domain.errors import (
    RevisionConflictError,
    TaskOutcomeAlreadyExistsError,
    TaskOutcomeMismatchError,
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
    KnowledgeBinding,
    KnowledgeSlot,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import (
    DegradationCheckResult,
    ObservationPolicy,
    SkillNode,
    SkillTree,
    SkillTreeDraft,
)
from agent_factory.settings import Settings


@dataclass(frozen=True, slots=True)
class DegradationSetup:
    suite: EvaluationSuite
    tree: SkillTree
    instance: AgentInstance
    base_binding: KnowledgeBinding


def _fail_degradation_audit(*args: object, **kwargs: object) -> Never:
    del args, kwargs
    raise RuntimeError("injected degradation audit failure")


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


def _slot(name: str) -> KnowledgeSlot:
    return KnowledgeSlot(
        name=name,
        required=True,
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        min_version="1.0.0",
        injection_mode=InjectionMode.RETRIEVAL,
    )


async def _register_knowledge(
    container: Container,
    knowledge_id: str,
) -> DomainKnowledge:
    content = f"Verified guidance for {knowledge_id}."
    return await container.controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=DomainKnowledgeDraft(
                knowledge_id=knowledge_id,
                version="1.0.0",
                name=knowledge_id.replace("-", " ").title(),
                kind=KnowledgeKind.DOCUMENT,
                content=content,
                checksum=checksum_knowledge_content(content),
            ),
            actor="owner",
        )
    )


async def _evaluate(
    container: Container,
    *,
    instance: AgentInstance,
    suite: EvaluationSuite,
    passed: bool,
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
                        output_text=(
                            "Use pytest with deterministic fixtures."
                            if passed
                            else "No verified testing term is present."
                        ),
                    ),
                ),
            ),
            actor="owner",
        )
    )


async def _promote(
    container: Container,
    *,
    instance: AgentInstance,
    suite: EvaluationSuite,
    node_id: str,
    knowledge_id: str | None = None,
) -> AgentInstance:
    report = await _evaluate(
        container,
        instance=instance,
        suite=suite,
        passed=True,
    )
    selections = (
        (
            KnowledgeSelection(
                slot_name=knowledge_id,
                knowledge_id=knowledge_id,
                version="1.0.0",
            ),
        )
        if knowledge_id is not None
        else ()
    )
    return await container.controller.promote_agent(
        PromoteAgentCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            target_node_id=node_id,
            evaluation_report_id=report.report_id,
            knowledge_selections=selections,
            actor="owner",
        )
    )


async def _prepare(container: Container) -> DegradationSetup:
    controller = container.controller
    suite = await controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=EvaluationSuiteDraft(
                suite_id="engineer-readiness",
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
                        input="Describe the testing strategy.",
                    ),
                ),
            ),
            actor="owner",
        )
    )
    observation_policy = ObservationPolicy(
        window_size=4,
        minimum_samples=3,
        consecutive_failures=2,
        failure_rate_threshold=0.75,
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
                        prompt_appendix="Apply junior testing discipline.",
                        granted_tools=frozenset({"document-search"}),
                        added_knowledge_slots=(_slot("junior-guide"),),
                        output_schema_override={
                            "type": "object",
                            "required": ["summary"],
                            "properties": {"summary": {"type": "string"}},
                        },
                        evaluation_suite=_suite_ref(suite),
                        observation_policy=observation_policy,
                    ),
                    SkillNode(
                        node_id="mid-engineer",
                        display_name="Mid Engineer",
                        parents=frozenset({"junior-engineer"}),
                        prompt_appendix="Explain architecture trade-offs.",
                        added_knowledge_slots=(_slot("mid-guide"),),
                        evaluation_suite=_suite_ref(suite),
                    ),
                    SkillNode(
                        node_id="security-engineer",
                        display_name="Security Engineer",
                        prompt_appendix="Preserve independent security review.",
                        evaluation_suite=_suite_ref(suite),
                    ),
                ),
            ),
            actor="owner",
        )
    )
    definition = AgentDefinition(
        agent_type="engineer-agent",
        role="Software Engineer",
        system_prompt="Produce technically verifiable engineering work.",
        capabilities=frozenset({Capability.CODE}),
        output_schema={"type": "object"},
        knowledge_slots=(_slot("base-guide"),),
    )
    prototype = await controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="engineer-agent",
            version="1.0.0",
            definition=definition,
            skill_tree=_tree_ref(tree),
            publish=True,
            actor="owner",
        )
    )
    base_knowledge = await _register_knowledge(container, "base-guide")
    await _register_knowledge(container, "junior-guide")
    await _register_knowledge(container, "mid-guide")
    instance = await controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            actor="owner",
        )
    )
    instance = await controller.bind_knowledge(
        BindKnowledgeCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            selections=(
                KnowledgeSelection(
                    slot_name="base-guide",
                    knowledge_id=base_knowledge.knowledge_id,
                    version=base_knowledge.version,
                ),
            ),
            actor="owner",
        )
    )
    instance = await _promote(
        container,
        instance=instance,
        suite=suite,
        node_id="junior-engineer",
        knowledge_id="junior-guide",
    )
    instance = await _promote(
        container,
        instance=instance,
        suite=suite,
        node_id="mid-engineer",
        knowledge_id="mid-guide",
    )
    instance = await _promote(
        container,
        instance=instance,
        suite=suite,
        node_id="security-engineer",
    )
    base_binding = next(
        binding
        for binding in instance.knowledge_bindings
        if binding.slot_name == "base-guide"
    )
    return DegradationSetup(
        suite=suite,
        tree=tree,
        instance=instance,
        base_binding=base_binding,
    )


def _observation_command(
    setup: DegradationSetup,
    *,
    task_number: int,
    report: EvaluationReport,
    passed: bool,
    idempotency_key: str | None = None,
) -> RecordTaskOutcomeCommand:
    return RecordTaskOutcomeCommand(
        instance_id=setup.instance.instance_id,
        expected_revision=setup.instance.revision,
        task_id=UUID(f"00000000-0000-0000-0000-{task_number:012d}"),
        skill_node_id="junior-engineer",
        passed=passed,
        evaluation_report_id=report.report_id,
        actor="owner",
        idempotency_key=idempotency_key,
    )


async def _record(
    container: Container,
    setup: DegradationSetup,
    *,
    task_number: int,
    passed: bool,
    idempotency_key: str | None = None,
) -> tuple[RecordTaskOutcomeCommand, DegradationCheckResult]:
    report = await _evaluate(
        container,
        instance=setup.instance,
        suite=setup.suite,
        passed=passed,
    )
    command = _observation_command(
        setup,
        task_number=task_number,
        report=report,
        passed=passed,
        idempotency_key=idempotency_key,
    )
    return command, await container.controller.record_task_outcome(command)


@pytest.mark.asyncio
async def test_observation_degrades_from_prototype_and_preserves_independent_branch(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    try:
        setup = await _prepare(container)
        _, first = await _record(
            container,
            setup,
            task_number=801,
            passed=True,
        )
        _, second = await _record(
            container,
            setup,
            task_number=802,
            passed=False,
        )
        final_command, final = await _record(
            container,
            setup,
            task_number=803,
            passed=False,
            idempotency_key="degrade-junior-1",
        )

        assert first.degraded is False
        assert second.degraded is False
        assert first.resulting_revision == setup.instance.revision
        assert final.degraded is True
        assert final.resulting_revision == setup.instance.revision + 1
        assert final.removed_nodes == frozenset({"junior-engineer", "mid-engineer"})
        assert final.removed_binding_slots == frozenset({"junior-guide", "mid-guide"})
        assert await container.controller.record_task_outcome(final_command) == final

        async with container.uow_factory(read_only=True) as uow:
            degraded = await uow.instances.get(setup.instance.instance_id)
            old_window = await uow.task_outcomes.list_for_node(
                instance_id=setup.instance.instance_id,
                instance_revision=setup.instance.revision,
                skill_node_id="junior-engineer",
                limit=4,
            )
            new_window = await uow.task_outcomes.list_for_node(
                instance_id=setup.instance.instance_id,
                instance_revision=setup.instance.revision + 1,
                skill_node_id="junior-engineer",
                limit=4,
            )
        assert degraded is not None
        assert degraded.status is InstanceStatus.DEGRADED
        assert degraded.active_skill_nodes == frozenset({"security-engineer"})
        assert "junior testing discipline" not in degraded.configuration.system_prompt
        assert "architecture trade-offs" not in degraded.configuration.system_prompt
        assert "independent security review" in degraded.configuration.system_prompt
        assert degraded.configuration.tools == ()
        assert degraded.configuration.output_schema == {"type": "object"}
        assert degraded.knowledge_bindings == (setup.base_binding,)
        assert len(old_window) == 3
        assert new_window == ()

        audit = await container.controller.query_audit(
            AuditQuery(
                entity_id=str(setup.instance.instance_id),
                event_types=frozenset(
                    {
                        AuditEventType.TASK_OUTCOME_RECORDED,
                        AuditEventType.SKILL_DEGRADED,
                    }
                ),
                page_size=20,
            )
        )
        assert [event.event_type for event in audit.items].count(
            AuditEventType.TASK_OUTCOME_RECORDED
        ) == 3
        assert [event.event_type for event in audit.items].count(
            AuditEventType.SKILL_DEGRADED
        ) == 1
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_observation_rejects_report_replay_and_contradictory_result(
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
            passed=False,
        )
        command = _observation_command(
            setup,
            task_number=811,
            report=report,
            passed=False,
        )
        await container.controller.record_task_outcome(command)

        with pytest.raises(TaskOutcomeAlreadyExistsError):
            await container.controller.record_task_outcome(
                command.model_copy(
                    update={"task_id": UUID("00000000-0000-0000-0000-000000000812")}
                )
            )
        contradictory = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
            passed=False,
        )
        with pytest.raises(TaskOutcomeMismatchError):
            await container.controller.record_task_outcome(
                _observation_command(
                    setup,
                    task_number=813,
                    report=contradictory,
                    passed=True,
                )
            )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_degradation_rolls_back_outcome_snapshot_audit_and_idempotency(
    tmp_path: Path,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    try:
        setup = await _prepare(container)
        await _record(container, setup, task_number=821, passed=True)
        await _record(container, setup, task_number=822, passed=False)
        report = await _evaluate(
            container,
            instance=setup.instance,
            suite=setup.suite,
            passed=False,
        )
        command = _observation_command(
            setup,
            task_number=823,
            report=report,
            passed=False,
            idempotency_key="rollback-degradation-1",
        )
        monkeypatch.setattr(
            container.controller._audit_factory,
            "skill_degraded",
            _fail_degradation_audit,
        )

        with pytest.raises(RuntimeError, match="injected degradation audit failure"):
            await container.controller.record_task_outcome(command)
        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(setup.instance.instance_id)
            window = await uow.task_outcomes.list_for_node(
                instance_id=setup.instance.instance_id,
                instance_revision=setup.instance.revision,
                skill_node_id="junior-engineer",
                limit=4,
            )
        assert current == setup.instance
        assert len(window) == 2

        monkeypatch.undo()
        result = await container.controller.record_task_outcome(command)
        assert result.degraded is True
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_concurrent_threshold_crossing_creates_one_degraded_revision(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    try:
        setup = await _prepare(container)
        await _record(container, setup, task_number=831, passed=True)
        await _record(container, setup, task_number=832, passed=False)
        reports = await asyncio.gather(
            _evaluate(
                container,
                instance=setup.instance,
                suite=setup.suite,
                passed=False,
            ),
            _evaluate(
                container,
                instance=setup.instance,
                suite=setup.suite,
                passed=False,
            ),
        )
        commands = tuple(
            _observation_command(
                setup,
                task_number=833 + index,
                report=report,
                passed=False,
            )
            for index, report in enumerate(reports)
        )

        results = await asyncio.gather(
            *(
                container.controller.record_task_outcome(command)
                for command in commands
            ),
            return_exceptions=True,
        )

        successes = [
            result for result in results if isinstance(result, DegradationCheckResult)
        ]
        conflicts = [
            result for result in results if isinstance(result, RevisionConflictError)
        ]
        assert len(successes) == 1
        assert successes[0].degraded is True
        assert len(conflicts) == 1
        async with container.uow_factory(read_only=True) as uow:
            current = await uow.instances.get(setup.instance.instance_id)
            window = await uow.task_outcomes.list_for_node(
                instance_id=setup.instance.instance_id,
                instance_revision=setup.instance.revision,
                skill_node_id="junior-engineer",
                limit=4,
            )
        assert current is not None
        assert current.revision == setup.instance.revision + 1
        assert len(window) == 3
    finally:
        await container.close()
