"""M2 SQLite repository, projection, and transaction integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiosqlite
import pytest

from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import (
    EvaluationDecision,
    InstanceStatus,
    PrototypeStatus,
    ReviewDecision,
    RuleKind,
)
from agent_factory.domain.errors import (
    EvaluationReportAlreadyExistsError,
    EvaluationReviewConflictError,
    EvaluationSuiteAlreadyExistsError,
    RepositoryUnavailableError,
    SkillTreeAlreadyExistsError,
    TaskOutcomeAlreadyExistsError,
)
from agent_factory.domain.evaluation import (
    CaseResultRef,
    EvaluationCase,
    EvaluationReport,
    EvaluationReview,
    EvaluationRule,
    EvaluationSuite,
    EvaluationSuiteDraft,
    RuleResult,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    PrototypeRef,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.evaluation import checksum_evaluation_suite
from agent_factory.domain.services.skills import checksum_skill_tree
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import (
    SkillNode,
    SkillTree,
    SkillTreeDraft,
    TaskOutcome,
)
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000601")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000602")
REVIEW_ID = UUID("00000000-0000-0000-0000-000000000603")


class FrozenClock:
    def now(self) -> datetime:
        return NOW


async def _factory(
    tmp_path: Path,
    migrations_dir: Path,
) -> tuple[Path, SqliteUnitOfWorkFactory]:
    database_path = tmp_path / "governance.db"
    await SqliteMigrationRunner(
        database_path,
        migrations_dir,
        FrozenClock(),
    ).migrate()
    return database_path, SqliteUnitOfWorkFactory(database_path)


def _suite() -> EvaluationSuite:
    draft = EvaluationSuiteDraft(
        suite_id="engineer-suite",
        version="1.0.0",
        rules=(
            EvaluationRule(
                rule_id="required-test",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ["pytest"]},
            ),
        ),
        cases=(EvaluationCase(case_id="case-one", input="Implement a change."),),
    )
    return EvaluationSuite.model_validate(
        {
            **draft.model_dump(mode="python"),
            "checksum": checksum_evaluation_suite(draft),
            "created_at": NOW,
            "created_by": "owner",
        }
    )


def _tree(suite: EvaluationSuite) -> SkillTree:
    suite_ref = EvaluationSuiteRef(
        suite_id=suite.suite_id,
        version=suite.version,
        checksum=suite.checksum,
    )
    draft = SkillTreeDraft(
        tree_id="engineer-skills",
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="junior-engineer",
                display_name="Junior Engineer",
                evaluation_suite=suite_ref,
            ),
            SkillNode(
                node_id="mid-engineer",
                display_name="Mid Engineer",
                parents=frozenset({"junior-engineer"}),
                granted_tools=frozenset({"test-runner"}),
                evaluation_suite=suite_ref,
            ),
        ),
    )
    return SkillTree.model_validate(
        {
            **draft.model_dump(mode="python"),
            "checksum": checksum_skill_tree(draft),
            "created_at": NOW,
            "created_by": "owner",
        }
    )


def _production_records(
    tree: SkillTree,
) -> tuple[AgentPrototype, AgentInstance]:
    definition = AgentDefinition(
        agent_type="engineer-agent",
        role="Engineer",
        system_prompt="Implement and test.",
    )
    tree_ref = SkillTreeRef(
        tree_id=tree.tree_id,
        version=tree.version,
        checksum=tree.checksum,
    )
    prototype = AgentPrototype(
        prototype_id="engineer-agent",
        version="1.0.0",
        status=PrototypeStatus.PUBLISHED,
        definition=definition,
        skill_tree=tree_ref,
        checksum=sha256_model(definition),
        created_at=NOW,
        created_by="owner",
        published_at=NOW,
    )
    instance = AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id=prototype.prototype_id,
            version=prototype.version,
            checksum=prototype.checksum,
        ),
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=definition,
        skill_tree=tree_ref,
        created_at=NOW,
        updated_at=NOW,
        created_by="owner",
    )
    return prototype, instance


def _report(
    tree: SkillTree,
    suite: EvaluationSuite,
    *,
    spec_checksum: str,
) -> EvaluationReport:
    return EvaluationReport(
        report_id=REPORT_ID,
        instance_id=INSTANCE_ID,
        instance_revision=1,
        agent_spec_checksum=spec_checksum,
        skill_tree=SkillTreeRef(
            tree_id=tree.tree_id,
            version=tree.version,
            checksum=tree.checksum,
        ),
        suite=EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        ),
        runtime_model="fixture-runtime",
        case_results=(CaseResultRef(case_id="case-one", checksum="b" * 64),),
        rule_results=(
            RuleResult(
                rule_id="required-test",
                case_id="case-one",
                passed=True,
                score=1.0,
            ),
        ),
        hard_rules_passed=True,
        soft_score=1.0,
        decision=EvaluationDecision.PASS,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )


async def _seed_chain(
    factory: SqliteUnitOfWorkFactory,
) -> tuple[
    EvaluationSuite,
    SkillTree,
    AgentPrototype,
    AgentInstance,
    EvaluationReport,
]:
    suite = _suite()
    tree = _tree(suite)
    prototype, instance = _production_records(tree)
    spec = AgentSpecBuilder().build(instance=instance, tools=(), generated_at=NOW)
    report = _report(tree, suite, spec_checksum=spec.spec_checksum)
    async with factory() as uow:
        await uow.evaluation_suites.add(suite)
        await uow.skill_trees.add(tree)
        await uow.prototypes.add(prototype)
        await uow.instances.add(instance)
        assert await uow.specs.add_if_absent(spec)
        await uow.evaluation_reports.add(report)
        await uow.commit()
    return suite, tree, prototype, instance, report


@pytest.mark.asyncio
async def test_governance_snapshots_round_trip_after_restart(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database_path, factory = await _factory(tmp_path, migrations_dir)
    suite, tree, prototype, instance, report = await _seed_chain(factory)
    review = EvaluationReview(
        review_id=REVIEW_ID,
        report_id=report.report_id,
        reviewer="owner",
        decision=ReviewDecision.APPROVED,
        comment="Evidence accepted.",
        reviewed_at=NOW + timedelta(seconds=2),
    )
    outcome = TaskOutcome(
        task_id=UUID("00000000-0000-0000-0000-000000000604"),
        skill_node_id="mid-engineer",
        passed=True,
        evaluation_report_id=report.report_id,
        recorded_at=NOW + timedelta(seconds=3),
    )
    async with factory() as uow:
        await uow.evaluation_reviews.add(review)
        await uow.task_outcomes.append(
            instance_id=instance.instance_id,
            instance_revision=instance.revision,
            outcome=outcome,
        )
        await uow.commit()

    restarted_factory = SqliteUnitOfWorkFactory(database_path)
    async with restarted_factory(read_only=True) as uow:
        assert await uow.evaluation_suites.get(suite.suite_id, suite.version) == suite
        assert await uow.skill_trees.get(tree.tree_id, tree.version) == tree
        assert await uow.prototypes.get(prototype.prototype_id, prototype.version) == (
            prototype
        )
        assert await uow.instances.get(instance.instance_id) == instance
        assert await uow.evaluation_reports.get(report.report_id) == report
        assert await uow.evaluation_reviews.get(review.review_id) == review
        assert await uow.evaluation_reviews.get_for_report(report.report_id) == review
        assert await uow.task_outcomes.list_for_node(
            instance_id=instance.instance_id,
            instance_revision=instance.revision,
            skill_node_id=outcome.skill_node_id,
            limit=10,
        ) == (outcome,)


@pytest.mark.asyncio
async def test_governance_unique_constraints_have_stable_errors(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    suite, tree, _, instance, report = await _seed_chain(factory)
    review = EvaluationReview(
        review_id=REVIEW_ID,
        report_id=report.report_id,
        reviewer="owner",
        decision=ReviewDecision.APPROVED,
        reviewed_at=NOW,
    )
    outcome = TaskOutcome(
        task_id=UUID("00000000-0000-0000-0000-000000000605"),
        skill_node_id="mid-engineer",
        passed=True,
        evaluation_report_id=report.report_id,
        recorded_at=NOW,
    )
    async with factory() as uow:
        with pytest.raises(EvaluationSuiteAlreadyExistsError):
            await uow.evaluation_suites.add(suite)
        with pytest.raises(SkillTreeAlreadyExistsError):
            await uow.skill_trees.add(tree)
        with pytest.raises(EvaluationReportAlreadyExistsError):
            await uow.evaluation_reports.add(report)
        await uow.evaluation_reviews.add(review)
        with pytest.raises(EvaluationReviewConflictError):
            await uow.evaluation_reviews.add(
                review.model_copy(
                    update={"review_id": UUID("00000000-0000-0000-0000-000000000606")}
                )
            )
        await uow.task_outcomes.append(
            instance_id=instance.instance_id,
            instance_revision=instance.revision,
            outcome=outcome,
        )
        with pytest.raises(TaskOutcomeAlreadyExistsError):
            await uow.task_outcomes.append(
                instance_id=instance.instance_id,
                instance_revision=instance.revision,
                outcome=outcome,
            )
        with pytest.raises(TaskOutcomeAlreadyExistsError):
            await uow.task_outcomes.append(
                instance_id=instance.instance_id,
                instance_revision=instance.revision,
                outcome=outcome.model_copy(
                    update={"task_id": UUID("00000000-0000-0000-0000-000000000607")}
                ),
            )


@pytest.mark.asyncio
async def test_report_and_outcome_foreign_keys_reject_mismatched_provenance(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    _, tree, prototype, instance, report = await _seed_chain(factory)
    invalid_report = report.model_copy(
        update={
            "report_id": UUID("00000000-0000-0000-0000-000000000607"),
            "agent_spec_checksum": "f" * 64,
        }
    )
    outcome = TaskOutcome(
        task_id=UUID("00000000-0000-0000-0000-000000000608"),
        skill_node_id=tree.nodes[-1].node_id,
        passed=True,
        evaluation_report_id=report.report_id,
        recorded_at=NOW,
    )
    invalid_prototype = prototype.model_copy(
        update={
            "prototype_id": "invalid-engineer-agent",
            "skill_tree": prototype.skill_tree.model_copy(update={"checksum": "f" * 64})
            if prototype.skill_tree is not None
            else None,
        }
    )
    orphan_draft = SkillTreeDraft(
        tree_id="orphan-skills",
        version="1.0.0",
        nodes=(
            SkillNode(
                node_id="orphan-engineer",
                display_name="Orphan Engineer",
                evaluation_suite=EvaluationSuiteRef(
                    suite_id="missing-suite",
                    version="1.0.0",
                    checksum="f" * 64,
                ),
            ),
        ),
    )
    orphan_tree = SkillTree.model_validate(
        {
            **orphan_draft.model_dump(mode="python"),
            "checksum": checksum_skill_tree(orphan_draft),
            "created_at": NOW,
            "created_by": "owner",
        }
    )
    async with factory() as uow:
        with pytest.raises(RepositoryUnavailableError):
            await uow.skill_trees.add(orphan_tree)
    async with factory() as uow:
        with pytest.raises(RepositoryUnavailableError):
            await uow.prototypes.add(invalid_prototype)
    async with factory() as uow:
        with pytest.raises(RepositoryUnavailableError):
            await uow.evaluation_reports.add(invalid_report)
        with pytest.raises(RepositoryUnavailableError):
            await uow.task_outcomes.append(
                instance_id=UUID("00000000-0000-0000-0000-000000000699"),
                instance_revision=instance.revision,
                outcome=outcome,
            )


@pytest.mark.asyncio
async def test_task_outcome_window_returns_latest_items_in_chronological_order(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    _, _, _, instance, report = await _seed_chain(factory)
    outcomes = tuple(
        TaskOutcome(
            task_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
            skill_node_id="mid-engineer",
            passed=index % 2 == 0,
            evaluation_report_id=UUID(f"00000000-0000-0000-0000-{610 + index:012d}"),
            recorded_at=NOW + timedelta(minutes=index),
        )
        for index in range(1, 4)
    )
    async with factory() as uow:
        for outcome in outcomes:
            await uow.evaluation_reports.add(
                report.model_copy(update={"report_id": outcome.evaluation_report_id})
            )
        for outcome in outcomes:
            await uow.task_outcomes.append(
                instance_id=instance.instance_id,
                instance_revision=instance.revision,
                outcome=outcome,
            )
        await uow.commit()

    async with factory(read_only=True) as uow:
        window = await uow.task_outcomes.list_for_node(
            instance_id=instance.instance_id,
            instance_revision=instance.revision,
            skill_node_id="mid-engineer",
            limit=2,
        )
    assert window == outcomes[1:]

    async with factory(read_only=True) as uow:
        assert (
            await uow.task_outcomes.list_for_node(
                instance_id=instance.instance_id,
                instance_revision=instance.revision + 1,
                skill_node_id="mid-engineer",
                limit=2,
            )
            == ()
        )

    async with factory(read_only=True) as uow:
        with pytest.raises(ValueError, match="between 1 and 100"):
            await uow.task_outcomes.list_for_node(
                instance_id=instance.instance_id,
                instance_revision=instance.revision,
                skill_node_id="mid-engineer",
                limit=0,
            )


@pytest.mark.asyncio
async def test_governance_uow_rolls_back_and_rejects_read_only_writes(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    _, factory = await _factory(tmp_path, migrations_dir)
    suite = _suite()
    async with factory() as uow:
        await uow.evaluation_suites.add(suite)

    async with factory(read_only=True) as uow:
        assert await uow.evaluation_suites.get(suite.suite_id, suite.version) is None
        with pytest.raises(RepositoryUnavailableError):
            await uow.evaluation_suites.add(suite)


@pytest.mark.asyncio
async def test_corrupt_skill_tree_projection_is_detected(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    database_path, factory = await _factory(tmp_path, migrations_dir)
    suite, tree, _, _, report = await _seed_chain(factory)
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute("PRAGMA foreign_keys = OFF")
        await connection.execute(
            """
            UPDATE skill_node_suites
            SET suite_checksum = ?
            WHERE tree_id = ? AND tree_version = ? AND node_id = ?
            """,
            ("f" * 64, tree.tree_id, tree.version, tree.nodes[0].node_id),
        )
        await connection.commit()

    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as error:
            await uow.skill_trees.get(tree.tree_id, tree.version)
    assert error.value.details["reason"] == (
        "projection-mismatch:node_evaluation_suites"
    )
    assert suite.checksum != "f" * 64

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """
            UPDATE evaluation_reports
            SET payload_json = json_set(payload_json, '$.runtime_model', 'tampered')
            WHERE report_id = ?
            """,
            (str(report.report_id),),
        )
        await connection.commit()
    async with factory(read_only=True) as uow:
        with pytest.raises(RepositoryUnavailableError) as report_error:
            await uow.evaluation_reports.get(report.report_id)
    assert report_error.value.details["reason"] == (
        "projection-mismatch:payload_checksum"
    )
