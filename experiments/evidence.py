"""Read-only validation of complete M5 execution evidence packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from experiments.artifacts import ArtifactStore, ArtifactStoreError
from experiments.contracts import (
    AttemptCompletion,
    AttemptIntent,
    AttemptStatus,
    ExecutionManifest,
    ExecutionPlan,
    ExecutionPlanItem,
    ExperimentRun,
    ExperimentRunRequest,
    ExperimentTask,
    RunAttempt,
)
from experiments.loader import ExperimentFixtureError, LoadedExperimentDataset
from experiments.planning import validate_execution_manifest, validate_execution_plan

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ExperimentEvidenceError(ValueError):
    """Execution evidence is missing, unexpected, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ExperimentEvidence:
    """One validated execution manifest and its plan-ordered terminal runs."""

    manifest: ExecutionManifest
    runs: tuple[ExperimentRun, ...]


class ExperimentEvidenceLoader:
    """Load a complete executor journal without writing or recovering artifacts."""

    def __init__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        store: ArtifactStore,
    ) -> None:
        self._dataset = dataset
        self._plan = plan
        self._store = store
        self._tasks = {task.task_id: task for task in dataset.tasks}

    def load(self) -> ExperimentEvidence:
        """Return all terminal runs after validating the complete immutable journal."""

        try:
            validate_execution_plan(self._plan, self._dataset)
            actual_paths = set(self._store.list_files(self._plan.experiment_id))
            manifest_path = self._manifest_path()
            manifest = self._read_required(manifest_path, ExecutionManifest)
            validate_execution_manifest(manifest, self._dataset, self._plan)
            expected_paths = {manifest_path}
            runs: list[ExperimentRun] = []
            for item in sorted(
                self._plan.items,
                key=lambda value: value.execution_order,
            ):
                request_path = self._request_path(item)
                terminal_path = self._terminal_path(item)
                request = self._read_required(request_path, ExperimentRunRequest)
                run = self._read_required(terminal_path, ExperimentRun)
                expected_paths.update((request_path, terminal_path))
                task = self._tasks[item.task_id]
                self._validate_request(request, item, task, manifest)
                expected_paths.update(
                    self._validate_attempt_journal(run, request, item, manifest)
                )
                self._validate_terminal(run, request, item, manifest)
                runs.append(run)
        except (ArtifactStoreError, ExperimentFixtureError) as exc:
            raise ExperimentEvidenceError(
                "execution evidence cannot be read or validated"
            ) from exc

        if actual_paths != expected_paths:
            missing = len(expected_paths - actual_paths)
            unexpected = len(actual_paths - expected_paths)
            raise ExperimentEvidenceError(
                "execution artifact set does not match the plan: "
                f"missing={missing}, unexpected={unexpected}"
            )
        return ExperimentEvidence(manifest=manifest, runs=tuple(runs))

    def _read_required(
        self,
        relative_path: str,
        model_type: type[_ModelT],
    ) -> _ModelT:
        if not self._store.exists(relative_path):
            raise ExperimentEvidenceError(
                f"required execution artifact is missing: {relative_path}"
            )
        return self._store.read_model(relative_path, model_type)

    def _validate_request(
        self,
        request: ExperimentRunRequest,
        item: ExecutionPlanItem,
        task: ExperimentTask,
        manifest: ExecutionManifest,
    ) -> None:
        identity = (
            request.run_id,
            request.experiment_id,
            request.manifest_checksum,
            request.plan_checksum,
            request.condition,
            request.task_id,
            request.repetition,
            request.execution_order,
        )
        expected = (
            item.run_id,
            manifest.experiment_id,
            manifest.manifest_checksum,
            self._plan.plan_checksum,
            item.condition,
            item.task_id,
            item.repetition,
            item.execution_order,
        )
        if (
            identity != expected
            or request.generation != manifest.generation
            or request.knowledge_checksum != task.knowledge.checksum
        ):
            raise ExperimentEvidenceError("run request provenance mismatch")

    def _validate_attempt_journal(
        self,
        run: ExperimentRun,
        request: ExperimentRunRequest,
        item: ExecutionPlanItem,
        manifest: ExecutionManifest,
    ) -> set[str]:
        expected_paths: set[str] = set()
        previous: RunAttempt | None = None
        completions: list[RunAttempt] = []
        for attempt in run.attempts:
            number = attempt.attempt_number
            intent_path = self._intent_path(item, number)
            completion_path = self._completion_path(item, number)
            expected_paths.update((intent_path, completion_path))
            intent = self._read_required(intent_path, AttemptIntent)
            completion = self._read_required(completion_path, AttemptCompletion)
            expected_backoff = 0.0 if number == 1 else 2.0 ** (number - 2)
            if (
                intent.run_id != item.run_id
                or intent.manifest_checksum != manifest.manifest_checksum
                or intent.attempt_number != number
                or intent.prompt_hash != request.prompt_hash
                or intent.reserved_prompt_tokens
                != manifest.limits.prompt_tokens_per_attempt_upper_bound
                or intent.reserved_completion_tokens
                != manifest.generation.max_output_tokens
                or intent.backoff_seconds != expected_backoff
                or intent.started_at < request.started_at
            ):
                raise ExperimentEvidenceError("attempt intent provenance mismatch")
            if (
                completion.run_id != item.run_id
                or completion.manifest_checksum != manifest.manifest_checksum
                or completion.attempt != attempt
                or attempt.started_at != intent.started_at
            ):
                raise ExperimentEvidenceError("attempt completion provenance mismatch")
            if previous is not None and (
                previous.status is AttemptStatus.SUCCEEDED
                or not previous.retryable
                or attempt.started_at < previous.completed_at
            ):
                raise ExperimentEvidenceError("attempt retry sequence is invalid")
            if (
                attempt.prompt_tokens is not None
                and attempt.prompt_tokens > intent.reserved_prompt_tokens
            ) or (
                attempt.completion_tokens is not None
                and attempt.completion_tokens > intent.reserved_completion_tokens
            ):
                raise ExperimentEvidenceError("attempt usage exceeds reservation")
            completions.append(completion.attempt)
            previous = attempt
        if run.attempts != tuple(completions):
            raise ExperimentEvidenceError("terminal attempts do not match journal")
        return expected_paths

    def _validate_terminal(
        self,
        run: ExperimentRun,
        request: ExperimentRunRequest,
        item: ExecutionPlanItem,
        manifest: ExecutionManifest,
    ) -> None:
        identity = (
            run.run_id,
            run.experiment_id,
            run.manifest_checksum,
            run.plan_checksum,
            run.condition,
            run.task_id,
            run.repetition,
            run.execution_order,
        )
        expected = (
            item.run_id,
            manifest.experiment_id,
            manifest.manifest_checksum,
            self._plan.plan_checksum,
            item.condition,
            item.task_id,
            item.repetition,
            item.execution_order,
        )
        if (
            identity != expected
            or run.generation != request.generation
            or run.invocation != request.invocation
            or run.prompt_hash != request.prompt_hash
            or run.knowledge_checksum != request.knowledge_checksum
            or run.agent_spec_checksum != request.agent_spec_checksum
            or run.started_at != request.started_at
        ):
            raise ExperimentEvidenceError("terminal run provenance mismatch")
        if run.attempts and run.completed_at < run.attempts[-1].completed_at:
            raise ExperimentEvidenceError("terminal run predates its final attempt")

    def _manifest_path(self) -> str:
        return f"{self._plan.experiment_id}/execution-manifest.json"

    def _request_path(self, item: ExecutionPlanItem) -> str:
        return f"{self._plan.experiment_id}/requests/{item.run_id}.json"

    def _intent_path(self, item: ExecutionPlanItem, attempt_number: int) -> str:
        return (
            f"{self._plan.experiment_id}/attempts/{item.run_id}/"
            f"{attempt_number:03d}-started.json"
        )

    def _completion_path(self, item: ExecutionPlanItem, attempt_number: int) -> str:
        return (
            f"{self._plan.experiment_id}/attempts/{item.run_id}/"
            f"{attempt_number:03d}-completed.json"
        )

    def _terminal_path(self, item: ExecutionPlanItem) -> str:
        return f"{self._plan.experiment_id}/terminal/{item.run_id}.json"
