"""SQLite repositories for immutable M2 skill-governance records."""

from __future__ import annotations

import sqlite3
from typing import NoReturn
from uuid import UUID

import aiosqlite

from agent_factory.domain.common import sha256_model
from agent_factory.domain.errors import (
    EvaluationReportAlreadyExistsError,
    EvaluationReviewConflictError,
    EvaluationSuiteAlreadyExistsError,
    FactoryError,
    SkillTreeAlreadyExistsError,
    TaskOutcomeAlreadyExistsError,
)
from agent_factory.domain.evaluation import (
    EvaluationReport,
    EvaluationReview,
    EvaluationSuite,
)
from agent_factory.domain.services.evaluation import checksum_evaluation_suite
from agent_factory.domain.services.skills import checksum_skill_tree
from agent_factory.domain.skills import SkillTree, TaskOutcome
from agent_factory.infrastructure.sqlite.codec import (
    decode_model,
    encode_model,
    raise_database_error,
    require_projection,
)
from agent_factory.infrastructure.sqlite.repository_base import (
    SqliteRepository,
    format_datetime,
)


class SqliteEvaluationSuiteRepository(SqliteRepository):
    repository_name = "evaluation-suites"

    async def add(self, suite: EvaluationSuite) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO evaluation_suites (
                    suite_id, version, payload_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    suite.suite_id,
                    suite.version,
                    encode_model(suite),
                    suite.checksum,
                    format_datetime(suite.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            _raise_integrity(
                exc,
                duplicate=EvaluationSuiteAlreadyExistsError(
                    details={"suite_id": suite.suite_id, "version": suite.version}
                ),
                repository=self.repository_name,
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(self, suite_id: str, version: str) -> EvaluationSuite | None:
        row = await self._fetchone(
            """
            SELECT suite_id, version, payload_json, checksum, created_at
            FROM evaluation_suites
            WHERE suite_id = ? AND version = ?
            """,
            (suite_id, version),
        )
        return None if row is None else self._decode(row)

    def _decode(self, row: aiosqlite.Row) -> EvaluationSuite:
        suite = decode_model(
            str(row["payload_json"]),
            EvaluationSuite,
            repository=self.repository_name,
        )
        projections = {
            "suite_id": suite.suite_id == row["suite_id"],
            "version": suite.version == row["version"],
            "checksum": suite.checksum == row["checksum"],
            "definition_checksum": checksum_evaluation_suite(suite) == suite.checksum,
            "created_at": format_datetime(suite.created_at) == row["created_at"],
        }
        _require_projections(projections, repository=self.repository_name)
        return suite


class SqliteSkillTreeRepository(SqliteRepository):
    repository_name = "skill-trees"

    async def add(self, tree: SkillTree) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO skill_trees (
                    tree_id, version, payload_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    tree.tree_id,
                    tree.version,
                    encode_model(tree),
                    tree.checksum,
                    format_datetime(tree.created_at),
                ),
            )
            for node in tree.nodes:
                await self._connection.execute(
                    """
                    INSERT INTO skill_node_suites (
                        tree_id, tree_version, node_id,
                        suite_id, suite_version, suite_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tree.tree_id,
                        tree.version,
                        node.node_id,
                        node.evaluation_suite.suite_id,
                        node.evaluation_suite.version,
                        node.evaluation_suite.checksum,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            _raise_integrity(
                exc,
                duplicate=SkillTreeAlreadyExistsError(
                    details={"tree_id": tree.tree_id, "version": tree.version}
                ),
                repository=self.repository_name,
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(self, tree_id: str, version: str) -> SkillTree | None:
        row = await self._fetchone(
            """
            SELECT tree_id, version, payload_json, checksum, created_at
            FROM skill_trees
            WHERE tree_id = ? AND version = ?
            """,
            (tree_id, version),
        )
        if row is None:
            return None
        tree = self._decode(row)
        suite_rows = await self._fetchall(
            """
            SELECT node_id, suite_id, suite_version, suite_checksum
            FROM skill_node_suites
            WHERE tree_id = ? AND tree_version = ?
            ORDER BY node_id
            """,
            (tree_id, version),
        )
        projected = tuple(
            (
                str(item["node_id"]),
                str(item["suite_id"]),
                str(item["suite_version"]),
                str(item["suite_checksum"]),
            )
            for item in suite_rows
        )
        expected = tuple(
            (
                node.node_id,
                node.evaluation_suite.suite_id,
                node.evaluation_suite.version,
                node.evaluation_suite.checksum,
            )
            for node in tree.nodes
        )
        require_projection(
            projected == expected,
            repository=self.repository_name,
            field="node_evaluation_suites",
        )
        return tree

    def _decode(self, row: aiosqlite.Row) -> SkillTree:
        tree = decode_model(
            str(row["payload_json"]),
            SkillTree,
            repository=self.repository_name,
        )
        projections = {
            "tree_id": tree.tree_id == row["tree_id"],
            "version": tree.version == row["version"],
            "checksum": tree.checksum == row["checksum"],
            "definition_checksum": checksum_skill_tree(tree) == tree.checksum,
            "created_at": format_datetime(tree.created_at) == row["created_at"],
        }
        _require_projections(projections, repository=self.repository_name)
        return tree


class SqliteEvaluationReportRepository(SqliteRepository):
    repository_name = "evaluation-reports"

    async def add(self, report: EvaluationReport) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO evaluation_reports (
                    report_id, instance_id, instance_revision, agent_spec_checksum,
                    tree_id, tree_version, tree_checksum,
                    suite_id, suite_version, suite_checksum,
                    decision, payload_json, payload_checksum,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(report.report_id),
                    str(report.instance_id),
                    report.instance_revision,
                    report.agent_spec_checksum,
                    report.skill_tree.tree_id,
                    report.skill_tree.version,
                    report.skill_tree.checksum,
                    report.suite.suite_id,
                    report.suite.version,
                    report.suite.checksum,
                    report.decision.value,
                    encode_model(report),
                    sha256_model(report),
                    format_datetime(report.started_at),
                    format_datetime(report.completed_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            _raise_integrity(
                exc,
                duplicate=EvaluationReportAlreadyExistsError(
                    details={"report_id": str(report.report_id)}
                ),
                repository=self.repository_name,
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(self, report_id: UUID) -> EvaluationReport | None:
        row = await self._fetchone(
            """
            SELECT report_id, instance_id, instance_revision, agent_spec_checksum,
                   tree_id, tree_version, tree_checksum,
                   suite_id, suite_version, suite_checksum,
                   decision, payload_json, payload_checksum,
                   started_at, completed_at
            FROM evaluation_reports
            WHERE report_id = ?
            """,
            (str(report_id),),
        )
        return None if row is None else self._decode(row)

    def _decode(self, row: aiosqlite.Row) -> EvaluationReport:
        report = decode_model(
            str(row["payload_json"]),
            EvaluationReport,
            repository=self.repository_name,
        )
        projections = {
            "report_id": str(report.report_id) == row["report_id"],
            "instance_id": str(report.instance_id) == row["instance_id"],
            "instance_revision": report.instance_revision == row["instance_revision"],
            "agent_spec_checksum": (
                report.agent_spec_checksum == row["agent_spec_checksum"]
            ),
            "skill_tree": (
                report.skill_tree.tree_id,
                report.skill_tree.version,
                report.skill_tree.checksum,
            )
            == (row["tree_id"], row["tree_version"], row["tree_checksum"]),
            "suite": (
                report.suite.suite_id,
                report.suite.version,
                report.suite.checksum,
            )
            == (row["suite_id"], row["suite_version"], row["suite_checksum"]),
            "decision": report.decision.value == row["decision"],
            "payload_checksum": sha256_model(report) == row["payload_checksum"],
            "started_at": format_datetime(report.started_at) == row["started_at"],
            "completed_at": (
                format_datetime(report.completed_at) == row["completed_at"]
            ),
        }
        _require_projections(projections, repository=self.repository_name)
        return report


class SqliteEvaluationReviewRepository(SqliteRepository):
    repository_name = "evaluation-reviews"

    async def add(self, review: EvaluationReview) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO evaluation_reviews (
                    review_id, report_id, decision, payload_json,
                    payload_checksum, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(review.review_id),
                    str(review.report_id),
                    review.decision.value,
                    encode_model(review),
                    sha256_model(review),
                    format_datetime(review.reviewed_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            _raise_integrity(
                exc,
                duplicate=EvaluationReviewConflictError(
                    details={"report_id": str(review.report_id)}
                ),
                repository=self.repository_name,
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(self, review_id: UUID) -> EvaluationReview | None:
        return await self._get_where("review_id", review_id)

    async def get_for_report(self, report_id: UUID) -> EvaluationReview | None:
        return await self._get_where("report_id", report_id)

    async def _get_where(self, field: str, value: UUID) -> EvaluationReview | None:
        row = await self._fetchone(
            f"""
            SELECT review_id, report_id, decision, payload_json,
                   payload_checksum, reviewed_at
            FROM evaluation_reviews
            WHERE {field} = ?
            """,
            (str(value),),
        )
        return None if row is None else self._decode(row)

    def _decode(self, row: aiosqlite.Row) -> EvaluationReview:
        review = decode_model(
            str(row["payload_json"]),
            EvaluationReview,
            repository=self.repository_name,
        )
        projections = {
            "review_id": str(review.review_id) == row["review_id"],
            "report_id": str(review.report_id) == row["report_id"],
            "decision": review.decision.value == row["decision"],
            "payload_checksum": sha256_model(review) == row["payload_checksum"],
            "reviewed_at": format_datetime(review.reviewed_at) == row["reviewed_at"],
        }
        _require_projections(projections, repository=self.repository_name)
        return review


class SqliteTaskOutcomeRepository(SqliteRepository):
    repository_name = "task-outcomes"

    async def append(
        self,
        *,
        instance_id: UUID,
        instance_revision: int,
        outcome: TaskOutcome,
    ) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO task_outcomes (
                    task_id, instance_id, instance_revision, skill_node_id,
                    passed, evaluation_report_id, payload_json,
                    payload_checksum, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(outcome.task_id),
                    str(instance_id),
                    instance_revision,
                    outcome.skill_node_id,
                    int(outcome.passed),
                    str(outcome.evaluation_report_id),
                    encode_model(outcome),
                    sha256_model(outcome),
                    format_datetime(outcome.recorded_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            _raise_integrity(
                exc,
                duplicate=TaskOutcomeAlreadyExistsError(
                    details={
                        "task_id": str(outcome.task_id),
                        "instance_id": str(instance_id),
                        "skill_node_id": outcome.skill_node_id,
                    }
                ),
                repository=self.repository_name,
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def list_for_node(
        self,
        *,
        instance_id: UUID,
        skill_node_id: str,
        limit: int,
    ) -> tuple[TaskOutcome, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self._fetchall(
            """
            SELECT * FROM (
                SELECT task_id, instance_id, instance_revision, skill_node_id,
                       passed, evaluation_report_id, payload_json,
                       payload_checksum, recorded_at
                FROM task_outcomes
                WHERE instance_id = ? AND skill_node_id = ?
                ORDER BY recorded_at DESC, task_id DESC
                LIMIT ?
            ) AS latest
            ORDER BY recorded_at, task_id
            """,
            (str(instance_id), skill_node_id, limit),
        )
        return tuple(self._decode(row) for row in rows)

    def _decode(self, row: aiosqlite.Row) -> TaskOutcome:
        outcome = decode_model(
            str(row["payload_json"]),
            TaskOutcome,
            repository=self.repository_name,
        )
        projections = {
            "task_id": str(outcome.task_id) == row["task_id"],
            "skill_node_id": outcome.skill_node_id == row["skill_node_id"],
            "passed": int(outcome.passed) == row["passed"],
            "evaluation_report_id": (
                str(outcome.evaluation_report_id) == row["evaluation_report_id"]
            ),
            "recorded_at": format_datetime(outcome.recorded_at) == row["recorded_at"],
            "payload_checksum": sha256_model(outcome) == row["payload_checksum"],
        }
        _require_projections(projections, repository=self.repository_name)
        return outcome


def _require_projections(
    projections: dict[str, bool],
    *,
    repository: str,
) -> None:
    for field, valid in projections.items():
        require_projection(valid, repository=repository, field=field)


def _raise_integrity(
    exc: sqlite3.IntegrityError,
    *,
    duplicate: FactoryError,
    repository: str,
) -> NoReturn:
    if exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_PRIMARYKEY",
        "SQLITE_CONSTRAINT_UNIQUE",
    }:
        raise duplicate from exc
    raise_database_error(repository, exc)
