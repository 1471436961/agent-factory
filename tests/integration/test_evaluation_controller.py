"""Real SQLite integration tests for the M2.3 evaluation application service."""

import sqlite3
from pathlib import Path
from typing import Never

import pytest

from agent_factory.application.commands import (
    CloneAgentCommand,
    EvaluateInstanceCommand,
    RegisterEvaluationSuiteCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
    ReviewEvaluationCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import Container, build_container
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    EvaluationDecision,
    InstanceStatus,
    ReviewDecision,
    RuleKind,
)
from agent_factory.domain.errors import (
    EvaluationReportNotFoundError,
    EvaluationReviewConflictError,
    EvaluationReviewNotRequiredError,
    EvaluationSubmissionError,
    EvaluationSuiteMismatchError,
    EvaluationSuiteNotFoundError,
    SkillTreeNotBoundError,
    SkillTreeNotFoundError,
)
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import AgentDefinition, AgentInstance
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.skills import SkillNode, SkillTree, SkillTreeDraft
from agent_factory.settings import Settings


def _fail_evaluation_audit(*args: object, **kwargs: object) -> Never:
    del args, kwargs
    raise RuntimeError("injected evaluation audit failure")


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
    *,
    suite_id: str = "engineer-readiness",
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


def _tree_draft(
    suite: EvaluationSuite,
    *,
    tree_id: str = "engineer-skills",
) -> SkillTreeDraft:
    return SkillTreeDraft(
        tree_id=tree_id,
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                evaluation_suite=_suite_ref(suite),
            ),
        ),
    )


def _tree_ref(tree: SkillTree) -> SkillTreeRef:
    return SkillTreeRef(
        tree_id=tree.tree_id,
        version=tree.version,
        checksum=tree.checksum,
    )


def _engineer_definition() -> AgentDefinition:
    return AgentDefinition(
        agent_type="engineer-agent",
        role="Software Engineer",
        system_prompt="Produce a technically verifiable engineering answer.",
        capabilities=frozenset({Capability.CODE}),
        output_schema={"type": "object"},
    )


async def _register_tree_and_agent(
    container: Container,
    *,
    suite_id: str = "engineer-readiness",
    tree_id: str = "engineer-skills",
    require_manual_review: bool = False,
) -> tuple[EvaluationSuite, SkillTree, AgentInstance]:
    controller = container.controller
    suite = await controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=_suite_draft(
                suite_id=suite_id,
                require_manual_review=require_manual_review,
            ),
            actor="owner",
        )
    )
    tree = await controller.register_skill_tree(
        RegisterSkillTreeCommand(
            tree=_tree_draft(suite, tree_id=tree_id),
            actor="owner",
        )
    )
    prototype = await controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id=f"{tree_id}-agent",
            version="1.0.0",
            definition=_engineer_definition(),
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
    return suite, tree, instance


def _evaluation_command(
    *,
    suite: EvaluationSuite,
    instance: AgentInstance,
    output: str,
    idempotency_key: str | None = None,
) -> EvaluateInstanceCommand:
    return EvaluateInstanceCommand(
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
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_controller_registers_governance_and_persists_evaluation_reports(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller
    suite_command = RegisterEvaluationSuiteCommand(
        suite=_suite_draft(),
        actor="owner",
        idempotency_key="register-suite-1",
    )

    try:
        suite = await controller.register_evaluation_suite(suite_command)
        assert await controller.register_evaluation_suite(suite_command) == suite
        assert (
            await controller.get_evaluation_suite(suite.suite_id, suite.version)
            == suite
        )
        tree_command = RegisterSkillTreeCommand(
            tree=_tree_draft(suite),
            actor="owner",
            idempotency_key="register-tree-1",
        )
        tree = await controller.register_skill_tree(tree_command)
        assert await controller.register_skill_tree(tree_command) == tree
        assert await controller.get_skill_tree(tree.tree_id, tree.version) == tree

        prototype = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="engineer-agent",
                version="1.0.0",
                definition=_engineer_definition(),
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
        assert prototype.skill_tree == _tree_ref(tree)
        assert instance.skill_tree == prototype.skill_tree

        pass_command = _evaluation_command(
            suite=suite,
            instance=instance,
            output="Use pytest with unit and integration tests.",
            idempotency_key="evaluate-instance-1",
        )
        passed = await controller.evaluate_instance(pass_command)
        assert await controller.evaluate_instance(pass_command) == passed
        assert passed.decision is EvaluationDecision.PASS
        assert passed.skill_tree == instance.skill_tree
        assert passed.suite == _suite_ref(suite)

        failed = await controller.evaluate_instance(
            _evaluation_command(
                suite=suite,
                instance=instance,
                output="Use unit and integration tests.",
            )
        )
        assert failed.decision is EvaluationDecision.FAIL

        async with container.uow_factory(read_only=True) as uow:
            assert await uow.evaluation_reports.get(passed.report_id) == passed
            spec = await uow.specs.get(instance.instance_id, instance.revision)
            assert spec is not None
            assert spec.spec_checksum == passed.agent_spec_checksum
            assert spec.skill_tree == instance.skill_tree

        audit = await controller.query_audit(AuditQuery(page_size=100))
        evaluation_events = tuple(
            event
            for event in audit.items
            if event.event_type is AuditEventType.EVALUATION_COMPLETED
        )
        assert len(evaluation_events) == 2
        assert (
            sum(
                event.event_type is AuditEventType.SPEC_EXPORTED
                for event in audit.items
            )
            == 1
        )
        assert "Use pytest" not in str(
            [event.model_dump(mode="json") for event in evaluation_events]
        )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_controller_rejects_unregistered_or_mismatched_governance_refs(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller
    missing_suite_ref = EvaluationSuiteRef(
        suite_id="missing-suite",
        version="1.0.0",
        checksum="0" * 64,
    )
    missing_tree = SkillTreeDraft(
        tree_id="missing-suite-tree",
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                evaluation_suite=missing_suite_ref,
            ),
        ),
    )

    try:
        with pytest.raises(EvaluationSuiteNotFoundError):
            await controller.register_skill_tree(
                RegisterSkillTreeCommand(tree=missing_tree, actor="owner")
            )

        suite, tree, instance = await _register_tree_and_agent(container)
        wrong_tree_ref = _tree_ref(tree).model_copy(update={"checksum": "f" * 64})
        with pytest.raises(SkillTreeNotFoundError):
            await controller.register_prototype(
                RegisterPrototypeCommand(
                    prototype_id="wrong-tree-agent",
                    version="1.0.0",
                    definition=_engineer_definition(),
                    skill_tree=wrong_tree_ref,
                    actor="owner",
                )
            )

        plain_prototype = await controller.register_prototype(
            RegisterPrototypeCommand(
                prototype_id="plain-agent",
                version="1.0.0",
                definition=_engineer_definition(),
                publish=True,
                actor="owner",
            )
        )
        plain_instance = await controller.clone_agent(
            CloneAgentCommand(
                prototype_id=plain_prototype.prototype_id,
                prototype_version=plain_prototype.version,
                actor="owner",
            )
        )
        with pytest.raises(SkillTreeNotBoundError):
            await controller.evaluate_instance(
                _evaluation_command(
                    suite=suite,
                    instance=plain_instance,
                    output="Use pytest.",
                )
            )

        other_suite = await controller.register_evaluation_suite(
            RegisterEvaluationSuiteCommand(
                suite=_suite_draft(suite_id="other-suite"),
                actor="owner",
            )
        )
        with pytest.raises(EvaluationSuiteMismatchError):
            await controller.evaluate_instance(
                _evaluation_command(
                    suite=other_suite,
                    instance=instance,
                    output="Use pytest.",
                )
            )

        invalid_cases = _evaluation_command(
            suite=suite,
            instance=instance,
            output="Use pytest.",
        ).model_copy(
            update={
                "submission": EvaluationSubmission(
                    instance_id=instance.instance_id,
                    instance_revision=instance.revision,
                    suite=_suite_ref(suite),
                    runtime_model="test-model-1",
                    case_results=(
                        SubmittedCaseResult(
                            case_id="unexpected-case",
                            output_text="Use pytest.",
                        ),
                    ),
                )
            }
        )
        with pytest.raises(EvaluationSubmissionError):
            await controller.evaluate_instance(invalid_cases)
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_manual_review_is_final_idempotent_and_redacted_from_audit(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        suite, _, instance = await _register_tree_and_agent(
            container,
            suite_id="manual-readiness",
            tree_id="manual-skills",
            require_manual_review=True,
        )
        report = await controller.evaluate_instance(
            _evaluation_command(
                suite=suite,
                instance=instance,
                output="Use pytest.",
            )
        )
        assert report.decision is EvaluationDecision.REVIEW_REQUIRED

        review_command = ReviewEvaluationCommand(
            report_id=report.report_id,
            decision=ReviewDecision.APPROVED,
            comment="private reviewer comment",
            actor="reviewer",
            idempotency_key="review-report-1",
        )
        review = await controller.review_evaluation(review_command)
        assert await controller.review_evaluation(review_command) == review
        with pytest.raises(EvaluationReviewConflictError):
            await controller.review_evaluation(
                review_command.model_copy(
                    update={
                        "decision": ReviewDecision.REJECTED,
                        "idempotency_key": None,
                    }
                )
            )

        failed_report = await controller.evaluate_instance(
            _evaluation_command(
                suite=suite,
                instance=instance,
                output="Use unit tests.",
            )
        )
        with pytest.raises(EvaluationReviewNotRequiredError):
            await controller.review_evaluation(
                ReviewEvaluationCommand(
                    report_id=failed_report.report_id,
                    decision=ReviewDecision.REJECTED,
                    actor="reviewer",
                )
            )
        with pytest.raises(EvaluationReportNotFoundError):
            await controller.review_evaluation(
                ReviewEvaluationCommand(
                    report_id=container.id_generator.new(),
                    decision=ReviewDecision.REJECTED,
                    actor="reviewer",
                )
            )

        audit = await controller.query_audit(AuditQuery(page_size=100))
        review_events = tuple(
            event
            for event in audit.items
            if event.event_type is AuditEventType.EVALUATION_REVIEWED
        )
        assert len(review_events) == 1
        assert "private reviewer comment" not in str(
            review_events[0].model_dump(mode="json")
        )
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_evaluation_remains_bound_to_requested_historical_revision(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        suite, _, revision_one = await _register_tree_and_agent(container)
        revision_two = AgentInstance.model_validate(
            {
                **revision_one.model_dump(mode="python"),
                "revision": 2,
                "status": InstanceStatus.WAITING,
                "updated_at": container.clock.now(),
            }
        )
        async with container.uow_factory() as uow:
            await uow.instances.save_snapshot(revision_two, expected_revision=1)
            await uow.commit()

        report = await controller.evaluate_instance(
            _evaluation_command(
                suite=suite,
                instance=revision_one,
                output="Use pytest.",
            )
        )

        assert report.instance_revision == 1
        async with container.uow_factory(read_only=True) as uow:
            latest = await uow.instances.get(revision_one.instance_id)
            historical = await uow.instances.get(revision_one.instance_id, 1)
            assert latest == revision_two
            assert historical == revision_one
            assert await uow.evaluation_reports.get(report.report_id) == report
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_evaluation_rolls_back_spec_report_and_audit_on_final_write_failure(
    tmp_path: Path,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = await _container(tmp_path, migrations_dir)
    controller = container.controller

    try:
        suite, _, instance = await _register_tree_and_agent(container)
        monkeypatch.setattr(
            controller._audit_factory,
            "evaluation_completed",
            _fail_evaluation_audit,
        )

        with pytest.raises(RuntimeError, match="injected evaluation audit failure"):
            await controller.evaluate_instance(
                _evaluation_command(
                    suite=suite,
                    instance=instance,
                    output="Use pytest.",
                    idempotency_key="failing-evaluation-1",
                )
            )

        async with container.uow_factory(read_only=True) as uow:
            assert await uow.specs.get(instance.instance_id, instance.revision) is None
        audit = await controller.query_audit(AuditQuery(page_size=100))
        assert all(
            event.event_type
            not in {
                AuditEventType.SPEC_EXPORTED,
                AuditEventType.EVALUATION_COMPLETED,
            }
            for event in audit.items
        )

        with sqlite3.connect(container.migration_runner.database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM evaluation_reports"
            ).fetchone()
        assert row is not None
        assert row[0] == 0
    finally:
        await container.close()
