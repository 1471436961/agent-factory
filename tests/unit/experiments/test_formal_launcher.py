"""Formal live launcher gates and shared execution path tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_factory.domain.common import canonical_json_bytes
from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.cli import build_parser
from experiments.contracts import (
    ExecutionLimits,
    ExperimentCondition,
    ExperimentPurpose,
    ExperimentTask,
    FrozenExperimentManifest,
    PilotEvidenceRef,
    RenderedInvocation,
)
from experiments.freezing import (
    calculate_freeze_manifest_checksum,
    load_frozen_experiment_manifest,
)
from experiments.gateway import FakeExperimentGateway
from experiments.loader import load_experiment_dataset
from experiments.pilot_launcher import (
    FormalLaunchRequest,
    PilotLauncherDependencies,
    PilotLaunchError,
    run_live_formal,
)
from experiments.planning import build_execution_manifest, load_execution_plan
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFINITION_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
PLAN_PATH = DEFINITION_ROOT / "execution-plan.json"
PILOT_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "evidence"
    / "writer-pilot-v1"
    / "freeze-manifest.json"
)


class _FormalInvocationProvider:
    def render(
        self,
        task: ExperimentTask,
        condition: ExperimentCondition,
    ) -> RenderedInvocation:
        output_schema = (
            None if condition is ExperimentCondition.MANUAL else task.output_schema
        )
        instructions = "Blind formal test instructions"
        task_input = f"Shared visible input for {task.task_id}"
        visible = {
            "instructions": instructions,
            "task_input": task_input,
            "output_schema": output_schema,
        }
        return RenderedInvocation(
            renderer_version="1.0",
            condition=condition,
            task_id=task.task_id,
            instructions=instructions,
            task_input=task_input,
            output_schema=output_schema,
            knowledge_checksum=task.knowledge.checksum,
            agent_spec_checksum=(
                None if condition is ExperimentCondition.MANUAL else "a" * 64
            ),
            prompt_hash=hashlib.sha256(canonical_json_bytes(visible)).hexdigest(),
        )


class _LiveFakeGateway(FakeExperimentGateway):
    is_live = True

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


@dataclass(slots=True)
class _KeySource:
    value: str = "test-key"
    reads: int = 0

    def read(self) -> str:
        self.reads += 1
        return self.value


@dataclass(slots=True)
class _GatewayFactory:
    gateways: list[_LiveFakeGateway] = field(default_factory=list)

    def __call__(self, *, api_key: str) -> _LiveFakeGateway:
        assert api_key == "test-key"
        gateway = _LiveFakeGateway()
        self.gateways.append(gateway)
        return gateway


async def _prepare(**_: object) -> _FormalInvocationProvider:
    return _FormalInvocationProvider()


def _ignore_freeze(*_: object, **__: object) -> None:
    return None


def _write_formal_manifest(path: Path) -> FrozenExperimentManifest:
    dataset = load_experiment_dataset(DEFINITION_ROOT)
    plan = load_execution_plan(PLAN_PATH, dataset)
    pilot = load_frozen_experiment_manifest(PILOT_MANIFEST_PATH)
    _, prompt_bytes = load_manual_system_prompt(
        DEFINITION_ROOT / "conditions" / "manual-system.txt"
    )
    generation = pilot.execution_manifest.generation
    max_requests = len(plan.items) * generation.max_attempts
    execution = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(prompt_bytes),
        generation=generation,
        limits=ExecutionLimits(
            max_provider_requests=max_requests,
            max_prompt_tokens=max_requests * 4_000,
            max_completion_tokens=max_requests * generation.max_output_tokens,
            prompt_tokens_per_attempt_upper_bound=4_000,
        ),
    )
    evidence = PilotEvidenceRef(
        experiment_id=pilot.experiment_id,
        freeze_manifest_path=(
            "experiments/evidence/writer-pilot-v1/freeze-manifest.json"
        ),
        freeze_manifest_checksum=pilot.manifest_checksum,
        report_path="docs/reports/m5.5-moonshot-pilot-review.md",
        report_checksum="a" * 64,
        evidence_seal_path=(
            "experiments/evidence/writer-pilot-v1/evidence-seal-mfjs-20260728.json"
        ),
        evidence_seal_checksum="b" * 64,
    )
    unsigned = pilot.model_copy(
        update={
            "purpose": ExperimentPurpose.FORMAL,
            "freeze_id": "writer-validation-test-freeze",
            "experiment_id": dataset.definition.experiment_id,
            "definition_checksum": plan.definition_checksum,
            "execution_manifest": execution,
            "pilot_evidence": evidence,
            "manifest_checksum": "0" * 64,
        }
    )
    manifest = unsigned.model_copy(
        update={"manifest_checksum": calculate_freeze_manifest_checksum(unsigned)}
    )
    path.write_bytes(canonical_model_bytes(manifest))
    return manifest


def _request(
    manifest_path: Path,
    output_root: Path,
    manifest: FrozenExperimentManifest,
    *,
    allow_live: bool,
) -> FormalLaunchRequest:
    return FormalLaunchRequest(
        definition_root=DEFINITION_ROOT,
        plan_path=PLAN_PATH,
        manifest_path=manifest_path,
        output_root=output_root,
        allow_live=allow_live,
        confirmed_experiment_id=manifest.experiment_id,
        confirmed_currency=manifest.cost_budget.currency,
        confirmed_hard_cost_micros=manifest.cost_budget.hard_cost_limit_micros,
    )


def test_formal_cli_exposes_only_full_plan_and_explicit_approval() -> None:
    args = build_parser().parse_args(
        [
            "run-formal-live",
            "--manifest",
            "formal.json",
            "--output-root",
            "evidence",
            "--allow-live",
            "--confirm-experiment-id",
            "writer-validation-v1",
            "--confirm-currency",
            "CNY",
            "--confirm-hard-cost-micros",
            "25751040",
        ]
    )

    assert args.command == "run-formal-live"
    assert args.allow_live is True
    assert not hasattr(args, "max_items")
    assert not hasattr(args, "api_key")


@pytest.mark.asyncio
async def test_formal_launcher_requires_live_approval_before_key_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import pilot_launcher

    manifest_path = tmp_path / "formal-manifest.json"
    manifest = _write_formal_manifest(manifest_path)
    key_source = _KeySource()
    monkeypatch.setattr(
        pilot_launcher, "load_freeze_candidate_spec", lambda _: object()
    )
    monkeypatch.setattr(pilot_launcher, "validate_formal_preflight", lambda **_: None)

    with pytest.raises(PilotLaunchError, match="requires --allow-live"):
        await run_live_formal(
            _request(manifest_path, tmp_path / "runs", manifest, allow_live=False),
            repository_root=REPOSITORY_ROOT,
            dependencies=PilotLauncherDependencies(
                freeze_verifier=_ignore_freeze,
                invocation_preparer=_prepare,
                api_key_source=key_source,
                gateway_factory=_GatewayFactory(),
            ),
        )

    assert key_source.reads == 0
    assert not (tmp_path / "runs").exists()


@pytest.mark.asyncio
async def test_formal_launcher_executes_the_complete_plan_with_fake_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import pilot_launcher

    manifest_path = tmp_path / "formal-manifest.json"
    manifest = _write_formal_manifest(manifest_path)
    gateway_factory = _GatewayFactory()
    monkeypatch.setattr(
        pilot_launcher, "load_freeze_candidate_spec", lambda _: object()
    )
    monkeypatch.setattr(pilot_launcher, "validate_formal_preflight", lambda **_: None)

    output_root = tmp_path / "runs"
    summary = await run_live_formal(
        _request(manifest_path, output_root, manifest, allow_live=True),
        repository_root=REPOSITORY_ROOT,
        dependencies=PilotLauncherDependencies(
            freeze_verifier=_ignore_freeze,
            invocation_preparer=_prepare,
            api_key_source=_KeySource(),
            gateway_factory=gateway_factory,
        ),
    )

    assert summary.run_count == 240
    assert summary.status_counts == {"succeeded": 240}
    assert summary.provider_attempts == 240
    assert len(gateway_factory.gateways[0].calls) == 240
    assert gateway_factory.gateways[0].closed == 1
    assert len(ArtifactStore(output_root).list_files(manifest.experiment_id)) == 961
