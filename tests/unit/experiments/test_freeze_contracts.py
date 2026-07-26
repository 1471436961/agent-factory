"""M5.5 freeze identity, pilot isolation, and exact-cost contract tests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import sha256_model
from experiments.contracts import (
    AnalysisConfig,
    CostBudget,
    ExecutionLimits,
    ExecutionManifest,
    ExperimentPurpose,
    FrozenArtifact,
    FrozenExperimentManifest,
    GenerationConfig,
    PilotEvidenceRef,
    PriceSnapshot,
    ProviderSnapshot,
    SourceSnapshot,
    calculate_conservative_cost_usd_micros,
)

NOW = datetime(2026, 7, 25, 16, 0, tzinfo=UTC)


def _pricing() -> PriceSnapshot:
    return PriceSnapshot(
        provider="openai",
        model="gpt-test-snapshot",
        input_usd_micros_per_unit=1_000_000,
        cached_input_usd_micros_per_unit=250_000,
        output_usd_micros_per_unit=2_000_000,
        source_url="https://example.com/provider-pricing",
        captured_at=NOW,
    )


def _execution_manifest() -> ExecutionManifest:
    return ExecutionManifest(
        experiment_id="writer-pilot-v1",
        dataset_checksum="1" * 64,
        plan_checksum="2" * 64,
        condition_bundle_checksum="3" * 64,
        generation=GenerationConfig(
            provider="openai",
            model="gpt-test-snapshot",
            sdk_version="2.46.0",
            temperature=0,
            max_output_tokens=500,
            request_timeout_seconds=30,
        ),
        limits=ExecutionLimits(
            max_provider_requests=24,
            max_prompt_tokens=10_000,
            max_completion_tokens=5_000,
            prompt_tokens_per_attempt_upper_bound=1_000,
        ),
        manifest_checksum="4" * 64,
    )


def _manifest() -> FrozenExperimentManifest:
    analysis = AnalysisConfig(bootstrap_seed=42, bootstrap_iterations=100)
    return FrozenExperimentManifest(
        purpose=ExperimentPurpose.PILOT,
        freeze_id="writer-pilot-freeze-v1",
        experiment_id="writer-pilot-v1",
        definition_checksum="5" * 64,
        candidate_spec_path="experiments/definitions/writer-pilot-v1/freeze-candidate.json",
        execution_manifest=_execution_manifest(),
        analysis_config=analysis,
        analysis_config_checksum=sha256_model(analysis),
        source=SourceSnapshot(
            source_commit="a" * 40,
            working_tree_clean=True,
            python_implementation="CPython",
            python_version="3.11.15",
            lockfile_checksum="6" * 64,
        ),
        provider=ProviderSnapshot(
            provider="openai",
            model="gpt-test-snapshot",
            api_name="responses-api",
            sdk_name="openai",
            sdk_version="2.46.0",
            model_is_immutable_snapshot=True,
        ),
        pricing=_pricing(),
        cost_budget=CostBudget(
            estimated_provider_requests=8,
            estimated_prompt_tokens=4_000,
            estimated_completion_tokens=2_000,
            estimated_cost_usd_micros=8_000,
            hard_cost_limit_usd_micros=15_000,
        ),
        files=(
            FrozenArtifact(
                path="experiments/definitions/writer-pilot-v1/dataset.yaml",
                byte_size=100,
                content_checksum="7" * 64,
            ),
            FrozenArtifact(
                path=("experiments/definitions/writer-pilot-v1/freeze-candidate.json"),
                byte_size=150,
                content_checksum="8" * 64,
            ),
            FrozenArtifact(
                path="uv.lock",
                byte_size=200,
                content_checksum="6" * 64,
            ),
        ),
        created_at=NOW,
        manifest_checksum="8" * 64,
    )


def _payload() -> dict[str, object]:
    return _manifest().model_dump(mode="python", exclude_none=False)


def _mapping(value: object) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value))


def test_pilot_manifest_binds_sources_files_and_exact_cost() -> None:
    manifest = _manifest()

    assert manifest.purpose is ExperimentPurpose.PILOT
    assert manifest.pilot_evidence is None
    assert manifest.cost_budget.estimated_cost_usd_micros == (
        calculate_conservative_cost_usd_micros(
            input_tokens=4_000,
            output_tokens=2_000,
            pricing=manifest.pricing,
        )
    )
    assert (
        calculate_conservative_cost_usd_micros(
            input_tokens=0,
            output_tokens=0,
            pricing=manifest.pricing,
        )
        == 0
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_conservative_cost_usd_micros(
            input_tokens=-1,
            output_tokens=0,
            pricing=manifest.pricing,
        )


def test_money_fields_reject_float_and_limit_below_estimate() -> None:
    payload = _manifest().cost_budget.model_dump(mode="python")
    payload["estimated_cost_usd_micros"] = 8_000.0
    with pytest.raises(ValidationError, match="valid integer"):
        CostBudget.model_validate(payload)

    payload["estimated_cost_usd_micros"] = 8_000
    payload["hard_cost_limit_usd_micros"] = 7_999
    with pytest.raises(ValidationError, match="below estimated"):
        CostBudget.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("experiment_id", "other-pilot-v1", "source identities"),
        ("analysis_config_checksum", "9" * 64, "source identities"),
    ],
)
def test_manifest_rejects_top_level_source_mismatch(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        FrozenExperimentManifest.model_validate(payload)


def test_manifest_rejects_provider_and_lockfile_mismatch() -> None:
    provider_payload = _payload()
    provider = _mapping(provider_payload["provider"])
    provider["model"] = "another-model"
    provider_payload["provider"] = provider
    with pytest.raises(ValidationError, match="source identities"):
        FrozenExperimentManifest.model_validate(provider_payload)

    lock_payload = _payload()
    source = _mapping(lock_payload["source"])
    source["lockfile_checksum"] = "9" * 64
    lock_payload["source"] = source
    with pytest.raises(ValidationError, match="declared lockfile"):
        FrozenExperimentManifest.model_validate(lock_payload)


def test_manifest_rejects_unsorted_or_duplicate_file_inventory() -> None:
    payload = _payload()
    files = list(cast(tuple[object, ...], payload["files"]))
    payload["files"] = tuple(reversed(files))
    with pytest.raises(ValidationError, match="unique and sorted"):
        FrozenExperimentManifest.model_validate(payload)

    payload["files"] = (files[0], files[0], files[1])
    with pytest.raises(ValidationError, match="unique and sorted"):
        FrozenExperimentManifest.model_validate(payload)


def test_manifest_recomputes_estimate_and_bounds_hard_cost() -> None:
    estimate_payload = _payload()
    budget = _mapping(estimate_payload["cost_budget"])
    budget["estimated_cost_usd_micros"] = 8_001
    estimate_payload["cost_budget"] = budget
    with pytest.raises(ValidationError, match="does not match tokens"):
        FrozenExperimentManifest.model_validate(estimate_payload)

    usage_payload = _payload()
    budget = _mapping(usage_payload["cost_budget"])
    budget["estimated_prompt_tokens"] = 10_001
    usage_payload["cost_budget"] = budget
    with pytest.raises(ValidationError, match="exceeds technical"):
        FrozenExperimentManifest.model_validate(usage_payload)

    ceiling_payload = _payload()
    budget = _mapping(ceiling_payload["cost_budget"])
    budget["hard_cost_limit_usd_micros"] = 20_001
    ceiling_payload["cost_budget"] = budget
    with pytest.raises(ValidationError, match="token-bound"):
        FrozenExperimentManifest.model_validate(ceiling_payload)


def test_formal_manifest_requires_distinct_pilot_evidence() -> None:
    payload = _payload()
    payload["purpose"] = ExperimentPurpose.FORMAL
    with pytest.raises(ValidationError, match="requires pilot evidence"):
        FrozenExperimentManifest.model_validate(payload)

    evidence = PilotEvidenceRef(
        experiment_id="writer-pilot-v1",
        freeze_manifest_checksum="a" * 64,
        report_checksum="b" * 64,
    )
    payload["pilot_evidence"] = evidence
    with pytest.raises(ValidationError, match="IDs must differ"):
        FrozenExperimentManifest.model_validate(payload)

    payload["experiment_id"] = "writer-formal-v1"
    execution = _mapping(payload["execution_manifest"])
    execution["experiment_id"] = "writer-formal-v1"
    payload["execution_manifest"] = execution
    formal = FrozenExperimentManifest.model_validate(payload)
    assert formal.purpose is ExperimentPurpose.FORMAL
    assert formal.pilot_evidence == evidence

    pilot_payload = _payload()
    pilot_payload["pilot_evidence"] = evidence
    with pytest.raises(ValidationError, match="cannot reference"):
        FrozenExperimentManifest.model_validate(pilot_payload)


def test_source_snapshot_requires_clean_cpython_environment() -> None:
    payload = _manifest().source.model_dump(mode="python")
    payload["working_tree_clean"] = False

    with pytest.raises(ValidationError, match="Input should be True"):
        SourceSnapshot.model_validate(payload)
