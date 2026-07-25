"""Dataset loading, integrity, and path-boundary tests."""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from pathlib import Path
from typing import cast

import pytest
import yaml  # type: ignore[import-untyped]

from experiments.contracts import ExperimentScenario
from experiments.loader import (
    ExperimentFixtureError,
    LoadedExperimentDataset,
    load_experiment_dataset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
EXPECTED_DATASET_CHECKSUM = (
    "673b6866d58853a5c788ccff5b6acdc6511ee01b1085439d3d1353811dd3d51b"
)


def _copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "writer-v1"
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _read_yaml(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")))


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def test_frozen_dataset_has_expected_matrix_and_checksum(
    dataset: LoadedExperimentDataset,
) -> None:
    assert dataset.definition.experiment_id == "writer-validation-v1"
    assert dataset.definition.repetitions == 5
    assert len(dataset.knowledge) == 6
    assert len(dataset.tasks) == 24
    assert len(dataset.rubrics) == 24
    assert dataset.dataset_checksum == EXPECTED_DATASET_CHECKSUM

    matrix = Counter((task.domain_id, task.scenario) for task in dataset.tasks)
    for domain_id in dataset.definition.domain_ids:
        assert matrix[(domain_id, ExperimentScenario.CONSISTENCY)] == 2
        assert matrix[(domain_id, ExperimentScenario.ADAPTATION)] == 2


def test_knowledge_is_synthetic_byte_exact_and_read_only(
    dataset: LoadedExperimentDataset,
) -> None:
    for fixture in dataset.knowledge:
        key = (fixture.knowledge_id, fixture.version)
        content = dataset.knowledge_bytes[key]
        assert fixture.synthetic is True
        assert hashlib.sha256(content).hexdigest() == fixture.content_checksum
        assert b"fictional" in content
        assert dataset.knowledge_text(*key).encode("utf-8") == content

    with pytest.raises(TypeError):
        cast(dict[tuple[str, str], bytes], dataset.knowledge_bytes)[
            ("new-knowledge", "1.0.0")
        ] = b"mutated"
    with pytest.raises(KeyError, match="unknown knowledge"):
        dataset.knowledge_text("missing-knowledge", "1.0.0")


def test_task_and_rubric_references_are_bijective(
    dataset: LoadedExperimentDataset,
) -> None:
    tasks = {task.task_id: task for task in dataset.tasks}
    rubrics = {rubric.rubric_id: rubric for rubric in dataset.rubrics}

    assert {task.rubric_id for task in tasks.values()} == set(rubrics)
    assert {rubric.task_id for rubric in rubrics.values()} == set(tasks)
    for task in tasks.values():
        rubric = rubrics[task.rubric_id]
        if task.scenario is ExperimentScenario.ADAPTATION:
            assert len(rubric.personalization_constraints) == 2
        else:
            assert rubric.personalization_constraints == ()


def test_loading_same_bytes_from_another_root_is_checksum_stable(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    copied = load_experiment_dataset(_copy_dataset(tmp_path))

    assert copied.root != dataset.root
    assert copied.dataset_checksum == dataset.dataset_checksum


def test_loader_rejects_modified_knowledge_bytes(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    content_path = root / "knowledge" / "nexora-events.md"
    content_path.write_bytes(content_path.read_bytes() + b"\nmodified\n")

    with pytest.raises(ExperimentFixtureError, match="checksum mismatch"):
        load_experiment_dataset(root)


def test_loader_rejects_yaml_tags_instead_of_constructing_objects(
    tmp_path: Path,
) -> None:
    root = _copy_dataset(tmp_path)
    (root / "dataset.yaml").write_text(
        "!!python/object/apply:os.system ['echo unsafe']\n",
        encoding="utf-8",
    )

    with pytest.raises(ExperimentFixtureError, match="YAML parsing failed"):
        load_experiment_dataset(root)


def test_loader_rejects_relative_path_escape(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    descriptor = root / "knowledge" / "nexora-events.yaml"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8").replace(
            "knowledge/nexora-events.md",
            "../outside.md",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExperimentFixtureError, match="Pydantic validation failed"):
        load_experiment_dataset(root)


def test_loader_rejects_unknown_cross_file_reference(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    tasks = root / "tasks" / "nexora-events.yaml"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace(
            "knowledge_id: nexora-events-api",
            "knowledge_id: unknown-events-api",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExperimentFixtureError, match="does not resolve"):
        load_experiment_dataset(root)


def test_loader_rejects_oversized_knowledge_before_hashing(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    content_path = root / "knowledge" / "nexora-events.md"
    content_path.write_bytes(b"x" * (128 * 1024 + 1))

    with pytest.raises(ExperimentFixtureError, match="exceeds byte limit"):
        load_experiment_dataset(root)


def test_loader_requires_a_directory_root(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(ExperimentFixtureError, match="must be a directory"):
        load_experiment_dataset(file_path)

    with pytest.raises(ExperimentFixtureError, match="cannot be resolved"):
        load_experiment_dataset(tmp_path / "missing-root")


@pytest.mark.parametrize("content", [b"\xff", b"[]\n"])
def test_loader_rejects_invalid_yaml_encoding_or_root(
    tmp_path: Path,
    content: bytes,
) -> None:
    root = _copy_dataset(tmp_path)
    (root / "dataset.yaml").write_bytes(content)

    with pytest.raises(ExperimentFixtureError, match="YAML"):
        load_experiment_dataset(root)


def test_loader_rejects_missing_referenced_artifact(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    descriptor = root / "knowledge" / "nexora-events.yaml"
    payload = _read_yaml(descriptor)
    payload["content_path"] = "knowledge/missing-events.md"
    _write_yaml(descriptor, payload)

    with pytest.raises(ExperimentFixtureError, match="cannot be resolved"):
        load_experiment_dataset(root)


def test_loader_rejects_invalid_task_output_schema(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "tasks" / "nexora-events.yaml"
    payload = _read_yaml(path)
    schema = cast(dict[str, object], payload["output_schema"])
    schema["type"] = "unknown-json-type"
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="output_schema is invalid"):
        load_experiment_dataset(root)


def test_loader_rejects_declared_task_count_mismatch(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "dataset.yaml"
    payload = _read_yaml(path)
    payload["expected_task_count"] = 25
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="task count"):
        load_experiment_dataset(root)


def test_loader_rejects_unknown_fact_reference(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "rubrics" / "nexora-events.yaml"
    payload = _read_yaml(path)
    rubrics = cast(list[dict[str, object]], payload["rubrics"])
    facts = cast(list[str], rubrics[0]["required_fact_ids"])
    facts[0] = "unknown-fact"
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="unknown fact"):
        load_experiment_dataset(root)


def test_loader_rejects_fact_matcher_without_knowledge_evidence(
    tmp_path: Path,
) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "knowledge" / "nexora-events.yaml"
    payload = _read_yaml(path)
    facts = cast(list[dict[str, object]], payload["facts"])
    matchers = cast(list[dict[str, object]], facts[0]["accepted_matchers"])
    matchers[0]["pattern"] = "POST /v9/does-not-exist"
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="no evidence"):
        load_experiment_dataset(root)


def test_loader_wraps_regex_match_timeout(
    dataset: LoadedExperimentDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr("experiments.loader.regex.search", raise_timeout)

    with pytest.raises(ExperimentFixtureError, match="exceeded timeout"):
        load_experiment_dataset(dataset.root)


def test_loader_rejects_forbidden_matcher_without_distractor(
    tmp_path: Path,
) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "rubrics" / "nexora-events.yaml"
    payload = _read_yaml(path)
    rubrics = cast(list[dict[str, object]], payload["rubrics"])
    forbidden = cast(list[dict[str, object]], rubrics[0]["forbidden_matchers"])
    forbidden[0]["pattern"] = "a legacy value absent from knowledge"
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="knowledge distractor"):
        load_experiment_dataset(root)


def test_loader_enforces_scenario_specific_personalization(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "rubrics" / "nexora-events.yaml"
    payload = _read_yaml(path)
    rubrics = cast(list[dict[str, object]], payload["rubrics"])
    rubrics[2].pop("personalization_constraints")
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="requires personalization"):
        load_experiment_dataset(root)

    root = _copy_dataset(tmp_path / "second")
    path = root / "rubrics" / "nexora-events.yaml"
    payload = _read_yaml(path)
    rubrics = cast(list[dict[str, object]], payload["rubrics"])
    rubrics[0]["personalization_constraints"] = [
        {
            "constraint_id": "unexpected-style",
            "description": "Unexpected for consistency.",
            "expectation": "present",
            "matcher": {"kind": "exact", "pattern": "unexpected"},
        }
    ]
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="consistency task"):
        load_experiment_dataset(root)


def test_loader_rejects_unknown_personalization_target(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    path = root / "rubrics" / "nexora-events.yaml"
    payload = _read_yaml(path)
    rubrics = cast(list[dict[str, object]], payload["rubrics"])
    constraints = cast(
        list[dict[str, object]],
        rubrics[2]["personalization_constraints"],
    )
    constraints[0]["target_field"] = "unknown_field"
    _write_yaml(path, payload)

    with pytest.raises(ExperimentFixtureError, match="absent from output schema"):
        load_experiment_dataset(root)


def test_loader_rejects_non_two_by_two_domain_matrix(tmp_path: Path) -> None:
    root = _copy_dataset(tmp_path)
    tasks_path = root / "tasks" / "nexora-events.yaml"
    task_payload = _read_yaml(tasks_path)
    tasks = cast(list[dict[str, object]], task_payload["tasks"])
    tasks[2]["scenario"] = "consistency"
    _write_yaml(tasks_path, task_payload)

    rubric_path = root / "rubrics" / "nexora-events.yaml"
    rubric_payload = _read_yaml(rubric_path)
    rubrics = cast(list[dict[str, object]], rubric_payload["rubrics"])
    rubrics[2].pop("personalization_constraints")
    _write_yaml(rubric_path, rubric_payload)

    with pytest.raises(ExperimentFixtureError, match=r"2\+2 scenario matrix"):
        load_experiment_dataset(root)


def test_loader_rejects_non_utf8_knowledge_after_checksum_match(
    tmp_path: Path,
) -> None:
    root = _copy_dataset(tmp_path)
    content_path = root / "knowledge" / "nexora-events.md"
    content = b"\xff"
    content_path.write_bytes(content)

    descriptor = root / "knowledge" / "nexora-events.yaml"
    payload = _read_yaml(descriptor)
    payload["content_checksum"] = hashlib.sha256(content).hexdigest()
    _write_yaml(descriptor, payload)

    with pytest.raises(ExperimentFixtureError, match="valid UTF-8"):
        load_experiment_dataset(root)
