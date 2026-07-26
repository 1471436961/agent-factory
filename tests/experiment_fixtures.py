"""Experiment-only fixtures that must never be treated as freeze evidence."""

from __future__ import annotations

import json
from pathlib import Path

from agent_factory.domain.common import sha256_model
from experiments.artifacts import canonical_model_bytes
from experiments.contracts import FrozenExperimentManifest
from experiments.freezing import (
    calculate_freeze_manifest_checksum,
    load_freeze_candidate_spec,
)


def write_current_pilot_test_manifest(
    repository_root: Path,
    output_path: Path,
) -> Path:
    """Create valid launcher input without claiming a clean source freeze."""

    definition_root = (
        repository_root / "experiments" / "definitions" / "writer-pilot-v1"
    )
    archived_path = (
        repository_root
        / "experiments"
        / "evidence"
        / "writer-pilot-v1"
        / "freeze-manifest-openai-pre-switch.json"
    )
    archived = json.loads(archived_path.read_text(encoding="utf-8"))
    candidate = load_freeze_candidate_spec(definition_root / "freeze-candidate.json")
    archived.update(
        {
            "schema_version": candidate.schema_version,
            "purpose": candidate.purpose,
            "freeze_id": candidate.freeze_id,
            "experiment_id": candidate.experiment_id,
            "definition_checksum": candidate.definition_checksum,
            "execution_manifest": candidate.execution_manifest.model_dump(mode="json"),
            "analysis_config": candidate.analysis_config.model_dump(mode="json"),
            "analysis_config_checksum": sha256_model(candidate.analysis_config),
            "provider": candidate.provider.model_dump(mode="json"),
            "pricing": candidate.pricing.model_dump(mode="json"),
            "cost_budget": candidate.cost_budget.model_dump(mode="json"),
            "pilot_evidence": candidate.pilot_evidence,
            "created_at": candidate.created_at.isoformat(),
            "manifest_checksum": "0" * 64,
        }
    )
    unsigned = FrozenExperimentManifest.model_validate(archived)
    manifest = unsigned.model_copy(
        update={"manifest_checksum": calculate_freeze_manifest_checksum(unsigned)}
    )
    output_path.write_bytes(canonical_model_bytes(manifest))
    return output_path
