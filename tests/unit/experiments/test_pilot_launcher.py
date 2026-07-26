"""Controlled Pilot launcher tests; every provider is local and deterministic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from experiments.artifacts import ArtifactStore
from experiments.contracts import ExecutionPlan, FrozenExperimentManifest
from experiments.executor import InvocationProvider
from experiments.freezing import FreezeError, load_frozen_experiment_manifest
from experiments.gateway import (
    FakeExperimentGateway,
    GatewayOutcome,
    GatewayRequest,
)
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset
from experiments.pilot_launcher import (
    ManagedExperimentGateway,
    PilotLauncherDependencies,
    PilotLaunchError,
    PilotLaunchRequest,
    prepare_pilot_invocation_provider,
    run_live_pilot,
    validate_live_output_root,
)
from experiments.planning import load_execution_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PILOT_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-pilot-v1"
FORMAL_ROOT = REPOSITORY_ROOT / "experiments" / "definitions" / "writer-v1"
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "evidence"
    / "writer-pilot-v1"
    / "freeze-manifest.json"
)
SECRET = "sk-test-must-never-enter-artifacts"


class _NoOpFreezeVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, manifest: FrozenExperimentManifest, **kwargs: object) -> None:
        assert kwargs["verify_environment"] is True
        self.calls += 1


class _RejectingFreezeVerifier:
    def __call__(self, manifest: FrozenExperimentManifest, **kwargs: object) -> None:
        raise PilotLaunchError("synthetic freeze rejection")


@dataclass(slots=True)
class _RecordingKeySource:
    value: str | None = SECRET
    reads: int = 0

    def read(self) -> str | None:
        self.reads += 1
        return self.value


class _LiveFakeGateway(FakeExperimentGateway):
    is_live = True

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.closed = 0
        self._fail = fail

    async def generate(self, request: GatewayRequest) -> GatewayOutcome:
        if self._fail:
            raise RuntimeError("synthetic provider interruption")
        return await super().generate(request)

    async def close(self) -> None:
        self.closed += 1


@dataclass(slots=True)
class _RecordingGatewayFactory:
    fail: bool = False
    keys: list[str] = field(default_factory=list)
    gateways: list[_LiveFakeGateway] = field(default_factory=list)

    def __call__(self, *, api_key: str) -> ManagedExperimentGateway:
        self.keys.append(api_key)
        gateway = _LiveFakeGateway(fail=self.fail)
        self.gateways.append(gateway)
        return gateway


class _RaisingGatewayFactory:
    def __call__(self, *, api_key: str) -> ManagedExperimentGateway:
        raise RuntimeError(f"client creation failed with {api_key}")


class _CloseFailGateway(_LiveFakeGateway):
    async def close(self) -> None:
        raise RuntimeError("sensitive close failure")


class _CloseFailGatewayFactory:
    def __call__(self, *, api_key: str) -> ManagedExperimentGateway:
        return _CloseFailGateway()


@dataclass(frozen=True, slots=True)
class _StaticInvocationPreparer:
    provider: InvocationProvider

    async def __call__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        manifest: FrozenExperimentManifest,
        store: ArtifactStore,
    ) -> InvocationProvider:
        return self.provider


def _request(
    output_root: Path,
    *,
    allow_live: bool = True,
    confirmed_experiment_id: str = "writer-pilot-v1",
    confirmed_hard_cost_usd_micros: int = 51_815,
) -> PilotLaunchRequest:
    return PilotLaunchRequest(
        definition_root=PILOT_ROOT,
        plan_path=PILOT_ROOT / "execution-plan.json",
        manifest_path=MANIFEST_PATH,
        formal_definition_root=FORMAL_ROOT,
        formal_plan_path=FORMAL_ROOT / "execution-plan.json",
        output_root=output_root,
        allow_live=allow_live,
        confirmed_experiment_id=confirmed_experiment_id,
        confirmed_hard_cost_usd_micros=confirmed_hard_cost_usd_micros,
    )


@pytest.mark.asyncio
async def test_live_pilot_executes_all_coordinates_and_resumes_without_calls(
    tmp_path: Path,
) -> None:
    verifier = _NoOpFreezeVerifier()
    key_source = _RecordingKeySource()
    gateway_factory = _RecordingGatewayFactory()
    dependencies = PilotLauncherDependencies(
        freeze_verifier=verifier,
        api_key_source=key_source,
        gateway_factory=gateway_factory,
    )
    output_root = tmp_path / "pilot-runs"

    first = await run_live_pilot(
        _request(output_root),
        repository_root=REPOSITORY_ROOT,
        dependencies=dependencies,
    )
    second = await run_live_pilot(
        _request(output_root),
        repository_root=REPOSITORY_ROOT,
        dependencies=dependencies,
    )

    assert first == second
    assert first.run_count == 8
    assert dict(first.status_counts) == {"succeeded": 8}
    assert first.provider_attempts == 8
    assert first.observed_prompt_tokens == 800
    assert first.observed_completion_tokens == 320
    assert first.observed_cost_usd_micros == 832
    assert first.max_provider_requests == 16
    assert first.hard_cost_limit_usd_micros == 51_815
    assert len(gateway_factory.gateways[0].calls) == 8
    assert len(gateway_factory.gateways[1].calls) == 0
    assert [gateway.closed for gateway in gateway_factory.gateways] == [1, 1]
    assert key_source.reads == 2
    assert verifier.calls == 2
    assert len(list((output_root / "writer-pilot-v1" / "terminal").glob("*.json"))) == 8
    preparation = (
        output_root / "_factory-preparation" / "writer-pilot-v1" / "preparation.json"
    )
    assert preparation.is_file()
    assert SECRET.encode() not in b"".join(
        path.read_bytes() for path in output_root.rglob("*.json")
    )


@pytest.mark.asyncio
async def test_freeze_rejection_happens_before_preparation_key_or_gateway(
    tmp_path: Path,
) -> None:
    key_source = _RecordingKeySource()
    gateway_factory = _RecordingGatewayFactory()
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_RejectingFreezeVerifier(),
        api_key_source=key_source,
        gateway_factory=gateway_factory,
    )

    with pytest.raises(PilotLaunchError, match="freeze rejection"):
        await run_live_pilot(
            _request(tmp_path / "runs"),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert key_source.reads == 0
    assert gateway_factory.gateways == []
    assert not (tmp_path / "runs").exists()


@pytest.mark.asyncio
async def test_historical_manifest_is_rejected_before_key_read(tmp_path: Path) -> None:
    key_source = _RecordingKeySource()
    dependencies = PilotLauncherDependencies(
        api_key_source=key_source,
        gateway_factory=_RecordingGatewayFactory(),
    )

    with pytest.raises(FreezeError):
        await run_live_pilot(
            _request(tmp_path / "runs"),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert key_source.reads == 0
    assert not (tmp_path / "runs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allow_live", "confirmed_experiment_id", "confirmed_cost"),
    [
        (False, "writer-pilot-v1", 51_815),
        (True, "other-pilot", 51_815),
        (True, "writer-pilot-v1", 51_814),
    ],
)
async def test_approval_mismatch_fails_before_key_and_output(
    tmp_path: Path,
    allow_live: bool,
    confirmed_experiment_id: str,
    confirmed_cost: int,
) -> None:
    key_source = _RecordingKeySource()
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_NoOpFreezeVerifier(),
        api_key_source=key_source,
        gateway_factory=_RecordingGatewayFactory(),
    )
    output_root = tmp_path / "runs"

    with pytest.raises(PilotLaunchError):
        await run_live_pilot(
            _request(
                output_root,
                allow_live=allow_live,
                confirmed_experiment_id=confirmed_experiment_id,
                confirmed_hard_cost_usd_micros=confirmed_cost,
            ),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert key_source.reads == 0
    assert not output_root.exists()


@pytest.mark.asyncio
async def test_missing_key_stops_after_factory_preparation(tmp_path: Path) -> None:
    key_source = _RecordingKeySource(value=None)
    gateway_factory = _RecordingGatewayFactory()
    output_root = tmp_path / "runs"
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_NoOpFreezeVerifier(),
        api_key_source=key_source,
        gateway_factory=gateway_factory,
    )

    with pytest.raises(PilotLaunchError, match="OPENAI_API_KEY"):
        await run_live_pilot(
            _request(output_root),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert key_source.reads == 1
    assert gateway_factory.gateways == []
    assert (
        output_root / "_factory-preparation" / "writer-pilot-v1" / "preparation.json"
    ).is_file()
    assert not (output_root / "writer-pilot-v1").exists()


@pytest.mark.asyncio
async def test_gateway_is_closed_when_execution_is_interrupted(tmp_path: Path) -> None:
    dataset = load_experiment_dataset(PILOT_ROOT)
    plan = load_execution_plan(PILOT_ROOT / "execution-plan.json", dataset)
    manifest = load_frozen_experiment_manifest(MANIFEST_PATH)
    provider = await prepare_pilot_invocation_provider(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=ArtifactStore(tmp_path / "prepared"),
    )

    gateway_factory = _RecordingGatewayFactory(fail=True)
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_NoOpFreezeVerifier(),
        invocation_preparer=_StaticInvocationPreparer(provider),
        api_key_source=_RecordingKeySource(),
        gateway_factory=gateway_factory,
    )

    with pytest.raises(RuntimeError, match="synthetic provider interruption"):
        await run_live_pilot(
            _request(tmp_path / "interrupted"),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert gateway_factory.gateways[0].closed == 1


@pytest.mark.asyncio
async def test_client_creation_failure_is_normalized_without_secret(
    tmp_path: Path,
) -> None:
    dataset = load_experiment_dataset(PILOT_ROOT)
    plan = load_execution_plan(PILOT_ROOT / "execution-plan.json", dataset)
    manifest = load_frozen_experiment_manifest(MANIFEST_PATH)
    provider = await prepare_pilot_invocation_provider(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=ArtifactStore(tmp_path / "prepared"),
    )
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_NoOpFreezeVerifier(),
        invocation_preparer=_StaticInvocationPreparer(provider),
        api_key_source=_RecordingKeySource(),
        gateway_factory=_RaisingGatewayFactory(),
    )

    with pytest.raises(PilotLaunchError) as captured:
        await run_live_pilot(
            _request(tmp_path / "creation-failure"),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert str(captured.value) == "OpenAI experiment client cannot be created"
    assert SECRET not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_client_close_failure_is_normalized_after_evidence_is_saved(
    tmp_path: Path,
) -> None:
    dataset = load_experiment_dataset(PILOT_ROOT)
    plan = load_execution_plan(PILOT_ROOT / "execution-plan.json", dataset)
    manifest = load_frozen_experiment_manifest(MANIFEST_PATH)
    provider = await prepare_pilot_invocation_provider(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=ArtifactStore(tmp_path / "prepared"),
    )
    output_root = tmp_path / "close-failure"
    dependencies = PilotLauncherDependencies(
        freeze_verifier=_NoOpFreezeVerifier(),
        invocation_preparer=_StaticInvocationPreparer(provider),
        api_key_source=_RecordingKeySource(),
        gateway_factory=_CloseFailGatewayFactory(),
    )

    with pytest.raises(PilotLaunchError, match="cannot be closed"):
        await run_live_pilot(
            _request(output_root),
            repository_root=REPOSITORY_ROOT,
            dependencies=dependencies,
        )

    assert len(list((output_root / "writer-pilot-v1" / "terminal").glob("*.json"))) == 8


def test_live_output_rejects_tracked_tree_and_symbolic_links(tmp_path: Path) -> None:
    with pytest.raises(PilotLaunchError, match=r"below \.tmp"):
        validate_live_output_root(
            REPOSITORY_ROOT / "docs" / "pilot-runs",
            REPOSITORY_ROOT,
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable for this Windows account")
    with pytest.raises(PilotLaunchError, match="symbolic links"):
        validate_live_output_root(link / "runs", REPOSITORY_ROOT)


def test_live_cli_has_no_credential_or_partial_plan_argument() -> None:
    from experiments.cli import build_parser

    args = build_parser().parse_args(
        [
            "run-pilot-live",
            "--manifest",
            "manifest.json",
            "--output-root",
            ".tmp/pilot",
            "--allow-live",
            "--confirm-experiment-id",
            "writer-pilot-v1",
            "--confirm-hard-cost-usd-micros",
            "51815",
        ]
    )
    assert not hasattr(args, "api_key")
    assert not hasattr(args, "max_items")
    assert args.allow_live is True
    assert args.confirm_experiment_id == "writer-pilot-v1"
    assert args.confirm_hard_cost_usd_micros == 51_815
