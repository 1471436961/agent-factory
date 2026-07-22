"""Real HTTP contract and restart recovery test for the M2 governance loop."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import httpx
import pytest

from agent_factory.domain.common import checksum_knowledge_content
from agent_factory.interfaces.api.main import create_app
from agent_factory.settings import Settings


def _settings(tmp_path: Path, migrations_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
        }
    )


@asynccontextmanager
async def _running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Actor-ID": "owner"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _evaluation_body(
    *,
    revision: int,
    suite: dict[str, str],
    output_text: str,
) -> dict[str, object]:
    return {
        "expected_revision": revision,
        "suite": suite,
        "runtime_model": "test-model-1",
        "case_results": [
            {
                "case_id": "testing-strategy",
                "output_text": output_text,
            }
        ],
    }


async def _evaluate(
    client: httpx.AsyncClient,
    *,
    instance_id: str,
    revision: int,
    suite: dict[str, str],
    output_text: str,
    idempotency_key: str,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/instances/{instance_id}/evaluations",
        json=_evaluation_body(
            revision=revision,
            suite=suite,
            output_text=output_text,
        ),
        headers=_headers(idempotency_key),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


async def _review(
    client: httpx.AsyncClient,
    *,
    report_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/evaluation-reports/{report_id}/reviews",
        json={
            "decision": "approved",
            "comment": "Deterministic rules passed; evidence was reviewed.",
        },
        headers=_headers(idempotency_key),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


async def _record_outcome(
    client: httpx.AsyncClient,
    *,
    instance_id: str,
    report_id: str,
    task_id: str,
    passed: bool,
    idempotency_key: str,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/instances/{instance_id}/task-outcomes",
        json={
            "expected_revision": 2,
            "task_id": task_id,
            "skill_node_id": "junior-engineer",
            "passed": passed,
            "evaluation_report_id": report_id,
        },
        headers=_headers(idempotency_key),
    )


@pytest.mark.asyncio
async def test_m2_rest_governance_loop_survives_restart(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    settings = _settings(tmp_path, migrations_dir)
    suite_body = {
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
    knowledge_content = "Use pytest, deterministic fixtures, and layered tests."
    knowledge_body = {
        "knowledge_id": "engineering-guide",
        "version": "1.0.0",
        "name": "Engineering Guide",
        "kind": "document",
        "content": knowledge_content,
        "checksum": checksum_knowledge_content(knowledge_content),
    }

    async with _running_client(settings) as client:
        suite_response = await client.post(
            "/api/v1/evaluation-suites",
            json=suite_body,
            headers=_headers("register-suite-1"),
        )
        assert suite_response.status_code == 201, suite_response.text
        suite_record = suite_response.json()
        suite_ref = {
            "suite_id": suite_record["suite_id"],
            "version": suite_record["version"],
            "checksum": suite_record["checksum"],
        }

        tree_body = {
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
                    "evaluation_suite": suite_ref,
                    "observation_policy": {
                        "window_size": 4,
                        "minimum_samples": 3,
                        "consecutive_failures": 2,
                        "failure_rate_threshold": 0.75,
                    },
                }
            ],
        }
        tree_response = await client.post(
            "/api/v1/skill-trees",
            json=tree_body,
            headers=_headers("register-tree-1"),
        )
        assert tree_response.status_code == 201, tree_response.text
        tree_record = tree_response.json()
        tree_ref = {
            "tree_id": tree_record["tree_id"],
            "version": tree_record["version"],
            "checksum": tree_record["checksum"],
        }

        prototype_body = {
            "prototype_id": "engineer-agent",
            "version": "1.0.0",
            "definition": {
                "agent_type": "engineer-agent",
                "role": "Software Engineer",
                "system_prompt": "Produce technically verifiable engineering work.",
                "tools": [],
                "capabilities": ["can-code"],
                "output_schema": {"type": "object"},
                "knowledge_slots": [],
                "metadata": {},
            },
            "skill_tree": tree_ref,
            "publish": True,
        }
        prototype_response = await client.post(
            "/api/v1/prototypes",
            json=prototype_body,
            headers=_headers("register-engineer-prototype-1"),
        )
        assert prototype_response.status_code == 201, prototype_response.text
        assert prototype_response.json()["skill_tree"] == tree_ref
        assert prototype_response.json()["status"] == "published"

        knowledge_response = await client.post(
            "/api/v1/knowledge",
            json=knowledge_body,
            headers=_headers("register-engineering-guide-1"),
        )
        assert knowledge_response.status_code == 201, knowledge_response.text

        clone_response = await client.post(
            "/api/v1/prototypes/engineer-agent/versions/1.0.0/instances",
            json={},
            headers=_headers("clone-engineer-agent-1"),
        )
        assert clone_response.status_code == 201, clone_response.text
        instance_id = clone_response.json()["instance_id"]

        promotion_report = await _evaluate(
            client,
            instance_id=instance_id,
            revision=1,
            suite=suite_ref,
            output_text="Use pytest with deterministic fixtures.",
            idempotency_key="evaluate-for-promotion-1",
        )
        assert promotion_report["decision"] == "review-required"
        promotion_review = await _review(
            client,
            report_id=str(promotion_report["report_id"]),
            idempotency_key="review-for-promotion-1",
        )

        promotion_body = {
            "expected_revision": 1,
            "target_node_id": "junior-engineer",
            "evaluation_report_id": promotion_report["report_id"],
            "evaluation_review_id": promotion_review["review_id"],
            "knowledge_selections": [
                {
                    "slot_name": "engineering-guide",
                    "knowledge_id": "engineering-guide",
                    "version": "1.0.0",
                }
            ],
        }
        promotion_response = await client.post(
            f"/api/v1/instances/{instance_id}/promotions",
            json=promotion_body,
            headers=_headers("promote-junior-engineer-1"),
        )
        assert promotion_response.status_code == 200, promotion_response.text
        promotion_record = promotion_response.json()
        assert promotion_record["revision"] == 2
        assert promotion_record["active_skill_nodes"] == ["junior-engineer"]

        passed_report = await _evaluate(
            client,
            instance_id=instance_id,
            revision=2,
            suite=suite_ref,
            output_text="Use pytest and deterministic fixtures.",
            idempotency_key="evaluate-observation-pass-1",
        )
        await _review(
            client,
            report_id=str(passed_report["report_id"]),
            idempotency_key="review-observation-pass-1",
        )
        first_outcome = await _record_outcome(
            client,
            instance_id=instance_id,
            report_id=str(passed_report["report_id"]),
            task_id="00000000-0000-0000-0000-000000000901",
            passed=True,
            idempotency_key="record-observation-pass-1",
        )
        assert first_outcome.status_code == 200, first_outcome.text
        assert first_outcome.json()["degraded"] is False

        failed_report_1 = await _evaluate(
            client,
            instance_id=instance_id,
            revision=2,
            suite=suite_ref,
            output_text="Use deterministic fixtures.",
            idempotency_key="evaluate-observation-fail-1",
        )
        assert failed_report_1["decision"] == "fail"
        second_outcome = await _record_outcome(
            client,
            instance_id=instance_id,
            report_id=str(failed_report_1["report_id"]),
            task_id="00000000-0000-0000-0000-000000000902",
            passed=False,
            idempotency_key="record-observation-fail-1",
        )
        assert second_outcome.status_code == 200, second_outcome.text
        assert second_outcome.json()["degraded"] is False

        failed_report_2 = await _evaluate(
            client,
            instance_id=instance_id,
            revision=2,
            suite=suite_ref,
            output_text="Use layered checks.",
            idempotency_key="evaluate-observation-fail-2",
        )
        final_outcome_body = {
            "expected_revision": 2,
            "task_id": "00000000-0000-0000-0000-000000000903",
            "skill_node_id": "junior-engineer",
            "passed": False,
            "evaluation_report_id": failed_report_2["report_id"],
        }
        final_outcome = await client.post(
            f"/api/v1/instances/{instance_id}/task-outcomes",
            json=final_outcome_body,
            headers=_headers("record-observation-fail-2"),
        )
        assert final_outcome.status_code == 200, final_outcome.text
        final_outcome_record = final_outcome.json()
        assert final_outcome_record["degraded"] is True
        assert final_outcome_record["resulting_revision"] == 3
        assert final_outcome_record["removed_nodes"] == ["junior-engineer"]
        assert final_outcome_record["removed_binding_slots"] == ["engineering-guide"]

        spec_response = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 3},
            headers=_headers(),
        )
        assert spec_response.status_code == 200, spec_response.text
        spec_record = spec_response.json()
        assert spec_record["active_skill_nodes"] == []
        assert spec_record["tools"] == []
        assert spec_record["knowledge"] == []
        assert spec_record["system_prompt"] == (
            "Produce technically verifiable engineering work."
        )

        audit_response = await client.get(
            "/api/v1/audit-events",
            params={"page_size": 100},
        )
        assert audit_response.status_code == 200, audit_response.text
        audit_record = audit_response.json()
        event_types = {event["event_type"] for event in audit_record["items"]}
        assert {
            "evaluation-suite.registered",
            "skill-tree.registered",
            "evaluation.completed",
            "evaluation.reviewed",
            "skill.promoted",
            "task-outcome.recorded",
            "skill.degraded",
        } <= event_types

    async with _running_client(settings) as client:
        persisted_suite = await client.get(
            "/api/v1/evaluation-suites/engineer-readiness/versions/1.0.0"
        )
        persisted_tree = await client.get(
            "/api/v1/skill-trees/engineer-skills/versions/1.0.0"
        )
        assert persisted_suite.status_code == 200
        assert persisted_suite.json() == suite_record
        assert persisted_tree.status_code == 200
        assert persisted_tree.json() == tree_record

        replay_report = await client.post(
            f"/api/v1/instances/{instance_id}/evaluations",
            json=_evaluation_body(
                revision=1,
                suite=suite_ref,
                output_text="Use pytest with deterministic fixtures.",
            ),
            headers=_headers("evaluate-for-promotion-1"),
        )
        replay_review = await client.post(
            f"/api/v1/evaluation-reports/{promotion_report['report_id']}/reviews",
            json={
                "decision": "approved",
                "comment": "Deterministic rules passed; evidence was reviewed.",
            },
            headers=_headers("review-for-promotion-1"),
        )
        replay_promotion = await client.post(
            f"/api/v1/instances/{instance_id}/promotions",
            json=promotion_body,
            headers=_headers("promote-junior-engineer-1"),
        )
        replay_outcome = await client.post(
            f"/api/v1/instances/{instance_id}/task-outcomes",
            json=final_outcome_body,
            headers=_headers("record-observation-fail-2"),
        )
        assert replay_report.status_code == 201
        assert replay_report.json() == promotion_report
        assert replay_review.status_code == 201
        assert replay_review.json() == promotion_review
        assert replay_promotion.status_code == 200
        assert replay_promotion.json() == promotion_record
        assert replay_outcome.status_code == 200
        assert replay_outcome.json() == final_outcome_record

        persisted_spec = await client.post(
            f"/api/v1/instances/{instance_id}/spec-exports",
            json={"revision": 3},
            headers=_headers(),
        )
        persisted_audit = await client.get(
            "/api/v1/audit-events",
            params={"page_size": 100},
        )
        assert persisted_spec.status_code == 200
        assert persisted_spec.json() == spec_record
        assert persisted_audit.status_code == 200
        assert persisted_audit.json() == audit_record


@pytest.mark.asyncio
async def test_m2_rest_errors_use_stable_envelopes(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    async with _running_client(_settings(tmp_path, migrations_dir)) as client:
        missing_suite = await client.get(
            "/api/v1/evaluation-suites/missing-suite/versions/1.0.0"
        )
        invalid_review = await client.post(
            "/api/v1/evaluation-reports/00000000-0000-0000-0000-000000000999/reviews",
            json={
                "decision": "approved",
                "comment": "private-review-comment",
                "unknown": "private-evidence",
            },
            headers=_headers(),
        )

        assert missing_suite.status_code == 404
        assert missing_suite.json()["error"]["code"] == ("EVALUATION_SUITE_NOT_FOUND")
        assert invalid_review.status_code == 422
        assert invalid_review.json()["error"]["code"] == ("REQUEST_VALIDATION_FAILED")
        assert "private-review-comment" not in invalid_review.text
        assert "private-evidence" not in invalid_review.text
