"""M5.5.3 Pilot fixture, isolation, and reviewed budget tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from experiments.contracts import (
    ExecutionPlan,
    ExperimentPurpose,
    FreezeCandidateSpec,
    GenerationConfig,
)
from experiments.freezing import (
    FreezeCandidateBuilder,
    GitSnapshot,
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
    verify_freeze_manifest,
)
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset
from experiments.pilot import PilotPreflightError, validate_pilot_preflight
from experiments.planning import calculate_manifest_checksum, load_execution_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PILOT_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-pilot-v1"
FORMAL_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PILOT_PLAN_PATH = PILOT_ROOT / "execution-plan.json"
FORMAL_PLAN_PATH = FORMAL_ROOT / "execution-plan.json"
CANDIDATE_PATH = PILOT_ROOT / "freeze-candidate.json"
FINAL_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "evidence"
    / "writer-pilot-v1"
    / "freeze-manifest.json"
)
HISTORICAL_MANIFEST_PATH = FINAL_MANIFEST_PATH.with_name("freeze-manifest-m5.5.5.json")
M557_MANIFEST_PATH = FINAL_MANIFEST_PATH.with_name("freeze-manifest-m5.5.7.json")


@dataclass(frozen=True, slots=True)
class _CleanGitReader:
    source_commit: str = "a" * 40

    def snapshot(self, repository_root: Path) -> GitSnapshot:
        assert repository_root == REPOSITORY_ROOT
        return GitSnapshot(
            repository_root=repository_root,
            source_commit=self.source_commit,
            working_tree_clean=True,
        )


@dataclass(frozen=True, slots=True)
class _FrozenEnvironmentReader:
    def python_implementation(self) -> str:
        return "CPython"

    def python_version(self) -> str:
        return "3.11.15"

    def distribution_version(self, distribution_name: str) -> str:
        assert distribution_name == "openai"
        return "2.46.0"


def _inputs() -> tuple[
    LoadedExperimentDataset,
    ExecutionPlan,
    LoadedExperimentDataset,
    ExecutionPlan,
    FreezeCandidateSpec,
]:
    pilot_dataset = load_experiment_dataset(PILOT_ROOT)
    pilot_plan = load_execution_plan(PILOT_PLAN_PATH, pilot_dataset)
    formal_dataset = load_experiment_dataset(FORMAL_ROOT)
    formal_plan = load_execution_plan(FORMAL_PLAN_PATH, formal_dataset)
    candidate = load_freeze_candidate_spec(CANDIDATE_PATH)
    return pilot_dataset, pilot_plan, formal_dataset, formal_plan, candidate


def _with_generation(
    candidate: FreezeCandidateSpec,
    generation: GenerationConfig,
) -> FreezeCandidateSpec:
    execution = candidate.execution_manifest.model_copy(
        update={"generation": generation}
    )
    execution = execution.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(execution)}
    )
    return candidate.model_copy(update={"execution_manifest": execution})


def _preflight(candidate: FreezeCandidateSpec) -> None:
    pilot_dataset, pilot_plan, formal_dataset, formal_plan, _ = _inputs()
    validate_pilot_preflight(
        pilot_dataset=pilot_dataset,
        pilot_plan=pilot_plan,
        candidate=candidate,
        formal_dataset=formal_dataset,
        formal_plan=formal_plan,
    )


def test_pilot_fixture_is_small_balanced_and_identity_isolated() -> None:
    pilot_dataset, pilot_plan, formal_dataset, formal_plan, candidate = _inputs()

    report = validate_pilot_preflight(
        pilot_dataset=pilot_dataset,
        pilot_plan=pilot_plan,
        candidate=candidate,
        formal_dataset=formal_dataset,
        formal_plan=formal_plan,
    )

    assert len(pilot_dataset.definition.domain_ids) == 2
    assert pilot_dataset.definition.tasks_per_scenario_per_domain == 1
    assert report.pilot_experiment_id == "writer-pilot-v1"
    assert report.formal_experiment_id == "writer-validation-v1"
    assert report.task_count == 4
    assert report.run_count == 8
    assert report.estimated_provider_requests == 8
    assert report.max_provider_requests == 16
    assert report.estimated_cost_usd_micros == 25_908
    assert report.hard_cost_limit_usd_micros == 51_815


def test_pilot_candidate_binds_reviewed_model_price_and_complete_inputs() -> None:
    pilot_dataset, pilot_plan, _, _, candidate = _inputs()

    assert candidate.purpose is ExperimentPurpose.PILOT
    assert candidate.provider.provider == "openai"
    assert candidate.provider.model == "gpt-4.1-mini-2025-04-14"
    assert candidate.provider.api_name == "responses"
    assert candidate.provider.sdk_version == "2.46.0"
    assert candidate.provider.model_is_immutable_snapshot is True
    assert candidate.pricing.input_usd_micros_per_unit == 400_000
    assert candidate.pricing.cached_input_usd_micros_per_unit == 100_000
    assert candidate.pricing.output_usd_micros_per_unit == 1_600_000
    assert candidate.pricing.source_url == (
        "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
    )
    assert candidate.execution_manifest.generation.max_attempts == 2
    assert candidate.execution_manifest.generation.concurrency == 1
    assert candidate.execution_manifest.limits.max_provider_requests == 16
    assert "experiments/pilot.py" in candidate.inventory_paths
    assert "experiments/pilot_launcher.py" in candidate.inventory_paths
    assert "experiments/openai_gateway.py" in candidate.inventory_paths
    top_level_experiment_sources = {
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in (REPOSITORY_ROOT / "experiments").glob("*.py")
    }
    assert top_level_experiment_sources <= set(candidate.inventory_paths)
    production_runtime_sources = {
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in (REPOSITORY_ROOT / "src" / "agent_factory").rglob("*")
        if path.is_file() and path.suffix in {".py", ".sql"}
    }
    assert len(production_runtime_sources) == 91
    assert production_runtime_sources <= set(candidate.inventory_paths)
    assert "experiments/definitions/writer-v1/execution-plan.json" in (
        candidate.inventory_paths
    )
    assert CANDIDATE_PATH.relative_to(REPOSITORY_ROOT).as_posix() in (
        candidate.inventory_paths
    )

    git_reader = _CleanGitReader()
    environment_reader = _FrozenEnvironmentReader()
    manifest = FreezeCandidateBuilder(
        REPOSITORY_ROOT,
        git_reader=git_reader,
        environment_reader=environment_reader,
    ).build(
        candidate=candidate,
        candidate_spec_path=CANDIDATE_PATH,
        plan_path=PILOT_PLAN_PATH,
        dataset=pilot_dataset,
        plan=pilot_plan,
    )

    assert tuple(item.path for item in manifest.files) == candidate.inventory_paths
    verify_freeze_manifest(
        manifest,
        repository_root=REPOSITORY_ROOT,
        dataset=pilot_dataset,
        plan=pilot_plan,
        plan_path=PILOT_PLAN_PATH,
        git_reader=git_reader,
        environment_reader=environment_reader,
    )


def test_archived_pilot_freeze_manifest_retains_historical_identity() -> None:
    manifest_bytes = HISTORICAL_MANIFEST_PATH.read_bytes()
    manifest = load_frozen_experiment_manifest(HISTORICAL_MANIFEST_PATH)

    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "a3216e6b292126c5041ab701c1864c53e56ba15faac3d33ecd55c69d3a59d7b2"
    )
    assert manifest.manifest_checksum == (
        "2673435ce2623c7c5bfaeb4a011c72f0558ef557c3506bba6685d114357bb6af"
    )
    assert manifest.source.source_commit == ("5a5d58cb42b62e3d2e10a060fea72d4ae0a97498")
    assert len(manifest.files) == 61
    assert "experiments/pilot_launcher.py" not in {
        artifact.path for artifact in manifest.files
    }


def test_m557_manifest_retains_pre_closure_identity() -> None:
    manifest_bytes = M557_MANIFEST_PATH.read_bytes()
    manifest = load_frozen_experiment_manifest(M557_MANIFEST_PATH)

    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "994f0d46557adeea77703849b0eb3978abe3d9fe89a1741c01b802ffcd2d2740"
    )
    assert manifest.manifest_checksum == (
        "6514a01799af9b6585f4ff009ad11c887439a324200771d0cae479f28f630d22"
    )
    assert manifest.source.source_commit == ("d3c19beb75587b5cc9963c05832c918694dfa9e1")
    assert len(manifest.files) == 62
    assert "experiments/pilot_launcher.py" in {
        artifact.path for artifact in manifest.files
    }
    assert not any(
        artifact.path.startswith("src/agent_factory/") for artifact in manifest.files
    )


def test_pilot_preflight_rejects_formal_identity_overlap() -> None:
    pilot_dataset, pilot_plan, _, _, candidate = _inputs()

    with pytest.raises(PilotPreflightError, match="experiment identities overlap"):
        validate_pilot_preflight(
            pilot_dataset=pilot_dataset,
            pilot_plan=pilot_plan,
            candidate=candidate,
            formal_dataset=pilot_dataset,
            formal_plan=pilot_plan,
        )


def test_pilot_preflight_rejects_budget_drift() -> None:
    pilot_dataset, pilot_plan, formal_dataset, formal_plan, candidate = _inputs()
    drifted_budget = candidate.cost_budget.model_copy(
        update={"hard_cost_limit_usd_micros": 51_814}
    )
    drifted_candidate = candidate.model_copy(update={"cost_budget": drifted_budget})

    with pytest.raises(PilotPreflightError, match="hard cost limit"):
        validate_pilot_preflight(
            pilot_dataset=pilot_dataset,
            pilot_plan=pilot_plan,
            candidate=drifted_candidate,
            formal_dataset=formal_dataset,
            formal_plan=formal_plan,
        )


def test_pilot_preflight_rejects_candidate_identity_drift() -> None:
    _, _, _, _, candidate = _inputs()

    with pytest.raises(PilotPreflightError, match="purpose must be pilot"):
        _preflight(candidate.model_copy(update={"purpose": ExperimentPurpose.FORMAL}))
    with pytest.raises(PilotPreflightError, match="experiment identities differ"):
        _preflight(candidate.model_copy(update={"experiment_id": "other-pilot"}))
    with pytest.raises(PilotPreflightError, match="definition checksum is stale"):
        _preflight(candidate.model_copy(update={"definition_checksum": "f" * 64}))

    execution = candidate.execution_manifest.model_copy(
        update={"condition_bundle_checksum": "f" * 64}
    )
    execution = execution.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(execution)}
    )
    with pytest.raises(PilotPreflightError, match="condition bundle checksum is stale"):
        _preflight(candidate.model_copy(update={"execution_manifest": execution}))


def test_pilot_preflight_rejects_model_and_technical_limit_drift() -> None:
    _, _, _, _, candidate = _inputs()
    concurrent = candidate.execution_manifest.generation.model_copy(
        update={"concurrency": 2}
    )
    with pytest.raises(PilotPreflightError, match="concurrency"):
        _preflight(_with_generation(candidate, concurrent))

    mutable_provider = candidate.provider.model_copy(
        update={"model_is_immutable_snapshot": False}
    )
    with pytest.raises(PilotPreflightError, match="immutable snapshot"):
        _preflight(candidate.model_copy(update={"provider": mutable_provider}))

    for field, message in (
        ("max_provider_requests", "request limit"),
        ("max_prompt_tokens", "prompt limit"),
        ("max_completion_tokens", "completion limit"),
    ):
        limits = candidate.execution_manifest.limits.model_copy(update={field: 1})
        execution = candidate.execution_manifest.model_copy(update={"limits": limits})
        execution = execution.model_copy(
            update={"manifest_checksum": calculate_manifest_checksum(execution)}
        )
        with pytest.raises(PilotPreflightError, match=message):
            _preflight(candidate.model_copy(update={"execution_manifest": execution}))


def test_pilot_preflight_rejects_estimated_usage_and_cost_drift() -> None:
    _, _, _, _, candidate = _inputs()
    estimated_usage = candidate.cost_budget.model_copy(
        update={"estimated_provider_requests": 7}
    )
    with pytest.raises(PilotPreflightError, match="estimated usage"):
        _preflight(candidate.model_copy(update={"cost_budget": estimated_usage}))

    estimated_cost = candidate.cost_budget.model_copy(
        update={"estimated_cost_usd_micros": 25_907}
    )
    with pytest.raises(PilotPreflightError, match="estimated cost"):
        _preflight(candidate.model_copy(update={"cost_budget": estimated_cost}))
