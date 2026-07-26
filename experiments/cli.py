"""Offline-only command line entry points for M5 experiment workflows."""

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
    AnalysisConfig,
    ExecutionLimits,
    ExperimentCondition,
    ExperimentTask,
    GenerationConfig,
    RenderedInvocation,
)
from experiments.executor import ExperimentExecutor
from experiments.freezing import (
    FreezeCandidateBuilder,
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
    publish_freeze_candidate,
    verify_freeze_manifest,
)
from experiments.gateway import FakeExperimentGateway
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset
from experiments.pilot import validate_pilot_preflight
from experiments.pipeline import OfflineAnalysisPipeline
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
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
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

    analyze = subcommands.add_parser(
        "analyze",
        help="validate complete execution evidence and publish offline analysis",
    )
    _add_definition_root(analyze)
    analyze.add_argument("--plan", type=Path)
    analyze.add_argument("--runs-root", type=Path, required=True)
    analyze.add_argument("--output-root", type=Path, required=True)
    analyze.add_argument("--bootstrap-seed", type=int)
    analyze.add_argument("--bootstrap-iterations", type=int, default=10_000)

    pilot = subcommands.add_parser(
        "verify-pilot",
        help="verify Pilot isolation and reviewed offline budget bounds",
    )
    _add_definition_root(pilot)
    pilot.add_argument("--plan", type=Path)
    pilot.add_argument("--spec", type=Path, required=True)
    pilot.add_argument(
        "--formal-definition-root",
        type=Path,
        default=DEFAULT_DEFINITION_ROOT,
    )
    pilot.add_argument("--formal-plan", type=Path)

    freeze = subcommands.add_parser(
        "freeze-candidate",
        help="derive an offline freeze candidate from reviewed local inputs",
    )
    _add_definition_root(freeze)
    freeze.add_argument("--plan", type=Path)
    freeze.add_argument("--spec", type=Path, required=True)
    freeze.add_argument("--output", type=Path)

    verify_freeze = subcommands.add_parser(
        "verify-freeze",
        help="verify frozen files and the current local execution environment",
    )
    _add_definition_root(verify_freeze)
    verify_freeze.add_argument("--plan", type=Path)
    verify_freeze.add_argument("--manifest", type=Path, required=True)
    verify_freeze.add_argument(
        "--content-only",
        action="store_true",
        help="verify portable content evidence without claiming environment readiness",
    )
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
    if args.command == "analyze":
        config = AnalysisConfig(
            bootstrap_seed=(
                dataset.definition.randomization_seed
                if args.bootstrap_seed is None
                else args.bootstrap_seed
            ),
            bootstrap_iterations=args.bootstrap_iterations,
        )
        result = OfflineAnalysisPipeline(
            dataset=dataset,
            plan=plan,
            run_store=ArtifactStore(args.runs_root),
            output_store=ArtifactStore(args.output_root),
            config=config,
        ).run()
        print(
            "offline analysis published: "
            f"runs={result.score_manifest.run_count} "
            f"score_set_sha256={result.score_manifest.score_set_checksum} "
            f"analysis_sha256={result.analysis_manifest.analysis_checksum}"
        )
        return 0
    if args.command == "verify-pilot":
        candidate = load_freeze_candidate_spec(args.spec.resolve())
        formal_root = args.formal_definition_root.resolve()
        formal_dataset = load_experiment_dataset(formal_root)
        formal_plan_path = (
            args.formal_plan or formal_root / "execution-plan.json"
        ).resolve()
        formal_plan = load_execution_plan(formal_plan_path, formal_dataset)
        report = validate_pilot_preflight(
            pilot_dataset=dataset,
            pilot_plan=plan,
            candidate=candidate,
            formal_dataset=formal_dataset,
            formal_plan=formal_plan,
        )
        print(
            "verified Pilot preflight: "
            f"experiment={report.pilot_experiment_id} "
            f"tasks={report.task_count} runs={report.run_count} "
            f"requests={report.estimated_provider_requests}/"
            f"{report.max_provider_requests} "
            f"cost_usd_micros={report.estimated_cost_usd_micros}/"
            f"{report.hard_cost_limit_usd_micros}"
        )
        return 0
    if args.command == "freeze-candidate":
        candidate = load_freeze_candidate_spec(args.spec.resolve())
        manifest = FreezeCandidateBuilder(REPOSITORY_ROOT).build(
            candidate=candidate,
            candidate_spec_path=args.spec,
            plan_path=plan_path,
            dataset=dataset,
            plan=plan,
        )
        output = (
            args.output
            or REPOSITORY_ROOT / ".tmp" / "m5-freeze" / f"{candidate.freeze_id}.json"
        ).resolve()
        created = publish_freeze_candidate(
            manifest,
            repository_root=REPOSITORY_ROOT,
            output_path=output,
        )
        action = "created" if created else "verified"
        print(
            f"{action} freeze candidate {output} "
            f"sha256={manifest.manifest_checksum} "
            f"commit={manifest.source.source_commit} files={len(manifest.files)} "
            f"hard_cost_usd_micros={manifest.cost_budget.hard_cost_limit_usd_micros}"
        )
        return 0
    if args.command == "verify-freeze":
        manifest = load_frozen_experiment_manifest(args.manifest.resolve())
        verify_freeze_manifest(
            manifest,
            repository_root=REPOSITORY_ROOT,
            dataset=dataset,
            plan=plan,
            plan_path=plan_path,
            verify_environment=not args.content_only,
        )
        scope = "content-only" if args.content_only else "content-and-environment"
        print(
            f"verified freeze {args.manifest.resolve()} scope={scope} "
            f"sha256={manifest.manifest_checksum}"
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
