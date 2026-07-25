"""Bounded loader and cross-file validation for frozen M5 fixtures."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TypeVar

import regex
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

from agent_factory.domain.common import Sha256, canonical_json_bytes
from agent_factory.domain.errors import FactoryError
from agent_factory.domain.validation import validate_output_schema
from experiments.contracts import (
    ExperimentDefinition,
    ExperimentScenario,
    ExperimentTask,
    KnowledgeFixture,
    MatcherKind,
    RubricBundle,
    RubricDefinition,
    TaskBundle,
    TextMatcher,
    model_payload,
)

_MAX_YAML_BYTES = 256 * 1024
_MAX_KNOWLEDGE_BYTES = 128 * 1024
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_KnowledgeKey = tuple[str, str]


class ExperimentFixtureError(ValueError):
    """A stable local error for malformed or inconsistent experiment fixtures."""

    def __init__(self, reason: str, *, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        location = f" ({path})" if path is not None else ""
        super().__init__(f"experiment fixture invalid: {reason}{location}")


@dataclass(frozen=True, slots=True)
class LoadedExperimentDataset:
    """Validated definitions plus byte-exact, read-only knowledge content."""

    root: Path
    definition: ExperimentDefinition
    knowledge: tuple[KnowledgeFixture, ...]
    tasks: tuple[ExperimentTask, ...]
    rubrics: tuple[RubricDefinition, ...]
    knowledge_bytes: Mapping[_KnowledgeKey, bytes]
    dataset_checksum: Sha256

    def knowledge_text(self, knowledge_id: str, version: str) -> str:
        """Decode one already-validated UTF-8 knowledge artifact."""

        try:
            content = self.knowledge_bytes[(knowledge_id, version)]
        except KeyError as exc:
            raise KeyError(
                f"unknown knowledge fixture {knowledge_id}@{version}"
            ) from exc
        return content.decode("utf-8")


def load_experiment_dataset(root: Path) -> LoadedExperimentDataset:
    """Load one fixture root and reject cross-file or checksum inconsistencies."""

    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ExperimentFixtureError(
            "fixture root cannot be resolved", path=root
        ) from exc
    if not resolved_root.is_dir():
        raise ExperimentFixtureError("fixture root must be a directory", path=root)
    definition = _load_yaml(
        _resolve_artifact(resolved_root, "dataset.yaml"),
        ExperimentDefinition,
    )

    knowledge: list[KnowledgeFixture] = []
    knowledge_bytes: dict[_KnowledgeKey, bytes] = {}
    for relative_path in definition.knowledge_files:
        fixture = _load_yaml(
            _resolve_artifact(resolved_root, relative_path),
            KnowledgeFixture,
        )
        content_path = _resolve_artifact(resolved_root, fixture.content_path)
        content = _read_limited(content_path, _MAX_KNOWLEDGE_BYTES)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExperimentFixtureError(
                "knowledge content must be valid UTF-8",
                path=content_path,
            ) from exc
        checksum = hashlib.sha256(content).hexdigest()
        if checksum != fixture.content_checksum:
            raise ExperimentFixtureError(
                "knowledge content checksum mismatch",
                path=content_path,
            )
        key = (fixture.knowledge_id, fixture.version)
        if key in knowledge_bytes:
            raise ExperimentFixtureError(
                "duplicate knowledge identity", path=content_path
            )
        knowledge.append(fixture)
        knowledge_bytes[key] = content

    tasks: list[ExperimentTask] = []
    task_bundle_domains: list[str] = []
    for relative_path in definition.task_files:
        path = _resolve_artifact(resolved_root, relative_path)
        task_bundle = _load_yaml(path, TaskBundle)
        task_bundle_domains.append(task_bundle.domain_id)
        try:
            validate_output_schema(task_bundle.output_schema)
        except FactoryError as exc:
            raise ExperimentFixtureError(
                "task output_schema is invalid", path=path
            ) from exc
        tasks.extend(
            ExperimentTask(
                **task.model_dump(mode="python"),
                output_schema=task_bundle.output_schema,
            )
            for task in task_bundle.tasks
        )

    rubrics: list[RubricDefinition] = []
    rubric_bundle_domains: list[str] = []
    for relative_path in definition.rubric_files:
        path = _resolve_artifact(resolved_root, relative_path)
        rubric_bundle = _load_yaml(path, RubricBundle)
        rubric_bundle_domains.append(rubric_bundle.domain_id)
        rubrics.extend(rubric_bundle.rubrics)

    ordered_knowledge = tuple(sorted(knowledge, key=lambda item: item.domain_id))
    ordered_tasks = tuple(sorted(tasks, key=lambda item: item.task_id))
    ordered_rubrics = tuple(sorted(rubrics, key=lambda item: item.rubric_id))
    _validate_dataset(
        definition=definition,
        knowledge=ordered_knowledge,
        knowledge_bytes=knowledge_bytes,
        tasks=ordered_tasks,
        task_bundle_domains=task_bundle_domains,
        rubrics=ordered_rubrics,
        rubric_bundle_domains=rubric_bundle_domains,
    )
    dataset_checksum = hashlib.sha256(
        canonical_json_bytes(
            {
                "definition": model_payload(definition),
                "knowledge": [model_payload(item) for item in ordered_knowledge],
                "tasks": [model_payload(item) for item in ordered_tasks],
                "rubrics": [model_payload(item) for item in ordered_rubrics],
            }
        )
    ).hexdigest()
    return LoadedExperimentDataset(
        root=resolved_root,
        definition=definition,
        knowledge=ordered_knowledge,
        tasks=ordered_tasks,
        rubrics=ordered_rubrics,
        knowledge_bytes=MappingProxyType(dict(knowledge_bytes)),
        dataset_checksum=dataset_checksum,
    )


def _validate_dataset(
    *,
    definition: ExperimentDefinition,
    knowledge: tuple[KnowledgeFixture, ...],
    knowledge_bytes: Mapping[_KnowledgeKey, bytes],
    tasks: tuple[ExperimentTask, ...],
    task_bundle_domains: list[str],
    rubrics: tuple[RubricDefinition, ...],
    rubric_bundle_domains: list[str],
) -> None:
    domains = set(definition.domain_ids)
    if {item.domain_id for item in knowledge} != domains:
        raise ExperimentFixtureError("knowledge domains do not match dataset")
    if set(task_bundle_domains) != domains or len(task_bundle_domains) != len(domains):
        raise ExperimentFixtureError("task bundle domains do not match dataset")
    if set(rubric_bundle_domains) != domains or len(rubric_bundle_domains) != len(
        domains
    ):
        raise ExperimentFixtureError("rubric bundle domains do not match dataset")
    if len(tasks) != definition.expected_task_count:
        raise ExperimentFixtureError("task count does not match dataset declaration")

    task_ids = [task.task_id for task in tasks]
    rubric_ids = [rubric.rubric_id for rubric in rubrics]
    if len(task_ids) != len(set(task_ids)):
        raise ExperimentFixtureError("dataset contains duplicate task IDs")
    if len(rubric_ids) != len(set(rubric_ids)):
        raise ExperimentFixtureError("dataset contains duplicate rubric IDs")
    if len(rubrics) != len(tasks):
        raise ExperimentFixtureError("each task requires exactly one rubric")

    knowledge_by_key = {(item.knowledge_id, item.version): item for item in knowledge}
    rubric_by_id = {item.rubric_id: item for item in rubrics}
    rubric_task_ids = {item.task_id for item in rubrics}
    if rubric_task_ids != set(task_ids):
        raise ExperimentFixtureError("rubric task references do not match dataset")

    per_domain: dict[str, dict[ExperimentScenario, int]] = {
        domain: {
            ExperimentScenario.CONSISTENCY: 0,
            ExperimentScenario.ADAPTATION: 0,
        }
        for domain in domains
    }
    for task in tasks:
        if task.domain_id not in domains:
            raise ExperimentFixtureError("task references an unknown domain")
        per_domain[task.domain_id][task.scenario] += 1
        key = (task.knowledge.knowledge_id, task.knowledge.version)
        fixture = knowledge_by_key.get(key)
        if fixture is None or fixture.domain_id != task.domain_id:
            raise ExperimentFixtureError("task knowledge reference does not resolve")
        if fixture.content_checksum != task.knowledge.checksum:
            raise ExperimentFixtureError(
                "task knowledge checksum does not match fixture"
            )
        rubric = rubric_by_id.get(task.rubric_id)
        if rubric is None or rubric.task_id != task.task_id:
            raise ExperimentFixtureError("task rubric reference does not resolve")
        facts = {fact.fact_id: fact for fact in fixture.facts}
        if not set(rubric.required_fact_ids) <= set(facts):
            raise ExperimentFixtureError("rubric references an unknown fact")
        content = knowledge_bytes[key].decode("utf-8")
        for fact_id in rubric.required_fact_ids:
            if not any(
                _matcher_matches(matcher, content)
                for matcher in facts[fact_id].accepted_matchers
            ):
                raise ExperimentFixtureError(
                    "required fact matcher has no evidence in knowledge"
                )
        for matcher in rubric.forbidden_matchers:
            if not _matcher_matches(matcher, content):
                raise ExperimentFixtureError(
                    "forbidden matcher must represent a knowledge distractor"
                )
        if task.scenario is ExperimentScenario.ADAPTATION:
            if not rubric.personalization_constraints:
                raise ExperimentFixtureError(
                    "adaptation task requires personalization constraints"
                )
        elif rubric.personalization_constraints:
            raise ExperimentFixtureError(
                "consistency task cannot contain personalization constraints"
            )
        _validate_constraint_targets(task, rubric)

    expected_matrix = {
        ExperimentScenario.CONSISTENCY: 2,
        ExperimentScenario.ADAPTATION: 2,
    }
    if any(counts != expected_matrix for counts in per_domain.values()):
        raise ExperimentFixtureError("each domain requires a 2+2 scenario matrix")


def _validate_constraint_targets(
    task: ExperimentTask,
    rubric: RubricDefinition,
) -> None:
    properties = task.output_schema.get("properties")
    known_fields = set(properties) if isinstance(properties, Mapping) else set()
    for constraint in rubric.personalization_constraints:
        if (
            constraint.target_field is not None
            and constraint.target_field not in known_fields
        ):
            raise ExperimentFixtureError(
                "personalization target_field is absent from output schema"
            )


def _matcher_matches(matcher: TextMatcher, text: str) -> bool:
    if matcher.kind is MatcherKind.EXACT:
        if matcher.case_sensitive:
            return matcher.pattern in text
        return matcher.pattern.casefold() in text.casefold()
    flags = 0 if matcher.case_sensitive else regex.IGNORECASE
    try:
        return regex.search(matcher.pattern, text, flags=flags, timeout=0.1) is not None
    except TimeoutError as exc:
        raise ExperimentFixtureError("matcher evaluation exceeded timeout") from exc


def _load_yaml(path: Path, model_type: type[_ModelT]) -> _ModelT:
    payload = _read_limited(path, _MAX_YAML_BYTES)
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExperimentFixtureError("YAML must be valid UTF-8", path=path) from exc
    try:
        raw = yaml.safe_load(decoded)
    except yaml.YAMLError as exc:
        raise ExperimentFixtureError("YAML parsing failed", path=path) from exc
    if not isinstance(raw, Mapping):
        raise ExperimentFixtureError("YAML root must be an object", path=path)
    try:
        return model_type.model_validate(raw)
    except ValidationError as exc:
        raise ExperimentFixtureError("Pydantic validation failed", path=path) from exc


def _resolve_artifact(root: Path, relative_path: str) -> Path:
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ExperimentFixtureError("artifact path must be a clean relative path")
    try:
        resolved = root.joinpath(*candidate.parts).resolve(strict=True)
    except OSError as exc:
        raise ExperimentFixtureError(
            "artifact path cannot be resolved",
            path=root.joinpath(*candidate.parts),
        ) from exc
    if not resolved.is_relative_to(root):
        raise ExperimentFixtureError(
            "artifact path escapes fixture root", path=resolved
        )
    if not resolved.is_file():
        raise ExperimentFixtureError(
            "artifact path must reference a file", path=resolved
        )
    return resolved


def _read_limited(path: Path, maximum_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExperimentFixtureError(
            "artifact metadata cannot be read", path=path
        ) from exc
    if size > maximum_bytes:
        raise ExperimentFixtureError("artifact exceeds byte limit", path=path)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ExperimentFixtureError("artifact cannot be read", path=path) from exc
