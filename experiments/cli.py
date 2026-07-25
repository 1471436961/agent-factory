"""Offline-only command line entry points for M5.3 infrastructure."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.models import AgentSpec, KnowledgeRef, PrototypeRef
from agent_factory.domain.services.spec import checksum_agent_spec
from experiments.artifacts import ArtifactStore
from experiments.contracts import (
    ExecutionLimits,
    ExperimentCondition,
    ExperimentTask,
    GenerationConfig,
    RenderedInvocation,
)
from experiments.executor import ExperimentExecutor
from experiments.gateway import FakeExperimentGateway
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset
from experiments.planning import (
    build_execution_manifest,
    build_execution_plan,
    load_execution_plan,
    plan_json_bytes,
)
from experiments.rendering import (
    calculate_condition_bundle_checksum,
    load_manual_system_prompt,
    render_factory_invocation,
    render_manual_invocation,
)

DEFAULT_DEFINITION_ROOT = Path(__file__).parent / "definitions" / "writer-v1"
_SMOKE_NAMESPACE = UUID("c9506518-07c6-5a4f-84ef-6cfd74ae3848")


class _SyntheticSmokeProvider:
    """Build non-evidentiary inputs solely for offline executor verification."""

    def __init__(
        self,
        dataset: LoadedExperimentDataset,
        manual_prompt: str,
    ) -> None:
        self._dataset = dataset
        self._manual_prompt = manual_prompt

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
            agent_spec=_synthetic_spec(task),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiments",
        description="Offline M5 execution-plan and recovery tools.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    plan = subcommands.add_parser("plan", help="generate the deterministic plan")
    _add_definition_root(plan)
    plan.add_argument("--output", type=Path)

    verify = subcommands.add_parser("verify-plan", help="verify the committed plan")
    _add_definition_root(verify)
    verify.add_argument("--plan", type=Path)

    smoke = subcommands.add_parser(
        "run-fake",
        help="run an offline executor smoke; output is not experiment evidence",
    )
    _add_definition_root(smoke)
    smoke.add_argument("--plan", type=Path)
    smoke.add_argument("--output-root", type=Path, required=True)
    smoke.add_argument("--max-items", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    definition_root = args.definition_root.resolve()
    dataset = load_experiment_dataset(definition_root)
    if args.command == "plan":
        plan = build_execution_plan(dataset)
        output = (args.output or definition_root / "execution-plan.json").resolve()
        store = ArtifactStore(output.parent)
        created = store.write_bytes_once(output.name, plan_json_bytes(plan))
        action = "created" if created else "verified"
        print(f"{action} {output} sha256={plan.plan_checksum}")
        return 0
    plan_path = (args.plan or definition_root / "execution-plan.json").resolve()
    plan = load_execution_plan(plan_path, dataset)
    if args.command == "verify-plan":
        print(
            f"verified {plan_path} runs={len(plan.items)} sha256={plan.plan_checksum}"
        )
        return 0
    return asyncio.run(
        _run_fake(
            dataset=dataset,
            plan=plan,
            output_root=args.output_root,
            max_items=args.max_items,
        )
    )


async def _run_fake(
    *,
    dataset: LoadedExperimentDataset,
    plan: object,
    output_root: Path,
    max_items: int,
) -> int:
    from experiments.contracts import ExecutionPlan

    if not isinstance(plan, ExecutionPlan):
        raise TypeError("plan must be an ExecutionPlan")
    prompt_path = dataset.root / "conditions" / "manual-system.txt"
    manual_prompt, prompt_bytes = load_manual_system_prompt(prompt_path)
    generation = GenerationConfig(
        provider="fake-provider",
        model="fake-writer-v1",
        sdk_version="0.0.0",
        temperature=0,
        max_output_tokens=512,
        request_timeout_seconds=30,
    )
    manifest = build_execution_manifest(
        dataset=dataset,
        plan=plan,
        condition_bundle_checksum=calculate_condition_bundle_checksum(prompt_bytes),
        generation=generation,
        limits=ExecutionLimits(
            max_provider_requests=720,
            max_prompt_tokens=3_000_000,
            max_completion_tokens=1_000_000,
            prompt_tokens_per_attempt_upper_bound=4_000,
        ),
    )
    executor = ExperimentExecutor(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        invocation_provider=_SyntheticSmokeProvider(dataset, manual_prompt),
        gateway=FakeExperimentGateway(),
        store=ArtifactStore(output_root),
    )
    runs = await executor.execute(max_items=max_items)
    counts = Counter(run.status.value for run in runs)
    print(
        "offline smoke only; not experiment evidence: "
        f"runs={len(runs)} statuses={dict(sorted(counts.items()))}"
    )
    return 0


def _synthetic_spec(task: ExperimentTask) -> AgentSpec:
    unsigned = AgentSpec(
        instance_id=uuid5(_SMOKE_NAMESPACE, task.task_id),
        revision=1,
        prototype=PrototypeRef(
            prototype_id="synthetic-smoke-writer",
            version="0.0.0",
            checksum="0" * 64,
        ),
        agent_type="writer-agent",
        role="Synthetic Smoke Writer",
        system_prompt="Produce an offline synthetic result from supplied knowledge.",
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
        generated_at=datetime(2026, 7, 25, tzinfo=UTC),
        spec_checksum="0" * 64,
        metadata={"evidence_scope": "offline-smoke-only"},
    )
    return unsigned.model_copy(update={"spec_checksum": checksum_agent_spec(unsigned)})


def _add_definition_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--definition-root",
        type=Path,
        default=DEFAULT_DEFINITION_ROOT,
    )
