"""Offline preflight for the frozen M5 Writer formal experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from agent_factory.domain.common import sha256_model
from experiments.contracts import (
    CurrencyCode,
    ExecutionPlan,
    ExperimentCondition,
    ExperimentPurpose,
    FreezeCandidateSpec,
    calculate_conservative_cost_micros,
)
from experiments.loader import LoadedExperimentDataset
from experiments.moonshot_gateway import MOONSHOT_MODEL, MOONSHOT_PROVIDER_OPTIONS
from experiments.planning import validate_execution_manifest, validate_execution_plan
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
)

_EXPECTED_TASKS = 24
_EXPECTED_REPETITIONS = 5
_EXPECTED_RUNS_PER_CONDITION = 120
_EXPECTED_TOTAL_RUNS = 240


class FormalPreflightError(ValueError):
    """A formal candidate differs from the preregistered design or budget."""


@dataclass(frozen=True, slots=True)
class FormalPreflightReport:
    experiment_id: str
    pilot_experiment_id: str
    task_count: int
    repetitions: int
    manual_run_count: int
    factory_run_count: int
    estimated_provider_requests: int
    max_provider_requests: int
    currency: CurrencyCode
    estimated_cost_micros: int
    hard_cost_limit_micros: int


def validate_formal_preflight(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    candidate: FreezeCandidateSpec,
) -> FormalPreflightReport:
    """Prove exact design, Moonshot profile, Pilot lineage, and budget bounds."""

    validate_execution_plan(plan, dataset)
    validate_execution_manifest(candidate.execution_manifest, dataset, plan)
    if candidate.purpose is not ExperimentPurpose.FORMAL:
        raise FormalPreflightError("candidate purpose must be formal")
    if candidate.pilot_evidence is None:
        raise FormalPreflightError("formal candidate must reference Pilot evidence")
    if candidate.experiment_id != dataset.definition.experiment_id:
        raise FormalPreflightError("candidate and formal experiment identities differ")
    if candidate.definition_checksum != sha256_model(dataset.definition):
        raise FormalPreflightError("formal candidate definition checksum is stale")
    if len(dataset.tasks) != _EXPECTED_TASKS:
        raise FormalPreflightError("formal experiment must contain exactly 24 tasks")
    if dataset.definition.repetitions != _EXPECTED_REPETITIONS:
        raise FormalPreflightError("formal experiment must use exactly 5 repetitions")

    run_counts = Counter(item.condition for item in plan.items)
    if (
        len(plan.items) != _EXPECTED_TOTAL_RUNS
        or run_counts[ExperimentCondition.MANUAL] != _EXPECTED_RUNS_PER_CONDITION
        or run_counts[ExperimentCondition.FACTORY] != _EXPECTED_RUNS_PER_CONDITION
    ):
        raise FormalPreflightError("formal plan must contain a balanced 120/120 design")

    _, manual_prompt_bytes = load_manual_system_prompt(
        dataset.root / "conditions" / "manual-system.txt"
    )
    if candidate.execution_manifest.condition_bundle_checksum != (
        calculate_condition_bundle_checksum(manual_prompt_bytes)
    ):
        raise FormalPreflightError("formal condition bundle checksum is stale")

    generation = candidate.execution_manifest.generation
    limits = candidate.execution_manifest.limits
    budget = candidate.cost_budget
    expected_request_count = _EXPECTED_TOTAL_RUNS
    maximum_request_count = expected_request_count * generation.max_attempts
    expected_prompt_tokens = (
        expected_request_count * limits.prompt_tokens_per_attempt_upper_bound
    )
    maximum_prompt_tokens = (
        maximum_request_count * limits.prompt_tokens_per_attempt_upper_bound
    )
    expected_completion_tokens = expected_request_count * generation.max_output_tokens
    maximum_completion_tokens = maximum_request_count * generation.max_output_tokens
    if generation.concurrency != 1 or generation.max_attempts != 2:
        raise FormalPreflightError(
            "formal execution requires one worker and two attempts"
        )
    if (
        generation.provider != "moonshot"
        or generation.model != MOONSHOT_MODEL
        or generation.temperature != 0.6
        or generation.max_output_tokens != 1024
        or generation.request_timeout_seconds != 60
        or generation.provider_options != MOONSHOT_PROVIDER_OPTIONS
        or candidate.provider.api_name != "chat-completions"
        or candidate.provider.sdk_name != "openai"
        or candidate.provider.model_is_immutable_snapshot
    ):
        raise FormalPreflightError("formal Moonshot inference profile is not reviewed")
    if (
        limits.max_provider_requests != maximum_request_count
        or limits.max_prompt_tokens != maximum_prompt_tokens
        or limits.max_completion_tokens != maximum_completion_tokens
    ):
        raise FormalPreflightError("formal execution limits do not cover exact bounds")
    if (
        budget.estimated_provider_requests != expected_request_count
        or budget.estimated_prompt_tokens != expected_prompt_tokens
        or budget.estimated_completion_tokens != expected_completion_tokens
    ):
        raise FormalPreflightError(
            "formal estimated usage must assume one attempt per run"
        )

    estimated_cost = calculate_conservative_cost_micros(
        input_tokens=expected_prompt_tokens,
        output_tokens=expected_completion_tokens,
        pricing=candidate.pricing,
    )
    hard_cost_limit = calculate_conservative_cost_micros(
        input_tokens=maximum_prompt_tokens,
        output_tokens=maximum_completion_tokens,
        pricing=candidate.pricing,
    )
    if budget.estimated_cost_micros != estimated_cost:
        raise FormalPreflightError(
            "formal estimated cost does not match frozen pricing"
        )
    if budget.hard_cost_limit_micros != hard_cost_limit:
        raise FormalPreflightError(
            "formal hard cost limit must equal the token ceiling"
        )

    return FormalPreflightReport(
        experiment_id=dataset.definition.experiment_id,
        pilot_experiment_id=candidate.pilot_evidence.experiment_id,
        task_count=len(dataset.tasks),
        repetitions=dataset.definition.repetitions,
        manual_run_count=run_counts[ExperimentCondition.MANUAL],
        factory_run_count=run_counts[ExperimentCondition.FACTORY],
        estimated_provider_requests=expected_request_count,
        max_provider_requests=maximum_request_count,
        currency=budget.currency,
        estimated_cost_micros=estimated_cost,
        hard_cost_limit_micros=hard_cost_limit,
    )
