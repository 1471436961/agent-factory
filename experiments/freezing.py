"""Build and verify an M5 freeze from reviewed inputs and local source facts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from agent_factory.domain.common import sha256_model
from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import (
    ExecutionPlan,
    FreezeCandidateSpec,
    FrozenArtifact,
    FrozenExperimentManifest,
    SourceSnapshot,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import (
    validate_execution_manifest,
    validate_execution_plan,
)
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
)

_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MAX_INVENTORY_BYTES = 32 * 1024 * 1024
_MAX_GIT_OUTPUT_BYTES = 1024 * 1024
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class FreezeError(RuntimeError):
    """A freeze candidate cannot be built or verified from local evidence."""


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    repository_root: Path
    source_commit: str
    working_tree_clean: bool


class GitSnapshotReader(Protocol):
    def snapshot(self, repository_root: Path) -> GitSnapshot:
        """Return the repository identity and current worktree state."""


class EnvironmentReader(Protocol):
    def python_implementation(self) -> str:
        """Return the active Python implementation name."""

    def python_version(self) -> str:
        """Return the active exact Python version."""

    def distribution_version(self, distribution_name: str) -> str:
        """Return one installed Python distribution version."""


class SubprocessGitSnapshotReader:
    """Read Git facts without shell interpolation or repository mutation."""

    def snapshot(self, repository_root: Path) -> GitSnapshot:
        expected_root = _resolve_repository_root(repository_root)
        actual_root = Path(
            self._run(expected_root, "rev-parse", "--show-toplevel")
        ).resolve(strict=True)
        if actual_root != expected_root:
            raise FreezeError("Git repository root does not match configured root")
        source_commit = self._run(expected_root, "rev-parse", "HEAD")
        status = self._run(
            expected_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        return GitSnapshot(
            repository_root=actual_root,
            source_commit=source_commit,
            working_tree_clean=not status,
        )

    @staticmethod
    def _run(repository_root: Path, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ("git", *arguments),
                cwd=repository_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FreezeError("Git source state cannot be read") from exc
        if (
            result.returncode != 0
            or len(result.stdout) > _MAX_GIT_OUTPUT_BYTES
            or len(result.stderr) > _MAX_GIT_OUTPUT_BYTES
        ):
            raise FreezeError("Git source state cannot be read")
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise FreezeError("Git output must be UTF-8") from exc


class SystemEnvironmentReader:
    """Read exact versions from the process that will execute the experiment."""

    def python_implementation(self) -> str:
        return platform.python_implementation()

    def python_version(self) -> str:
        return platform.python_version()

    def distribution_version(self, distribution_name: str) -> str:
        try:
            return importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise FreezeError("declared provider SDK is not installed") from exc


class FreezeCandidateBuilder:
    """Derive a canonical freeze manifest without making provider calls."""

    def __init__(
        self,
        repository_root: Path,
        *,
        git_reader: GitSnapshotReader | None = None,
        environment_reader: EnvironmentReader | None = None,
    ) -> None:
        self._repository_root = _resolve_repository_root(repository_root)
        self._git_reader = git_reader or SubprocessGitSnapshotReader()
        self._environment_reader = environment_reader or SystemEnvironmentReader()

    def build(
        self,
        *,
        candidate: FreezeCandidateSpec,
        candidate_spec_path: Path,
        plan_path: Path,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
    ) -> FrozenExperimentManifest:
        candidate_spec_relative = self._validate_reviewed_inputs(
            candidate,
            candidate_spec_path,
            plan_path,
            dataset,
            plan,
        )
        snapshot = self._git_reader.snapshot(self._repository_root)
        self._validate_git_snapshot(snapshot)
        files = self._read_inventory(candidate.inventory_paths)
        expected_spec_bytes = canonical_model_bytes(candidate)
        candidate_artifact = next(
            item for item in files if item.path == candidate_spec_relative
        )
        if (
            candidate_artifact.byte_size != len(expected_spec_bytes)
            or candidate_artifact.content_checksum
            != hashlib.sha256(expected_spec_bytes).hexdigest()
        ):
            raise FreezeError("candidate spec bytes do not match reviewed inputs")
        final_snapshot = self._git_reader.snapshot(self._repository_root)
        self._validate_git_snapshot(final_snapshot)
        if final_snapshot != snapshot:
            raise FreezeError("Git source state changed while freezing inputs")
        lockfile = next(item for item in files if item.path == "uv.lock")
        implementation = self._environment_reader.python_implementation()
        if implementation != "CPython":
            raise FreezeError("active Python runtime must be CPython")
        try:
            source = SourceSnapshot(
                source_commit=snapshot.source_commit,
                working_tree_clean=True,
                python_implementation="CPython",
                python_version=self._environment_reader.python_version(),
                lockfile_checksum=lockfile.content_checksum,
            )
        except ValidationError as exc:
            raise FreezeError("active Python runtime cannot be frozen") from exc
        installed_sdk_version = self._environment_reader.distribution_version(
            candidate.provider.sdk_name
        )
        if installed_sdk_version != candidate.provider.sdk_version:
            raise FreezeError("installed provider SDK version does not match candidate")
        try:
            unsigned = FrozenExperimentManifest(
                purpose=candidate.purpose,
                freeze_id=candidate.freeze_id,
                experiment_id=candidate.experiment_id,
                definition_checksum=candidate.definition_checksum,
                candidate_spec_path=candidate_spec_relative,
                execution_manifest=candidate.execution_manifest,
                analysis_config=candidate.analysis_config,
                analysis_config_checksum=sha256_model(candidate.analysis_config),
                source=source,
                provider=candidate.provider,
                pricing=candidate.pricing,
                cost_budget=candidate.cost_budget,
                pilot_evidence=candidate.pilot_evidence,
                files=files,
                created_at=candidate.created_at,
                manifest_checksum="0" * 64,
            )
        except ValidationError as exc:
            raise FreezeError("freeze candidate violates manifest contract") from exc
        return unsigned.model_copy(
            update={"manifest_checksum": calculate_freeze_manifest_checksum(unsigned)}
        )

    def _validate_reviewed_inputs(
        self,
        candidate: FreezeCandidateSpec,
        candidate_spec_path: Path,
        plan_path: Path,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
    ) -> str:
        try:
            validate_execution_plan(plan, dataset)
            validate_execution_manifest(candidate.execution_manifest, dataset, plan)
        except ValueError as exc:
            raise FreezeError("candidate execution identity is invalid") from exc
        if candidate.experiment_id != dataset.definition.experiment_id:
            raise FreezeError("candidate experiment identity does not match dataset")
        if candidate.definition_checksum != plan.definition_checksum:
            raise FreezeError("candidate definition checksum does not match plan")
        relative_spec = _relative_file_path(
            self._repository_root,
            candidate_spec_path,
        )
        if relative_spec not in candidate.inventory_paths:
            raise FreezeError("candidate spec must be included in frozen inventory")
        required_paths = _required_input_paths(
            self._repository_root,
            dataset,
            plan_path,
            candidate_spec_path,
        )
        missing_paths = required_paths.difference(candidate.inventory_paths)
        if missing_paths:
            raise FreezeError("freeze candidate inventory omits required inputs")
        prompt_path = dataset.root / "conditions" / "manual-system.txt"
        try:
            _, prompt_bytes = load_manual_system_prompt(prompt_path)
        except ValueError as exc:
            raise FreezeError("candidate condition bundle cannot be loaded") from exc
        if calculate_condition_bundle_checksum(prompt_bytes) != (
            candidate.execution_manifest.condition_bundle_checksum
        ):
            raise FreezeError("candidate condition bundle checksum is invalid")
        return relative_spec

    def _validate_git_snapshot(self, snapshot: GitSnapshot) -> None:
        try:
            snapshot_root = snapshot.repository_root.resolve(strict=True)
        except OSError as exc:
            raise FreezeError(
                "Git snapshot repository root cannot be resolved"
            ) from exc
        if snapshot_root != self._repository_root:
            raise FreezeError("Git snapshot repository root does not match")
        if not snapshot.working_tree_clean:
            raise FreezeError("Git working tree must be clean before freezing")
        try:
            SourceSnapshot(
                source_commit=snapshot.source_commit,
                working_tree_clean=True,
                python_implementation="CPython",
                python_version=self._environment_reader.python_version(),
                lockfile_checksum="0" * 64,
            )
        except ValidationError as exc:
            raise FreezeError("Git source commit or Python runtime is invalid") from exc

    def _read_inventory(
        self,
        inventory_paths: tuple[str, ...],
    ) -> tuple[FrozenArtifact, ...]:
        files: list[FrozenArtifact] = []
        total_bytes = 0
        for relative_path in inventory_paths:
            path = _resolve_inventory_file(self._repository_root, relative_path)
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise FreezeError("frozen inventory file cannot be read") from exc
            if not content or len(content) > _MAX_INPUT_BYTES:
                raise FreezeError("frozen inventory file size is invalid")
            total_bytes += len(content)
            if total_bytes > _MAX_INVENTORY_BYTES:
                raise FreezeError("frozen inventory exceeds total byte limit")
            files.append(
                FrozenArtifact(
                    path=relative_path,
                    byte_size=len(content),
                    content_checksum=hashlib.sha256(content).hexdigest(),
                )
            )
        return tuple(files)


def calculate_freeze_manifest_checksum(manifest: FrozenExperimentManifest) -> str:
    """Hash every freeze field except the self-referential checksum."""

    return sha256_model(manifest, exclude={"manifest_checksum"})


def verify_freeze_manifest(
    manifest: FrozenExperimentManifest,
    *,
    repository_root: Path,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    plan_path: Path,
    verify_environment: bool = True,
    git_reader: GitSnapshotReader | None = None,
    environment_reader: EnvironmentReader | None = None,
) -> None:
    """Verify frozen content and, by default, the current execution environment."""

    root = _resolve_repository_root(repository_root)
    if calculate_freeze_manifest_checksum(manifest) != manifest.manifest_checksum:
        raise FreezeError("freeze manifest checksum mismatch")
    try:
        validate_execution_plan(plan, dataset)
        validate_execution_manifest(manifest.execution_manifest, dataset, plan)
    except ValueError as exc:
        raise FreezeError("freeze execution identity is invalid") from exc
    if (
        manifest.experiment_id != dataset.definition.experiment_id
        or manifest.definition_checksum != plan.definition_checksum
    ):
        raise FreezeError("freeze manifest does not match dataset and plan")
    builder = FreezeCandidateBuilder(
        root,
        git_reader=git_reader,
        environment_reader=environment_reader,
    )
    snapshot: GitSnapshot | None = None
    if verify_environment:
        snapshot = builder._git_reader.snapshot(root)
        builder._validate_git_snapshot(snapshot)
        if snapshot.source_commit != manifest.source.source_commit:
            raise FreezeError("current Git commit does not match frozen source")
    required_paths = _required_input_paths(
        root,
        dataset,
        plan_path,
        root / manifest.candidate_spec_path,
    )
    if not required_paths.issubset(item.path for item in manifest.files):
        raise FreezeError("freeze manifest omits required inputs")
    actual_files = builder._read_inventory(tuple(item.path for item in manifest.files))
    if actual_files != manifest.files:
        raise FreezeError("frozen file inventory checksum or size mismatch")
    candidate = load_freeze_candidate_spec(root / manifest.candidate_spec_path)
    _validate_candidate_matches_manifest(candidate, manifest)
    if not verify_environment:
        return
    final_snapshot = builder._git_reader.snapshot(root)
    builder._validate_git_snapshot(final_snapshot)
    if final_snapshot != snapshot:
        raise FreezeError("Git source state changed while verifying inputs")
    environment = builder._environment_reader
    if (
        environment.python_implementation() != manifest.source.python_implementation
        or environment.python_version() != manifest.source.python_version
    ):
        raise FreezeError("current Python runtime does not match frozen source")
    if environment.distribution_version(manifest.provider.sdk_name) != (
        manifest.provider.sdk_version
    ):
        raise FreezeError("current provider SDK does not match frozen source")


def load_freeze_candidate_spec(path: Path) -> FreezeCandidateSpec:
    """Load one bounded canonical JSON candidate specification."""

    return _load_canonical_model(path, FreezeCandidateSpec, "freeze candidate spec")


def load_frozen_experiment_manifest(path: Path) -> FrozenExperimentManifest:
    """Load one bounded canonical JSON freeze manifest."""

    manifest = _load_canonical_model(
        path,
        FrozenExperimentManifest,
        "freeze manifest",
    )
    if calculate_freeze_manifest_checksum(manifest) != manifest.manifest_checksum:
        raise FreezeError("freeze manifest checksum mismatch")
    return manifest


def publish_freeze_candidate(
    manifest: FrozenExperimentManifest,
    *,
    repository_root: Path,
    output_path: Path,
) -> bool:
    """Write a candidate once without dirtying the source repository."""

    root = _resolve_repository_root(repository_root)
    output = output_path.resolve()
    try:
        relative = output.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None and (not relative.parts or relative.parts[0] != ".tmp"):
        raise FreezeError("in-repository freeze output must be below .tmp")
    return ArtifactStore(output.parent).write_model_once(output.name, manifest)


def _load_canonical_model(
    path: Path,
    model_type: type[_ModelT],
    label: str,
) -> _ModelT:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise FreezeError(f"{label} cannot be read") from exc
    if not content or len(content) > _MAX_INPUT_BYTES:
        raise FreezeError(f"{label} size is invalid")
    try:
        model = model_type.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise FreezeError(f"{label} is invalid") from exc
    if content != canonical_model_bytes(model):
        raise FreezeError(f"{label} is not canonical JSON")
    return model


def _resolve_repository_root(repository_root: Path) -> Path:
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise FreezeError("repository root cannot be resolved") from exc
    if not root.is_dir():
        raise FreezeError("repository root must be a directory")
    return root


def _relative_file_path(repository_root: Path, path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(repository_root).as_posix()
    except (OSError, ValueError) as exc:
        raise FreezeError("frozen input must be a repository file") from exc
    _resolve_inventory_file(repository_root, relative)
    return relative


def _resolve_inventory_file(repository_root: Path, relative_path: str) -> Path:
    candidate = PurePosixPath(relative_path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise FreezeError("frozen inventory path must be clean and relative")
    unresolved = repository_root.joinpath(*candidate.parts)
    current = repository_root
    try:
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                raise FreezeError("frozen inventory cannot contain symbolic links")
        resolved = unresolved.resolve(strict=True)
    except FreezeError:
        raise
    except OSError as exc:
        raise FreezeError("frozen inventory file cannot be resolved") from exc
    if not resolved.is_relative_to(repository_root) or not resolved.is_file():
        raise FreezeError("frozen inventory entry must be a repository file")
    return resolved


def _required_input_paths(
    repository_root: Path,
    dataset: LoadedExperimentDataset,
    plan_path: Path,
    candidate_spec_path: Path | None = None,
) -> set[str]:
    definition = dataset.definition
    dataset_paths = {
        "dataset.yaml",
        "conditions/manual-system.txt",
        *definition.knowledge_files,
        *definition.task_files,
        *definition.rubric_files,
        *(item.content_path for item in dataset.knowledge),
    }
    required: set[str] = {
        "uv.lock",
        _relative_file_path(repository_root, plan_path),
    }
    if candidate_spec_path is not None:
        required.add(_relative_file_path(repository_root, candidate_spec_path))
    for relative_path in dataset_paths:
        required.add(_relative_file_path(repository_root, dataset.root / relative_path))
    return required


def _validate_candidate_matches_manifest(
    candidate: FreezeCandidateSpec,
    manifest: FrozenExperimentManifest,
) -> None:
    if (
        candidate.purpose != manifest.purpose
        or candidate.freeze_id != manifest.freeze_id
        or candidate.experiment_id != manifest.experiment_id
        or candidate.definition_checksum != manifest.definition_checksum
        or candidate.execution_manifest != manifest.execution_manifest
        or candidate.analysis_config != manifest.analysis_config
        or candidate.provider != manifest.provider
        or candidate.pricing != manifest.pricing
        or candidate.cost_budget != manifest.cost_budget
        or candidate.pilot_evidence != manifest.pilot_evidence
        or candidate.inventory_paths != tuple(item.path for item in manifest.files)
        or candidate.created_at != manifest.created_at
    ):
        raise FreezeError("candidate spec does not match freeze manifest")
