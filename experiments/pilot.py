"""Offline Pilot isolation and budget preflight for M5.5."""

from __future__ import annotations

from dataclasses import dataclass

from agent_factory.domain.common import sha256_model
from experiments.contracts import (
    CurrencyCode,
    ExecutionPlan,
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


class PilotPreflightError(ValueError):
    """A reviewed Pilot candidate is unsafe or overlaps the formal experiment."""


@dataclass(frozen=True, slots=True)
class PilotPreflightReport:
    """Compact facts emitted after every offline Pilot precondition passes."""

    pilot_experiment_id: str
    formal_experiment_id: str
    task_count: int
    run_count: int
    estimated_provider_requests: int
    max_provider_requests: int
    currency: CurrencyCode
    estimated_cost_micros: int
    hard_cost_limit_micros: int


def validate_pilot_preflight(
    *,
    pilot_dataset: LoadedExperimentDataset,
    pilot_plan: ExecutionPlan,
    candidate: FreezeCandidateSpec,
    formal_dataset: LoadedExperimentDataset,
    formal_plan: ExecutionPlan,
) -> PilotPreflightReport:
    """Prove Pilot isolation and exact one-attempt/two-attempt budget bounds."""

    validate_execution_plan(pilot_plan, pilot_dataset)
    validate_execution_plan(formal_plan, formal_dataset)
    validate_execution_manifest(
        candidate.execution_manifest,
        pilot_dataset,
        pilot_plan,
    )
    if candidate.purpose is not ExperimentPurpose.PILOT:
        raise PilotPreflightError("candidate purpose must be pilot")
    if candidate.experiment_id != pilot_dataset.definition.experiment_id:
        raise PilotPreflightError("candidate and Pilot experiment identities differ")
    if candidate.definition_checksum != sha256_model(pilot_dataset.definition):
        raise PilotPreflightError("candidate definition checksum is stale")
    if pilot_dataset.definition.repetitions != 1:
        raise PilotPreflightError("Pilot must use one repetition per condition")
    if pilot_dataset.definition.tasks_per_scenario_per_domain != 1:
        raise PilotPreflightError("Pilot must use a 1+1 scenario matrix per domain")

    _, manual_prompt_bytes = load_manual_system_prompt(
        pilot_dataset.root / "conditions" / "manual-system.txt"
    )
    expected_bundle_checksum = calculate_condition_bundle_checksum(manual_prompt_bytes)
    if (
        candidate.execution_manifest.condition_bundle_checksum
        != expected_bundle_checksum
    ):
        raise PilotPreflightError("Pilot condition bundle checksum is stale")

    _require_distinct_identity(
        "experiment",
        {pilot_dataset.definition.experiment_id},
        {formal_dataset.definition.experiment_id},
    )
    _require_distinct_identity(
        "domain",
        set(pilot_dataset.definition.domain_ids),
        set(formal_dataset.definition.domain_ids),
    )
    _require_distinct_identity(
        "task",
        {item.task_id for item in pilot_dataset.tasks},
        {item.task_id for item in formal_dataset.tasks},
    )
    _require_distinct_identity(
        "rubric",
        {item.rubric_id for item in pilot_dataset.rubrics},
        {item.rubric_id for item in formal_dataset.rubrics},
    )
    _require_distinct_identity(
        "knowledge",
        {(item.knowledge_id, item.version) for item in pilot_dataset.knowledge},
        {(item.knowledge_id, item.version) for item in formal_dataset.knowledge},
    )
    _require_distinct_identity(
        "run",
        {item.run_id for item in pilot_plan.items},
        {item.run_id for item in formal_plan.items},
    )

    generation = candidate.execution_manifest.generation
    limits = candidate.execution_manifest.limits
    budget = candidate.cost_budget
    run_count = len(pilot_plan.items)
    expected_request_count = run_count
    maximum_request_count = run_count * generation.max_attempts
    expected_prompt_tokens = (
        expected_request_count * limits.prompt_tokens_per_attempt_upper_bound
    )
    maximum_prompt_tokens = (
        maximum_request_count * limits.prompt_tokens_per_attempt_upper_bound
    )
    expected_completion_tokens = expected_request_count * generation.max_output_tokens
    maximum_completion_tokens = maximum_request_count * generation.max_output_tokens
    if generation.concurrency != 1:
        raise PilotPreflightError("Pilot concurrency must remain one")
    if (
        generation.provider != "moonshot"
        or generation.model != MOONSHOT_MODEL
        or generation.temperature != 0.6
        or generation.provider_options != MOONSHOT_PROVIDER_OPTIONS
        or candidate.provider.api_name != "chat-completions"
        or candidate.provider.sdk_name != "openai"
    ):
        raise PilotPreflightError("Pilot Moonshot inference profile is not reviewed")
    if (
        generation.provider == "moonshot"
        and candidate.provider.model_is_immutable_snapshot
    ):
        raise PilotPreflightError(
            "Moonshot provider alias cannot claim immutable snapshot semantics"
        )
    if limits.max_provider_requests != maximum_request_count:
        raise PilotPreflightError("Pilot request limit must cover exactly all attempts")
    if limits.max_prompt_tokens != maximum_prompt_tokens:
        raise PilotPreflightError("Pilot prompt limit does not match attempt bounds")
    if limits.max_completion_tokens != maximum_completion_tokens:
        raise PilotPreflightError(
            "Pilot completion limit does not match attempt bounds"
        )
    if (
        budget.estimated_provider_requests != expected_request_count
        or budget.estimated_prompt_tokens != expected_prompt_tokens
        or budget.estimated_completion_tokens != expected_completion_tokens
    ):
        raise PilotPreflightError(
            "Pilot estimated usage must assume one attempt per run"
        )

    expected_cost = calculate_conservative_cost_micros(
        input_tokens=expected_prompt_tokens,
        output_tokens=expected_completion_tokens,
        pricing=candidate.pricing,
    )
    hard_cost_limit = calculate_conservative_cost_micros(
        input_tokens=maximum_prompt_tokens,
        output_tokens=maximum_completion_tokens,
        pricing=candidate.pricing,
    )
    if budget.estimated_cost_micros != expected_cost:
        raise PilotPreflightError("Pilot estimated cost does not match frozen pricing")
    if budget.hard_cost_limit_micros != hard_cost_limit:
        raise PilotPreflightError("Pilot hard cost limit must equal the token ceiling")

    return PilotPreflightReport(
        pilot_experiment_id=pilot_dataset.definition.experiment_id,
        formal_experiment_id=formal_dataset.definition.experiment_id,
        task_count=len(pilot_dataset.tasks),
        run_count=run_count,
        estimated_provider_requests=expected_request_count,
        max_provider_requests=maximum_request_count,
        currency=budget.currency,
        estimated_cost_micros=expected_cost,
        hard_cost_limit_micros=hard_cost_limit,
    )


def _require_distinct_identity(
    label: str,
    pilot_values: set[object],
    formal_values: set[object],
) -> None:
    if pilot_values & formal_values:
        raise PilotPreflightError(f"Pilot and formal {label} identities overlap")
