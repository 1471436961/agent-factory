"""M5.5.2 freeze construction and verification tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_factory.domain.common import sha256_model
from experiments.artifacts import canonical_model_bytes
from experiments.contracts import (
    AnalysisConfig,
    CostBudget,
    ExecutionLimits,
    ExperimentPurpose,
    FreezeCandidateSpec,
    FrozenExperimentManifest,
    GenerationConfig,
    PriceSnapshot,
    ProviderSnapshot,
    calculate_conservative_cost_micros,
)
from experiments.freezing import (
    FreezeCandidateBuilder,
    FreezeError,
    GitSnapshot,
    GitSnapshotReader,
    SubprocessGitSnapshotReader,
    SystemEnvironmentReader,
    calculate_freeze_manifest_checksum,
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
    publish_freeze_candidate,
    verify_freeze_manifest,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import (
    build_execution_manifest,
    calculate_manifest_checksum,
    load_execution_plan,
)
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLAN_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "definitions"
    / "writer-v1"
    / "execution-plan.json"
)
NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _FakeGitReader:
    clean: bool = True
    source_commit: str = "a" * 40
    repository_root: Path = REPOSITORY_ROOT

    def snapshot(self, repository_root: Path) -> GitSnapshot:
        assert repository_root == REPOSITORY_ROOT
        return GitSnapshot(
            repository_root=self.repository_root,
            source_commit=self.source_commit,
            working_tree_clean=self.clean,
        )


@dataclass(frozen=True, slots=True)
class _FakeEnvironmentReader:
    implementation: str = "CPython"
    version: str = "3.11.15"
    sdk_version: str = "2.46.0"

    def python_implementation(self) -> str:
        return self.implementation

    def python_version(self) -> str:
        return self.version

    def distribution_version(self, distribution_name: str) -> str:
        assert distribution_name == "openai"
        return self.sdk_version


class _ChangingGitReader:
    def __init__(self) -> None:
        self._calls = 0

    def snapshot(self, repository_root: Path) -> GitSnapshot:
        self._calls += 1
        return GitSnapshot(
            repository_root=repository_root,
            source_commit=("a" if self._calls == 1 else "b") * 40,
            working_tree_clean=True,
        )


def _candidate(
    dataset: LoadedExperimentDataset,
    candidate_spec_path: Path,
    *,
    omitted_path: str | None = None,
) -> FreezeCandidateSpec:
    plan = load_execution_plan(PLAN_PATH, dataset)
    _, prompt_bytes = load_manual_system_prompt(
        dataset.root / "conditions" / "manual-system.txt"
    )
    execution = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(prompt_bytes),
        generation=GenerationConfig(
            provider="openai",
            model="gpt-test-snapshot",
            sdk_version="2.46.0",
            temperature=0,
            max_output_tokens=500,
            request_timeout_seconds=30,
        ),
        limits=ExecutionLimits(
            max_provider_requests=720,
            max_prompt_tokens=3_000_000,
            max_completion_tokens=1_000_000,
            prompt_tokens_per_attempt_upper_bound=4_000,
        ),
    )
    pricing = PriceSnapshot(
        provider="openai",
        model="gpt-test-snapshot",
        currency="USD",
        input_micros_per_unit=1_000_000,
        output_micros_per_unit=2_000_000,
        source_url="https://example.com/provider-pricing",
        captured_at=NOW,
    )
    inventory = {
        "uv.lock",
        candidate_spec_path.relative_to(REPOSITORY_ROOT).as_posix(),
        *(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in dataset.root.rglob("*")
            if path.is_file()
        ),
    }
    if omitted_path is not None:
        inventory.remove(omitted_path)
    estimated_cost = calculate_conservative_cost_micros(
        input_tokens=240_000,
        output_tokens=120_000,
        pricing=pricing,
    )
    return FreezeCandidateSpec(
        purpose=ExperimentPurpose.PILOT,
        freeze_id="writer-pilot-freeze-v1",
        experiment_id=dataset.definition.experiment_id,
        definition_checksum=plan.definition_checksum,
        execution_manifest=execution,
        analysis_config=AnalysisConfig(
            bootstrap_seed=dataset.definition.randomization_seed,
            bootstrap_iterations=10_000,
        ),
        provider=ProviderSnapshot(
            provider="openai",
            model="gpt-test-snapshot",
            api_name="responses-api",
            sdk_name="openai",
            sdk_version="2.46.0",
            model_is_immutable_snapshot=True,
        ),
        pricing=pricing,
        cost_budget=CostBudget(
            currency="USD",
            estimated_provider_requests=240,
            estimated_prompt_tokens=240_000,
            estimated_completion_tokens=120_000,
            estimated_cost_micros=estimated_cost,
            hard_cost_limit_micros=1_000_000,
        ),
        inventory_paths=tuple(sorted(inventory)),
        created_at=NOW,
    )


def _write_candidate(
    dataset: LoadedExperimentDataset,
    path: Path,
    *,
    omitted_path: str | None = None,
) -> FreezeCandidateSpec:
    candidate = _candidate(dataset, path, omitted_path=omitted_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_model_bytes(candidate))
    return candidate


def _builder(
    *,
    git_reader: GitSnapshotReader | None = None,
    environment_reader: _FakeEnvironmentReader | None = None,
) -> FreezeCandidateBuilder:
    return FreezeCandidateBuilder(
        REPOSITORY_ROOT,
        git_reader=git_reader or _FakeGitReader(),
        environment_reader=environment_reader or _FakeEnvironmentReader(),
    )


def _build(
    builder: FreezeCandidateBuilder,
    candidate: FreezeCandidateSpec,
    candidate_path: Path,
    dataset: LoadedExperimentDataset,
) -> FrozenExperimentManifest:
    plan = load_execution_plan(PLAN_PATH, dataset)
    return builder.build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )


def test_build_publish_load_and_verify_are_deterministic(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    plan = load_execution_plan(PLAN_PATH, dataset)

    first = _builder().build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )
    second = _builder().build(
        candidate=load_freeze_candidate_spec(candidate_path),
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )

    assert first == second
    assert first.manifest_checksum == calculate_freeze_manifest_checksum(first)
    assert tuple(item.path for item in first.files) == candidate.inventory_paths
    manifest_path = tmp_path / "freeze-manifest.json"
    assert publish_freeze_candidate(
        first,
        repository_root=REPOSITORY_ROOT,
        output_path=manifest_path,
    )
    assert not publish_freeze_candidate(
        first,
        repository_root=REPOSITORY_ROOT,
        output_path=manifest_path,
    )
    loaded = load_frozen_experiment_manifest(manifest_path)
    verify_freeze_manifest(
        loaded,
        repository_root=REPOSITORY_ROOT,
        dataset=dataset,
        plan=plan,
        plan_path=PLAN_PATH,
        git_reader=_FakeGitReader(),
        environment_reader=_FakeEnvironmentReader(),
    )


def test_dirty_tree_and_sdk_mismatch_are_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)

    with pytest.raises(FreezeError, match="working tree must be clean"):
        _build(
            _builder(git_reader=_FakeGitReader(clean=False)),
            candidate,
            candidate_path,
            dataset,
        )
    with pytest.raises(FreezeError, match="SDK version does not match"):
        _build(
            _builder(environment_reader=_FakeEnvironmentReader(sdk_version="2.45.0")),
            candidate,
            candidate_path,
            dataset,
        )

    with pytest.raises(FreezeError, match="changed while freezing"):
        _build(
            _builder(git_reader=_ChangingGitReader()),
            candidate,
            candidate_path,
            dataset,
        )


def test_required_input_omission_and_noncanonical_spec_are_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    omitted = (
        (dataset.root / "conditions" / "manual-system.txt")
        .relative_to(REPOSITORY_ROOT)
        .as_posix()
    )
    candidate = _write_candidate(dataset, candidate_path, omitted_path=omitted)
    plan = load_execution_plan(PLAN_PATH, dataset)

    with pytest.raises(FreezeError, match="omits required inputs"):
        _builder().build(
            candidate=candidate,
            candidate_spec_path=candidate_path,
            plan_path=PLAN_PATH,
            dataset=dataset,
            plan=plan,
        )

    candidate_path.write_text(
        json.dumps(candidate.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    with pytest.raises(FreezeError, match="not canonical JSON"):
        load_freeze_candidate_spec(candidate_path)


def test_changed_file_source_or_manifest_checksum_is_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    plan = load_execution_plan(PLAN_PATH, dataset)
    manifest = _builder().build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )
    candidate_path.write_bytes(candidate_path.read_bytes() + b" ")

    with pytest.raises(FreezeError, match="checksum or size mismatch"):
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            verify_environment=False,
        )

    candidate_path.write_bytes(canonical_model_bytes(candidate))
    changed_candidate = candidate.model_copy(update={"freeze_id": "other-freeze"})
    changed_bytes = canonical_model_bytes(changed_candidate)
    candidate_path.write_bytes(changed_bytes)
    changed_files = tuple(
        item.model_copy(
            update={
                "byte_size": len(changed_bytes),
                "content_checksum": hashlib.sha256(changed_bytes).hexdigest(),
            }
        )
        if item.path == manifest.candidate_spec_path
        else item
        for item in manifest.files
    )
    mismatched_manifest = manifest.model_copy(update={"files": changed_files})
    mismatched_manifest = mismatched_manifest.model_copy(
        update={
            "manifest_checksum": calculate_freeze_manifest_checksum(mismatched_manifest)
        }
    )
    with pytest.raises(FreezeError, match="spec does not match freeze manifest"):
        verify_freeze_manifest(
            mismatched_manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            verify_environment=False,
        )

    candidate_path.write_bytes(canonical_model_bytes(candidate))
    with pytest.raises(FreezeError, match="Git commit does not match"):
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            git_reader=_FakeGitReader(source_commit="b" * 40),
            environment_reader=_FakeEnvironmentReader(),
        )

    tampered = manifest.model_copy(update={"manifest_checksum": "f" * 64})
    with pytest.raises(FreezeError, match="manifest checksum mismatch"):
        verify_freeze_manifest(
            tampered,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            verify_environment=False,
        )


def test_publish_rejects_tracked_repository_destination(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    plan = load_execution_plan(PLAN_PATH, dataset)
    manifest = _builder().build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )

    with pytest.raises(FreezeError, match=r"must be below \.tmp"):
        publish_freeze_candidate(
            manifest,
            repository_root=REPOSITORY_ROOT,
            output_path=REPOSITORY_ROOT / "docs" / "freeze-manifest.json",
        )


def test_unclean_inventory_path_and_oversized_file_are_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    traversal = candidate.model_copy(
        update={
            "inventory_paths": tuple(
                sorted((*candidate.inventory_paths, "experiments/../uv.lock"))
            )
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(traversal))
    with pytest.raises(FreezeError, match="clean and relative"):
        _build(_builder(), traversal, candidate_path, dataset)

    oversized_path = tmp_path / "oversized.bin"
    oversized_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    oversized = candidate.model_copy(
        update={
            "inventory_paths": tuple(
                sorted(
                    (
                        *candidate.inventory_paths,
                        oversized_path.relative_to(REPOSITORY_ROOT).as_posix(),
                    )
                )
            )
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(oversized))
    with pytest.raises(FreezeError, match="file size is invalid"):
        _build(_builder(), oversized, candidate_path, dataset)


def test_freeze_checksum_excludes_only_its_own_field(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    plan = load_execution_plan(PLAN_PATH, dataset)
    manifest = _builder().build(
        candidate=candidate,
        candidate_spec_path=candidate_path,
        plan_path=PLAN_PATH,
        dataset=dataset,
        plan=plan,
    )

    assert manifest.analysis_config_checksum == sha256_model(manifest.analysis_config)
    changed = manifest.model_copy(update={"freeze_id": "another-freeze"})
    assert calculate_freeze_manifest_checksum(changed) != manifest.manifest_checksum


def test_system_readers_capture_real_local_facts_without_mutation() -> None:
    git_snapshot = SubprocessGitSnapshotReader().snapshot(REPOSITORY_ROOT)
    environment = SystemEnvironmentReader()

    assert git_snapshot.repository_root == REPOSITORY_ROOT
    assert len(git_snapshot.source_commit) in range(40, 65)
    assert isinstance(git_snapshot.working_tree_clean, bool)
    assert environment.python_implementation() == "CPython"
    assert environment.python_version().count(".") == 2
    assert environment.distribution_version("openai")
    with pytest.raises(FreezeError, match="SDK is not installed"):
        environment.distribution_version("agent-factory-definitely-missing-sdk")


def test_invalid_source_runtime_and_condition_identity_are_rejected(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)

    with pytest.raises(FreezeError, match="must be CPython"):
        _build(
            _builder(environment_reader=_FakeEnvironmentReader(implementation="PyPy")),
            candidate,
            candidate_path,
            dataset,
        )
    with pytest.raises(FreezeError, match="commit or Python runtime is invalid"):
        _build(
            _builder(git_reader=_FakeGitReader(source_commit="invalid")),
            candidate,
            candidate_path,
            dataset,
        )
    with pytest.raises(FreezeError, match="repository root does not match"):
        _build(
            _builder(git_reader=_FakeGitReader(repository_root=tmp_path)),
            candidate,
            candidate_path,
            dataset,
        )

    invalid_execution = candidate.execution_manifest.model_copy(
        update={"condition_bundle_checksum": "f" * 64}
    )
    invalid_execution = invalid_execution.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(invalid_execution)}
    )
    invalid_candidate = candidate.model_copy(
        update={"execution_manifest": invalid_execution}
    )
    candidate_path.write_bytes(canonical_model_bytes(invalid_candidate))
    with pytest.raises(FreezeError, match="condition bundle checksum is invalid"):
        _build(_builder(), invalid_candidate, candidate_path, dataset)


def test_environment_verification_is_strict_but_content_only_is_portable(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    manifest = _build(_builder(), candidate, candidate_path, dataset)
    plan = load_execution_plan(PLAN_PATH, dataset)

    with pytest.raises(FreezeError, match="Python runtime does not match"):
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            git_reader=_FakeGitReader(),
            environment_reader=_FakeEnvironmentReader(version="3.11.14"),
        )
    with pytest.raises(FreezeError, match="provider SDK does not match"):
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            git_reader=_FakeGitReader(),
            environment_reader=_FakeEnvironmentReader(sdk_version="2.45.0"),
        )
    with pytest.raises(FreezeError, match="changed while verifying"):
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=PLAN_PATH,
            git_reader=_ChangingGitReader(),
            environment_reader=_FakeEnvironmentReader(),
        )

    verify_freeze_manifest(
        manifest,
        repository_root=REPOSITORY_ROOT,
        dataset=dataset,
        plan=plan,
        plan_path=PLAN_PATH,
        verify_environment=False,
        git_reader=_FakeGitReader(source_commit="f" * 40, clean=False),
        environment_reader=_FakeEnvironmentReader(implementation="PyPy"),
    )


def test_freeze_loaders_reject_missing_invalid_and_noncanonical_files(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(FreezeError, match="cannot be read"):
        load_freeze_candidate_spec(missing)

    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"not-json\n")
    with pytest.raises(FreezeError, match="is invalid"):
        load_freeze_candidate_spec(invalid)

    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    manifest = _build(_builder(), candidate, candidate_path, dataset)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    with pytest.raises(FreezeError, match="not canonical JSON"):
        load_frozen_experiment_manifest(manifest_path)

    bad_checksum = manifest.model_copy(update={"manifest_checksum": "f" * 64})
    manifest_path.write_bytes(canonical_model_bytes(bad_checksum))
    with pytest.raises(FreezeError, match="manifest checksum mismatch"):
        load_frozen_experiment_manifest(manifest_path)


def test_git_reader_rejects_wrong_root_command_failure_and_non_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = SubprocessGitSnapshotReader()
    with pytest.raises(FreezeError, match="root does not match"):
        reader.snapshot(REPOSITORY_ROOT / "experiments")

    def failed_run(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=(), returncode=1, stdout=b"", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(FreezeError, match="source state cannot be read"):
        reader.snapshot(REPOSITORY_ROOT)

    def invalid_utf8_run(
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=b"\xff",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", invalid_utf8_run)
    with pytest.raises(FreezeError, match="Git output must be UTF-8"):
        reader.snapshot(REPOSITORY_ROOT)


def test_candidate_identity_and_spec_inventory_cannot_be_forged(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)

    different_review = candidate.model_copy(update={"freeze_id": "other-freeze"})
    with pytest.raises(FreezeError, match="bytes do not match reviewed inputs"):
        _build(_builder(), different_review, candidate_path, dataset)

    wrong_experiment = candidate.model_copy(update={"experiment_id": "other-pilot"})
    candidate_path.write_bytes(canonical_model_bytes(wrong_experiment))
    with pytest.raises(FreezeError, match="experiment identity does not match"):
        _build(_builder(), wrong_experiment, candidate_path, dataset)

    wrong_definition = candidate.model_copy(update={"definition_checksum": "f" * 64})
    candidate_path.write_bytes(canonical_model_bytes(wrong_definition))
    with pytest.raises(FreezeError, match="definition checksum does not match"):
        _build(_builder(), wrong_definition, candidate_path, dataset)

    without_spec = candidate.model_copy(
        update={
            "inventory_paths": tuple(
                path
                for path in candidate.inventory_paths
                if path != candidate_path.relative_to(REPOSITORY_ROOT).as_posix()
            )
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(without_spec))
    with pytest.raises(FreezeError, match="spec must be included"):
        _build(_builder(), without_spec, candidate_path, dataset)

    bad_execution = candidate.execution_manifest.model_copy(
        update={"manifest_checksum": "f" * 64}
    )
    invalid_execution = candidate.model_copy(
        update={"execution_manifest": bad_execution}
    )
    candidate_path.write_bytes(canonical_model_bytes(invalid_execution))
    with pytest.raises(FreezeError, match="execution identity is invalid"):
        _build(_builder(), invalid_execution, candidate_path, dataset)


def test_inventory_requires_existing_regular_files(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "freeze-candidate.json"
    candidate = _write_candidate(dataset, candidate_path)
    missing_path = (tmp_path / "missing.txt").relative_to(REPOSITORY_ROOT).as_posix()
    missing = candidate.model_copy(
        update={
            "inventory_paths": tuple(sorted((*candidate.inventory_paths, missing_path)))
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(missing))
    with pytest.raises(FreezeError, match="cannot be resolved"):
        _build(_builder(), missing, candidate_path, dataset)

    directory_path = tmp_path.relative_to(REPOSITORY_ROOT).as_posix()
    directory = candidate.model_copy(
        update={
            "inventory_paths": tuple(
                sorted((*candidate.inventory_paths, directory_path))
            )
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(directory))
    with pytest.raises(FreezeError, match="must be a repository file"):
        _build(_builder(), directory, candidate_path, dataset)

    empty_path = tmp_path / "empty.txt"
    empty_path.touch()
    empty = candidate.model_copy(
        update={
            "inventory_paths": tuple(
                sorted(
                    (
                        *candidate.inventory_paths,
                        empty_path.relative_to(REPOSITORY_ROOT).as_posix(),
                    )
                )
            )
        }
    )
    candidate_path.write_bytes(canonical_model_bytes(empty))
    with pytest.raises(FreezeError, match="file size is invalid"):
        _build(_builder(), empty, candidate_path, dataset)
