"""Condition-free human review package tests."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.blind_review import BlindReviewError, build_blind_review_package
from experiments.cli import main
from experiments.contracts import BlindReviewItem, RunStatus
from experiments.loader import LoadedExperimentDataset
from experiments.planning import load_execution_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"


class _ReviewIdFactory:
    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> UUID:
        value = uuid5(NAMESPACE_URL, f"blind-review-test-{self._next}")
        self._next += 1
        return value


def test_blind_package_separates_public_items_from_condition_mapping(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    review_root = tmp_path / "public-review"
    mapping_root = tmp_path / "private-mapping"
    result = build_blind_review_package(
        dataset=dataset,
        plan=plan,
        run_store=ArtifactStore(completed_fake_run_root),
        review_root=review_root,
        mapping_root=mapping_root,
        id_factory=_ReviewIdFactory(),
    )

    assert result.package.item_count == 240
    assert len(result.mapping.records) == 240
    assert result.package.mapping_checksum == result.mapping.mapping_checksum
    assert len(ArtifactStore(review_root).list_files(plan.experiment_id)) == 0
    assert (
        len(ArtifactStore(review_root).list_files(f"reviews/{plan.experiment_id}"))
        == 241
    )
    assert ArtifactStore(mapping_root).list_files("mappings") == (
        f"mappings/{plan.experiment_id}.json",
    )

    first = ArtifactStore(review_root).read_model(
        result.package.files[0].path,
        BlindReviewItem,
    )
    public_fields = set(first.model_dump(mode="json"))
    assert {
        "condition",
        "run_id",
        "repetition",
        "execution_order",
        "prompt_hash",
        "agent_spec_checksum",
    }.isdisjoint(public_fields)

    replay = build_blind_review_package(
        dataset=dataset,
        plan=plan,
        run_store=ArtifactStore(completed_fake_run_root),
        review_root=review_root,
        mapping_root=mapping_root,
        id_factory=lambda: pytest.fail("replay must reuse the private mapping"),
    )
    assert replay == result


def test_mapping_tamper_and_nested_roots_are_rejected(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
) -> None:
    plan = load_execution_plan(PLAN_PATH, dataset)
    review_root = tmp_path / "public"
    mapping_root = tmp_path / "private"
    result = build_blind_review_package(
        dataset=dataset,
        plan=plan,
        run_store=ArtifactStore(completed_fake_run_root),
        review_root=review_root,
        mapping_root=mapping_root,
        id_factory=_ReviewIdFactory(),
    )
    mapping_path = mapping_root / "mappings" / f"{plan.experiment_id}.json"
    changed_record = result.mapping.records[0].model_copy(
        update={"condition": result.mapping.records[1].condition}
    )
    changed = result.mapping.model_copy(
        update={"records": (changed_record, *result.mapping.records[1:])}
    )
    changed = changed.model_copy(
        update={
            "mapping_checksum": "0" * 64,
        }
    )
    mapping_path.write_bytes(canonical_model_bytes(changed))
    with pytest.raises(BlindReviewError, match="identity is stale"):
        build_blind_review_package(
            dataset=dataset,
            plan=plan,
            run_store=ArtifactStore(completed_fake_run_root),
            review_root=review_root,
            mapping_root=mapping_root,
        )

    with pytest.raises(BlindReviewError, match="separate roots"):
        build_blind_review_package(
            dataset=dataset,
            plan=plan,
            run_store=ArtifactStore(completed_fake_run_root),
            review_root=tmp_path / "combined",
            mapping_root=tmp_path / "combined" / "mapping",
        )


def test_failed_review_item_cannot_claim_model_output(
    dataset: LoadedExperimentDataset,
) -> None:
    task = dataset.tasks[0]
    rubric = next(item for item in dataset.rubrics if item.task_id == task.task_id)
    fixture = next(
        item for item in dataset.knowledge if item.domain_id == task.domain_id
    )
    facts = {item.fact_id: item for item in fixture.facts}
    with pytest.raises(ValueError, match="failed blind review item"):
        BlindReviewItem(
            review_item_id=uuid5(NAMESPACE_URL, "failed-review"),
            task_id=task.task_id,
            scenario=task.scenario,
            instruction=task.instruction,
            reader_profile=task.reader_profile,
            run_status=RunStatus.INVALID_RESPONSE,
            output_text="should not be shown",
            structured_output=None,
            required_facts=tuple(facts[item] for item in rubric.required_fact_ids),
            rubric=rubric,
        )


def test_blind_review_cli_publishes_complete_separated_package(
    dataset: LoadedExperimentDataset,
    completed_fake_run_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    review_root = tmp_path / "cli-public-review"
    mapping_root = tmp_path / "cli-private-mapping"

    assert (
        main(
            [
                "build-blind-review",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(PLAN_PATH),
                "--runs-root",
                str(completed_fake_run_root),
                "--review-root",
                str(review_root),
                "--mapping-root",
                str(mapping_root),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "blind review package published: items=240" in output
    assert len(ArtifactStore(review_root).list_files("reviews")) == 241
    assert len(ArtifactStore(mapping_root).list_files("mappings")) == 1
