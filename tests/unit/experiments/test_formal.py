"""Formal experiment preflight tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.cli import main
from experiments.contracts import (
    CostBudget,
    ExecutionLimits,
    ExperimentPurpose,
    FreezeCandidateSpec,
    PilotEvidenceRef,
    calculate_conservative_cost_micros,
)
from experiments.formal import FormalPreflightError, validate_formal_preflight
from experiments.freezing import load_freeze_candidate_spec
from experiments.loader import load_experiment_dataset
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
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"
PILOT_CANDIDATE_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "definitions"
    / "writer-pilot-v1"
    / "freeze-candidate.json"
)
FORMAL_CANDIDATE_PATH = DEFINITION_ROOT / "freeze-candidate.json"


def _formal_candidate() -> FreezeCandidateSpec:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    pilot = load_freeze_candidate_spec(PILOT_CANDIDATE_PATH)
    _, prompt_bytes = load_manual_system_prompt(
        DEFINITION_ROOT / "conditions" / "manual-system.txt"
    )
    generation = pilot.execution_manifest.generation
    max_requests = len(plan.items) * generation.max_attempts
    limits = ExecutionLimits(
        max_provider_requests=max_requests,
        max_prompt_tokens=max_requests * 4_000,
        max_completion_tokens=max_requests * generation.max_output_tokens,
        prompt_tokens_per_attempt_upper_bound=4_000,
    )
    execution = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(prompt_bytes),
        generation=generation,
        limits=limits,
    )
    estimated_prompt = len(plan.items) * 4_000
    estimated_completion = len(plan.items) * generation.max_output_tokens
    estimated_cost = calculate_conservative_cost_micros(
        input_tokens=estimated_prompt,
        output_tokens=estimated_completion,
        pricing=pilot.pricing,
    )
    hard_cost = calculate_conservative_cost_micros(
        input_tokens=limits.max_prompt_tokens,
        output_tokens=limits.max_completion_tokens,
        pricing=pilot.pricing,
    )
    evidence = PilotEvidenceRef(
        experiment_id="writer-pilot-v1",
        freeze_manifest_path=(
            "experiments/evidence/writer-pilot-v1/freeze-manifest.json"
        ),
        freeze_manifest_checksum="a" * 64,
        report_path="docs/reports/m5.5-moonshot-pilot-review.md",
        report_checksum="b" * 64,
        evidence_seal_path=(
            "experiments/evidence/writer-pilot-v1/evidence-seal-mfjs-20260728.json"
        ),
        evidence_seal_checksum="c" * 64,
    )
    inventory = tuple(
        sorted(
            {
                "uv.lock",
                evidence.freeze_manifest_path,
                evidence.report_path,
                evidence.evidence_seal_path,
            }
        )
    )
    return FreezeCandidateSpec(
        purpose=ExperimentPurpose.FORMAL,
        freeze_id="writer-validation-v1-moonshot",
        experiment_id=dataset.definition.experiment_id,
        definition_checksum=plan.definition_checksum,
        execution_manifest=execution,
        analysis_config=pilot.analysis_config,
        provider=pilot.provider,
        pricing=pilot.pricing,
        cost_budget=CostBudget(
            currency=pilot.pricing.currency,
            estimated_provider_requests=len(plan.items),
            estimated_prompt_tokens=estimated_prompt,
            estimated_completion_tokens=estimated_completion,
            estimated_cost_micros=estimated_cost,
            hard_cost_limit_micros=hard_cost,
        ),
        pilot_evidence=evidence,
        inventory_paths=inventory,
        created_at=pilot.created_at,
    )


def _with_execution_update(
    candidate: FreezeCandidateSpec,
    **updates: object,
) -> FreezeCandidateSpec:
    execution = candidate.execution_manifest.model_copy(update=updates)
    execution = execution.model_copy(
        update={"manifest_checksum": calculate_manifest_checksum(execution)}
    )
    return candidate.model_copy(update={"execution_manifest": execution})


def test_formal_preflight_proves_exact_balanced_design_and_budget() -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)

    report = validate_formal_preflight(
        dataset=dataset,
        plan=plan,
        candidate=_formal_candidate(),
    )

    assert report.task_count == 24
    assert report.repetitions == 5
    assert (report.manual_run_count, report.factory_run_count) == (120, 120)
    assert (report.estimated_provider_requests, report.max_provider_requests) == (
        240,
        480,
    )
    assert report.currency == "CNY"
    assert report.estimated_cost_micros == 12_875_520
    assert report.hard_cost_limit_micros == 25_751_040


def test_verify_formal_cli_reports_exact_reviewed_bounds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "verify-formal",
                "--definition-root",
                str(DEFINITION_ROOT),
                "--plan",
                str(PLAN_PATH),
                "--spec",
                str(FORMAL_CANDIDATE_PATH),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "tasks=24 repetitions=5 runs=120+120" in output
    assert "requests=240/480 currency=CNY" in output
    assert "cost_micros=12875520/25751040" in output


def test_formal_preflight_rejects_purpose_profile_and_budget_drift() -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    candidate = _formal_candidate()

    with pytest.raises(FormalPreflightError, match="purpose must be formal"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate.model_copy(update={"purpose": ExperimentPurpose.PILOT}),
        )

    generation = candidate.execution_manifest.generation.model_copy(
        update={"temperature": 0.7}
    )
    drifted_execution = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=(
            candidate.execution_manifest.condition_bundle_checksum
        ),
        generation=generation,
        limits=candidate.execution_manifest.limits,
    )
    with pytest.raises(FormalPreflightError, match="profile is not reviewed"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate.model_copy(
                update={"execution_manifest": drifted_execution}
            ),
        )

    bad_budget = candidate.cost_budget.model_copy(
        update={"hard_cost_limit_micros": 25_751_041}
    )
    with pytest.raises(FormalPreflightError, match="must equal the token ceiling"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate.model_copy(update={"cost_budget": bad_budget}),
        )


def test_formal_preflight_rejects_lineage_and_source_identity_drift() -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    candidate = _formal_candidate()

    cases = (
        (
            candidate.model_copy(update={"pilot_evidence": None}),
            "must reference Pilot evidence",
        ),
        (
            candidate.model_copy(update={"experiment_id": "writer-other-v1"}),
            "experiment identities differ",
        ),
        (
            candidate.model_copy(update={"definition_checksum": "f" * 64}),
            "definition checksum is stale",
        ),
        (
            _with_execution_update(
                candidate,
                condition_bundle_checksum="f" * 64,
            ),
            "condition bundle checksum is stale",
        ),
    )
    for changed, message in cases:
        with pytest.raises(FormalPreflightError, match=message):
            validate_formal_preflight(
                dataset=dataset,
                plan=plan,
                candidate=changed,
            )


def test_formal_preflight_rejects_execution_and_estimate_drift() -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    candidate = _formal_candidate()

    one_attempt = candidate.execution_manifest.generation.model_copy(
        update={"max_attempts": 1}
    )
    with pytest.raises(FormalPreflightError, match="one worker and two attempts"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=_with_execution_update(candidate, generation=one_attempt),
        )

    drifted_limits = candidate.execution_manifest.limits.model_copy(
        update={
            "max_prompt_tokens": (
                candidate.execution_manifest.limits.max_prompt_tokens + 1
            )
        }
    )
    with pytest.raises(FormalPreflightError, match="do not cover exact bounds"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=_with_execution_update(candidate, limits=drifted_limits),
        )

    drifted_usage = candidate.cost_budget.model_copy(
        update={"estimated_provider_requests": 239}
    )
    with pytest.raises(FormalPreflightError, match="one attempt per run"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate.model_copy(update={"cost_budget": drifted_usage}),
        )

    drifted_cost = candidate.cost_budget.model_copy(
        update={"estimated_cost_micros": 12_875_521}
    )
    with pytest.raises(FormalPreflightError, match="frozen pricing"):
        validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate.model_copy(update={"cost_budget": drifted_cost}),
        )
