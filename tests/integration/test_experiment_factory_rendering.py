"""Prove Pilot FACTORY inputs originate from the real production chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.experiment_fixtures import write_current_pilot_test_manifest

from agent_factory.domain.enums import AuditEventType
from experiments.artifacts import ArtifactStore
from experiments.contracts import ExperimentCondition
from experiments.freezing import load_frozen_experiment_manifest
from experiments.loader import load_experiment_dataset
from experiments.pilot_launcher import (
    PilotFactoryPreparation,
    prepare_pilot_invocation_provider,
)
from experiments.planning import load_execution_plan
from experiments.rendering import validate_condition_pair

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-pilot-v1"


@pytest.mark.asyncio
async def test_pilot_preparer_persists_controller_specs_and_audit_chain(
    tmp_path: Path,
) -> None:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(DEFINITION_ROOT / "execution-plan.json", dataset)
    manifest_path = write_current_pilot_test_manifest(
        REPOSITORY_ROOT,
        tmp_path / "launch-freeze-manifest.json",
    )
    manifest = load_frozen_experiment_manifest(manifest_path)
    store = ArtifactStore(tmp_path / "runs")

    provider = await prepare_pilot_invocation_provider(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=store,
    )
    preparation_path = "_factory-preparation/writer-pilot-v1/preparation.json"
    preparation = store.read_model(preparation_path, PilotFactoryPreparation)

    assert len(preparation.records) == 2
    assert len(preparation.audit_events) == 12
    assert {event.event_type for event in preparation.audit_events} == {
        AuditEventType.PROTOTYPE_REGISTERED,
        AuditEventType.PROTOTYPE_PUBLISHED,
        AuditEventType.KNOWLEDGE_REGISTERED,
        AuditEventType.INSTANCE_CLONED,
        AuditEventType.KNOWLEDGE_BOUND,
        AuditEventType.SPEC_EXPORTED,
    }
    assert all(record.agent_spec.revision == 2 for record in preparation.records)
    assert all(not record.agent_spec.tools for record in preparation.records)

    for task in dataset.tasks:
        manual = provider.render(task, ExperimentCondition.MANUAL)
        factory = provider.render(task, ExperimentCondition.FACTORY)
        validate_condition_pair(manual, factory)
        assert factory.agent_spec_checksum is not None
        assert factory.knowledge_checksum == task.knowledge.checksum

    original_bytes = store.read_bytes(preparation_path)
    replay = await prepare_pilot_invocation_provider(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=store,
    )
    assert store.read_bytes(preparation_path) == original_bytes
    assert replay.render(
        dataset.tasks[0], ExperimentCondition.FACTORY
    ) == provider.render(dataset.tasks[0], ExperimentCondition.FACTORY)
