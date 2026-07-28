"""Deterministic construction of the reviewed formal freeze candidate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from experiments.artifacts import ArtifactStore
from experiments.contracts import (
    AnalysisConfig,
    CostBudget,
    ExecutionLimits,
    ExecutionPlan,
    ExperimentPurpose,
    FreezeCandidateSpec,
    PilotEvidenceRef,
    PriceSnapshot,
    calculate_conservative_cost_micros,
)
from experiments.evidence_sealing import load_pilot_evidence_seal
from experiments.formal import validate_formal_preflight
from experiments.freezing import load_frozen_experiment_manifest
from experiments.loader import LoadedExperimentDataset
from experiments.planning import build_execution_manifest
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
)


class FormalCandidateDraftError(RuntimeError):
    """Reviewed formal inputs cannot produce one canonical candidate."""


@dataclass(frozen=True, slots=True)
class FormalCandidateDraftRequest:
    repository_root: Path
    candidate_path: Path
    pilot_manifest_path: Path
    pilot_report_path: Path
    pilot_evidence_seal_path: Path
    pricing_source_url: str
    pricing_captured_at: datetime
    created_at: datetime


def build_formal_freeze_candidate(
    *,
    request: FormalCandidateDraftRequest,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
) -> FreezeCandidateSpec:
    """Derive exact formal limits, budget, inventory, and Pilot provenance."""

    root = request.repository_root.resolve(strict=True)
    pilot_manifest = load_frozen_experiment_manifest(request.pilot_manifest_path)
    seal = load_pilot_evidence_seal(request.pilot_evidence_seal_path)
    if (
        pilot_manifest.purpose is not ExperimentPurpose.PILOT
        or pilot_manifest.experiment_id != seal.experiment_id
        or pilot_manifest.manifest_checksum != seal.freeze_manifest_checksum
        or pilot_manifest.execution_manifest.manifest_checksum
        != seal.execution_manifest_checksum
    ):
        raise FormalCandidateDraftError("Pilot Manifest and evidence seal do not match")

    manifest_path = _repository_file(root, request.pilot_manifest_path)
    report_path = _repository_file(root, request.pilot_report_path)
    seal_path = _repository_file(root, request.pilot_evidence_seal_path)
    candidate_path = _repository_path(root, request.candidate_path)
    report_bytes = request.pilot_report_path.read_bytes()
    if not report_bytes:
        raise FormalCandidateDraftError("Pilot review report cannot be empty")
    pilot_evidence = PilotEvidenceRef(
        experiment_id=pilot_manifest.experiment_id,
        freeze_manifest_path=manifest_path,
        freeze_manifest_checksum=pilot_manifest.manifest_checksum,
        report_path=report_path,
        report_checksum=hashlib.sha256(report_bytes).hexdigest(),
        evidence_seal_path=seal_path,
        evidence_seal_checksum=seal.seal_checksum,
    )

    generation = pilot_manifest.execution_manifest.generation
    maximum_requests = len(plan.items) * generation.max_attempts
    prompt_tokens_per_attempt = (
        pilot_manifest.execution_manifest.limits.prompt_tokens_per_attempt_upper_bound
    )
    limits = ExecutionLimits(
        max_provider_requests=maximum_requests,
        max_prompt_tokens=maximum_requests * prompt_tokens_per_attempt,
        max_completion_tokens=maximum_requests * generation.max_output_tokens,
        prompt_tokens_per_attempt_upper_bound=prompt_tokens_per_attempt,
    )
    _, prompt_bytes = load_manual_system_prompt(
        dataset.root / "conditions" / "manual-system.txt"
    )
    execution = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(prompt_bytes),
        generation=generation,
        limits=limits,
    )
    pricing = PriceSnapshot(
        provider=pilot_manifest.provider.provider,
        model=pilot_manifest.provider.model,
        currency="CNY",
        input_micros_per_unit=6_500_000,
        cached_input_micros_per_unit=1_100_000,
        output_micros_per_unit=27_000_000,
        source_url=request.pricing_source_url,
        captured_at=request.pricing_captured_at,
    )
    estimated_requests = len(plan.items)
    estimated_prompt_tokens = estimated_requests * prompt_tokens_per_attempt
    estimated_completion_tokens = estimated_requests * generation.max_output_tokens
    budget = CostBudget(
        currency=pricing.currency,
        estimated_provider_requests=estimated_requests,
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens=estimated_completion_tokens,
        estimated_cost_micros=calculate_conservative_cost_micros(
            input_tokens=estimated_prompt_tokens,
            output_tokens=estimated_completion_tokens,
            pricing=pricing,
        ),
        hard_cost_limit_micros=calculate_conservative_cost_micros(
            input_tokens=limits.max_prompt_tokens,
            output_tokens=limits.max_completion_tokens,
            pricing=pricing,
        ),
    )
    candidate = FreezeCandidateSpec(
        purpose=ExperimentPurpose.FORMAL,
        freeze_id="writer-validation-v1-moonshot-kimi-k2-6",
        experiment_id=dataset.definition.experiment_id,
        definition_checksum=plan.definition_checksum,
        execution_manifest=execution,
        analysis_config=AnalysisConfig(
            bootstrap_seed=dataset.definition.randomization_seed,
            bootstrap_iterations=10_000,
        ),
        provider=pilot_manifest.provider,
        pricing=pricing,
        cost_budget=budget,
        pilot_evidence=pilot_evidence,
        inventory_paths=_build_inventory(
            root=root,
            dataset=dataset,
            candidate_path=candidate_path,
            pilot_evidence=pilot_evidence,
        ),
        created_at=request.created_at,
    )
    validate_formal_preflight(dataset=dataset, plan=plan, candidate=candidate)
    return candidate


def publish_formal_freeze_candidate(
    candidate: FreezeCandidateSpec,
    output_path: Path,
) -> bool:
    output = output_path.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    return ArtifactStore(output.parent).write_model_once(output.name, candidate)


def _build_inventory(
    *,
    root: Path,
    dataset: LoadedExperimentDataset,
    candidate_path: str,
    pilot_evidence: PilotEvidenceRef,
) -> tuple[str, ...]:
    paths = {
        "pyproject.toml",
        "uv.lock",
        "docs/design/experiment-protocol.md",
        "docs/milestones/m5-validation-experiment.md",
        candidate_path,
        pilot_evidence.freeze_manifest_path,
        pilot_evidence.report_path,
        pilot_evidence.evidence_seal_path,
        *(
            _repository_file(root, path)
            for path in dataset.root.rglob("*")
            if path.is_file()
        ),
    }
    for source_root, suffixes in (
        (root / "experiments", {".py"}),
        (root / "src" / "agent_factory", {".py", ".sql"}),
    ):
        for path in source_root.rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                paths.add(_repository_file(root, path))
    return tuple(sorted(paths))


def _repository_file(root: Path, path: Path) -> str:
    relative = _repository_path(root, path)
    if path.is_symlink():
        raise FormalCandidateDraftError(
            "formal inventory cannot contain symbolic links"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FormalCandidateDraftError(
            "formal inventory file cannot be resolved"
        ) from exc
    if not resolved.is_file():
        raise FormalCandidateDraftError("formal inventory requires regular files")
    return relative


def _repository_path(root: Path, path: Path) -> str:
    unresolved = path if path.is_absolute() else root / path
    resolved = unresolved.resolve(strict=False)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise FormalCandidateDraftError(
            "formal inventory must stay in repository"
        ) from exc
    if not relative or ".." in Path(relative).parts:
        raise FormalCandidateDraftError("formal inventory path is invalid")
    return relative
