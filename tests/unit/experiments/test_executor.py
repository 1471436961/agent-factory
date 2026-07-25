"""Retry, budget, immutable replay, and interruption recovery tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.models import AgentSpec, KnowledgeRef, PrototypeRef
from agent_factory.domain.services.spec import checksum_agent_spec
from experiments.artifacts import ArtifactConflictError, ArtifactStore
from experiments.contracts import (
    ExecutionLimits,
    ExperimentCondition,
    ExperimentTask,
    GenerationConfig,
    RenderedInvocation,
    RunStatus,
)
from experiments.executor import (
    ExecutionAbortedError,
    ExperimentExecutor,
)
from experiments.gateway import (
    FakeExperimentGateway,
    GatewayFailure,
    GatewayFailureKind,
    GatewayRequest,
)
from experiments.loader import LoadedExperimentDataset
from experiments.planning import build_execution_manifest, build_execution_plan
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
    render_factory_invocation,
    render_manual_invocation,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANUAL_PROMPT_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "definitions"
    / "writer-v1"
    / "conditions"
    / "manual-system.txt"
)


@dataclass(slots=True)
class TickingClock:
    current: datetime = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


class PreparedInvocationProvider:
    def __init__(self, dataset: LoadedExperimentDataset) -> None:
        self._dataset = dataset
        self._manual_prompt, self.prompt_bytes = load_manual_system_prompt(
            MANUAL_PROMPT_PATH
        )

    def render(
        self,
        task: ExperimentTask,
        condition: ExperimentCondition,
    ) -> RenderedInvocation:
        knowledge = self._dataset.knowledge_bytes[
            (task.knowledge.knowledge_id, task.knowledge.version)
        ]
        if condition is ExperimentCondition.MANUAL:
            return render_manual_invocation(
                task=task,
                knowledge_bytes=knowledge,
                manual_system_prompt=self._manual_prompt,
            )
        return render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge,
            agent_spec=_agent_spec(task),
        )


class InterruptingGateway:
    is_live = False

    def __init__(self) -> None:
        self.calls: list[GatewayRequest] = []

    async def generate(self, request: GatewayRequest) -> object:
        self.calls.append(request)
        raise RuntimeError("injected gateway interruption")


class LiveFakeGateway(FakeExperimentGateway):
    is_live = True


def _agent_spec(task: ExperimentTask) -> AgentSpec:
    unsigned = AgentSpec(
        instance_id=UUID("50000000-0000-0000-0000-000000000001"),
        revision=2,
        prototype=PrototypeRef(
            prototype_id="writer-prototype",
            version="1.0.0",
            checksum="a" * 64,
        ),
        agent_type="writer-agent",
        role="Technical Writer",
        system_prompt="Produce accurate documentation from supplied knowledge.",
        tools=(),
        knowledge=(
            KnowledgeRef(
                slot_name="domain-knowledge",
                knowledge_id=task.knowledge.knowledge_id,
                version=task.knowledge.version,
                checksum=task.knowledge.checksum,
                injection_mode=InjectionMode.INLINE,
            ),
        ),
        output_schema=task.output_schema,
        generated_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        spec_checksum="0" * 64,
    )
    return unsigned.model_copy(update={"spec_checksum": checksum_agent_spec(unsigned)})


def _generation() -> GenerationConfig:
    return GenerationConfig(
        provider="fake-provider",
        model="fake-writer-v1",
        sdk_version="0.0.0",
        temperature=0,
        max_output_tokens=512,
        request_timeout_seconds=30,
    )


def _limits(
    *,
    requests: int = 720,
    prompt_tokens: int = 3_000_000,
    completion_tokens: int = 1_000_000,
) -> ExecutionLimits:
    return ExecutionLimits(
        max_provider_requests=requests,
        max_prompt_tokens=prompt_tokens,
        max_completion_tokens=completion_tokens,
        prompt_tokens_per_attempt_upper_bound=4_000,
    )


def _executor(
    dataset: LoadedExperimentDataset,
    root: Path,
    gateway: object,
    *,
    limits: ExecutionLimits | None = None,
    clock: TickingClock | None = None,
    sleeper: RecordingSleeper | None = None,
) -> tuple[ExperimentExecutor, PreparedInvocationProvider]:
    plan = build_execution_plan(dataset)
    provider = PreparedInvocationProvider(dataset)
    manifest = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(
            provider.prompt_bytes
        ),
        generation=_generation(),
        limits=limits or _limits(),
    )
    return (
        ExperimentExecutor(
            dataset=dataset,
            plan=plan,
            manifest=manifest,
            invocation_provider=provider,
            gateway=gateway,  # type: ignore[arg-type]
            store=ArtifactStore(root),
            clock=clock or TickingClock(),
            sleeper=sleeper or RecordingSleeper(),
        ),
        provider,
    )


@pytest.mark.asyncio
async def test_full_plan_completes_once_and_replays_without_gateway_calls(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first_gateway = FakeExperimentGateway()
    first, _provider = _executor(dataset, root, first_gateway)

    runs = await first.execute()

    assert len(runs) == 240
    assert len(first_gateway.calls) == 240
    assert {run.status for run in runs} == {RunStatus.SUCCEEDED}
    assert len({run.run_id for run in runs}) == 240

    replay_gateway = FakeExperimentGateway()
    replay, _provider = _executor(dataset, root, replay_gateway)
    replayed = await replay.execute()

    assert replayed == runs
    assert replay_gateway.calls == ()


@pytest.mark.asyncio
async def test_retry_matrix_preserves_every_attempt(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    plan = build_execution_plan(dataset)
    items = plan.items[:5]
    script = {
        (items[0].run_id, 1): GatewayFailure(
            kind=GatewayFailureKind.NETWORK,
            error_code="NETWORK_ERROR",
        ),
        (items[1].run_id, 1): GatewayFailure(
            kind=GatewayFailureKind.RATE_LIMITED,
            error_code="RATE_LIMITED",
        ),
        (items[1].run_id, 2): GatewayFailure(
            kind=GatewayFailureKind.SERVER_ERROR,
            error_code="PROVIDER_503",
        ),
        (items[2].run_id, 1): GatewayFailure(
            kind=GatewayFailureKind.CLIENT_ERROR,
            error_code="INVALID_REQUEST",
        ),
        (items[3].run_id, 1): GatewayFailure(
            kind=GatewayFailureKind.FILTERED,
            error_code="CONTENT_FILTERED",
        ),
        (items[4].run_id, 1): GatewayFailure(
            kind=GatewayFailureKind.INVALID_RESPONSE,
            error_code="INVALID_RESPONSE",
            raw_response={"unparsed": "provider output"},
        ),
    }
    sleeper = RecordingSleeper()
    gateway = FakeExperimentGateway(script)
    executor, _provider = _executor(
        dataset,
        tmp_path / "runs",
        gateway,
        sleeper=sleeper,
    )

    runs = await executor.execute(max_items=5)

    assert [len(run.attempts) for run in runs] == [2, 3, 1, 1, 1]
    assert [run.status for run in runs] == [
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
        RunStatus.PROVIDER_FAILED,
        RunStatus.FILTERED,
        RunStatus.INVALID_RESPONSE,
    ]
    assert sleeper.delays == [1.0, 1.0, 2.0]
    assert runs[4].attempts[0].error_response == {"unparsed": "provider output"}


@pytest.mark.asyncio
async def test_interrupted_inflight_attempt_becomes_unknown_then_retries(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    interrupted_gateway = InterruptingGateway()
    interrupted, _provider = _executor(dataset, root, interrupted_gateway)
    with pytest.raises(RuntimeError, match="injected gateway interruption"):
        await interrupted.execute(max_items=1)

    recovery_gateway = FakeExperimentGateway()
    recovered, _provider = _executor(dataset, root, recovery_gateway)
    runs = await recovered.execute(max_items=1)

    assert len(interrupted_gateway.calls) == 1
    assert len(recovery_gateway.calls) == 1
    assert len(runs[0].attempts) == 2
    assert runs[0].attempts[0].error_code == "RESULT_UNKNOWN_AFTER_INTERRUPTION"
    assert runs[0].attempts[1].status.value == "succeeded"


@pytest.mark.asyncio
async def test_budget_is_reserved_before_calls_and_remaining_runs_are_explicit(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    gateway = FakeExperimentGateway()
    executor, _provider = _executor(
        dataset,
        tmp_path / "runs",
        gateway,
        limits=_limits(requests=1, prompt_tokens=4_000, completion_tokens=512),
    )

    runs = await executor.execute(max_items=3)

    assert len(gateway.calls) == 1
    assert [run.status for run in runs] == [
        RunStatus.SUCCEEDED,
        RunStatus.BUDGET_STOPPED,
        RunStatus.BUDGET_STOPPED,
    ]
    assert runs[1].attempts == ()


@pytest.mark.asyncio
async def test_live_gateway_is_blocked_without_explicit_approval(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    gateway = LiveFakeGateway()
    executor, _provider = _executor(dataset, tmp_path / "runs", gateway)

    with pytest.raises(ExecutionAbortedError, match="explicit approval"):
        await executor.execute(max_items=1)
    assert gateway.calls == ()
    assert list((tmp_path / "runs").rglob("*.json")) == []


@pytest.mark.asyncio
async def test_gateway_credentials_never_enter_artifacts(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    secret = "sk-test-secret-that-must-not-be-written"
    gateway = FakeExperimentGateway()
    gateway.api_key = secret  # type: ignore[attr-defined]
    root = tmp_path / "runs"
    executor, _provider = _executor(dataset, root, gateway)

    await executor.execute(max_items=2)

    for artifact in root.rglob("*.json"):
        assert secret.encode("utf-8") not in artifact.read_bytes()


@pytest.mark.asyncio
async def test_executor_rejects_invalid_partial_selection(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    executor, _provider = _executor(
        dataset,
        tmp_path / "runs",
        FakeExperimentGateway(),
    )
    with pytest.raises(ValueError, match="max_items"):
        await executor.execute(max_items=0)


@pytest.mark.asyncio
async def test_recovery_rejects_changed_execution_manifest(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first, _provider = _executor(dataset, root, FakeExperimentGateway())
    await first.execute(max_items=1)

    changed, _provider = _executor(
        dataset,
        root,
        FakeExperimentGateway(),
        limits=_limits(requests=719),
    )
    with pytest.raises(ArtifactConflictError, match="other bytes"):
        await changed.execute(max_items=1)


@pytest.mark.asyncio
async def test_terminal_without_request_is_rejected_as_incomplete_evidence(
    dataset: LoadedExperimentDataset,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first, _provider = _executor(dataset, root, FakeExperimentGateway())
    runs = await first.execute(max_items=1)
    request_path = root / "writer-validation-v1" / "requests" / f"{runs[0].run_id}.json"
    request_path.unlink()

    recovered, _provider = _executor(dataset, root, FakeExperimentGateway())
    with pytest.raises(ExecutionAbortedError, match="missing its request"):
        await recovered.execute(max_items=1)
