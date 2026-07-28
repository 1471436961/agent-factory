"""Command line entry points for offline M5 workflows and the gated Pilot."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from agent_factory.domain.enums import InjectionMode
from agent_factory.domain.errors import FactoryError
from agent_factory.domain.models import AgentSpec, KnowledgeRef, PrototypeRef
from agent_factory.domain.services.spec import checksum_agent_spec
from experiments.artifacts import ArtifactStore, ArtifactStoreError
from experiments.audit_verification import (
    audit_verification_checksum,
    publish_audit_verification,
    run_audit_lineage_verification,
)
from experiments.blind_review import build_blind_review_package
from experiments.contracts import (
    AnalysisConfig,
    ExecutionLimits,
    ExperimentCondition,
    ExperimentPurpose,
    ExperimentTask,
    GenerationConfig,
    RenderedInvocation,
)
from experiments.evidence_sealing import (
    build_formal_evidence_seal,
    build_pilot_evidence_seal,
    publish_formal_evidence_seal,
    publish_pilot_evidence_seal,
)
from experiments.executor import ExperimentExecutor
from experiments.formal import FormalPreflightError, validate_formal_preflight
from experiments.formal_freezing import (
    FormalCandidateDraftRequest,
    build_formal_freeze_candidate,
    publish_formal_freeze_candidate,
)
from experiments.freezing import (
    FreezeCandidateBuilder,
    FreezeError,
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
    publish_freeze_candidate,
    verify_freeze_manifest,
)
from experiments.gateway import FakeExperimentGateway
from experiments.loader import (
    ExperimentFixtureError,
    LoadedExperimentDataset,
    load_experiment_dataset,
)
from experiments.pilot import PilotPreflightError, validate_pilot_preflight
from experiments.pilot_launcher import (
    FormalLaunchRequest,
    PilotLaunchError,
    PilotLaunchRequest,
    run_live_formal,
    run_live_pilot,
)
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
DEFAULT_PILOT_DEFINITION_ROOT = (
    Path(__file__).parent / "definitions" / "writer-pilot-v1"
)
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
        description="M5 offline tools and explicitly gated Pilot execution.",
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

    blind_review = subcommands.add_parser(
        "build-blind-review",
        help="publish condition-free review items and a separate private mapping",
    )
    _add_definition_root(blind_review)
    blind_review.add_argument("--plan", type=Path)
    blind_review.add_argument("--runs-root", type=Path, required=True)
    blind_review.add_argument("--review-root", type=Path, required=True)
    blind_review.add_argument("--mapping-root", type=Path, required=True)

    formal_candidate = subcommands.add_parser(
        "draft-formal-candidate",
        help="derive the reviewed formal candidate before clean-commit freezing",
    )
    _add_definition_root(formal_candidate)
    formal_candidate.add_argument("--plan", type=Path)
    formal_candidate.add_argument(
        "--pilot-manifest",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "experiments/evidence/writer-pilot-v1/freeze-manifest.json"
        ),
    )
    formal_candidate.add_argument(
        "--pilot-report",
        type=Path,
        default=REPOSITORY_ROOT / "docs/reports/m5.5-moonshot-pilot-review.md",
    )
    formal_candidate.add_argument(
        "--pilot-evidence-seal",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "experiments/evidence/writer-pilot-v1/evidence-seal-mfjs-20260728.json"
        ),
    )
    formal_candidate.add_argument(
        "--pricing-source-url",
        default="https://platform.kimi.com/docs/pricing/chat-k26",
    )
    formal_candidate.add_argument(
        "--pricing-captured-at", type=datetime.fromisoformat, required=True
    )
    formal_candidate.add_argument(
        "--created-at", type=datetime.fromisoformat, required=True
    )
    formal_candidate.add_argument("--output", type=Path)

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

    formal_verify = subcommands.add_parser(
        "verify-formal",
        help="verify exact formal design, provider profile, Pilot lineage and budget",
    )
    _add_definition_root(formal_verify)
    formal_verify.add_argument("--plan", type=Path)
    formal_verify.add_argument("--spec", type=Path, required=True)

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

    seal_evidence = subcommands.add_parser(
        "seal-pilot-evidence",
        help="validate and hash one externally retained Pilot evidence tree",
    )
    _add_definition_root(seal_evidence, default=DEFAULT_PILOT_DEFINITION_ROOT)
    seal_evidence.add_argument("--plan", type=Path)
    seal_evidence.add_argument("--manifest", type=Path, required=True)
    seal_evidence.add_argument("--evidence-root", type=Path, required=True)
    seal_evidence.add_argument("--root-label", required=True)
    seal_evidence.add_argument("--output", type=Path, required=True)

    seal_formal_evidence = subcommands.add_parser(
        "seal-formal-evidence",
        help="validate and hash one externally retained formal evidence tree",
    )
    _add_definition_root(seal_formal_evidence)
    seal_formal_evidence.add_argument("--plan", type=Path)
    seal_formal_evidence.add_argument("--manifest", type=Path, required=True)
    seal_formal_evidence.add_argument("--evidence-root", type=Path, required=True)
    seal_formal_evidence.add_argument("--root-label", required=True)
    seal_formal_evidence.add_argument("--output", type=Path, required=True)

    audit_lineage = subcommands.add_parser(
        "verify-audit-lineage",
        help="run the deterministic H5 lineage check in an isolated SQLite database",
    )
    _add_definition_root(audit_lineage)
    audit_lineage.add_argument("--plan", type=Path)
    audit_lineage.add_argument("--database-path", type=Path, required=True)
    audit_lineage.add_argument("--output", type=Path, required=True)

    live = subcommands.add_parser(
        "run-pilot-live",
        help="execute every run in one fully verified Pilot Manifest",
    )
    _add_definition_root(live, default=DEFAULT_PILOT_DEFINITION_ROOT)
    live.add_argument("--plan", type=Path)
    live.add_argument("--manifest", type=Path, required=True)
    live.add_argument("--output-root", type=Path, required=True)
    live.add_argument(
        "--formal-definition-root",
        type=Path,
        default=DEFAULT_DEFINITION_ROOT,
    )
    live.add_argument("--formal-plan", type=Path)
    live.add_argument("--allow-live", action="store_true")
    live.add_argument("--confirm-experiment-id", required=True)
    live.add_argument("--confirm-currency", required=True, choices=("USD", "CNY"))
    live.add_argument("--confirm-hard-cost-micros", type=int, required=True)

    formal_live = subcommands.add_parser(
        "run-formal-live",
        help="execute all 240 runs in one fully verified formal Manifest",
    )
    _add_definition_root(formal_live)
    formal_live.add_argument("--plan", type=Path)
    formal_live.add_argument("--manifest", type=Path, required=True)
    formal_live.add_argument("--output-root", type=Path, required=True)
    formal_live.add_argument("--allow-live", action="store_true")
    formal_live.add_argument("--confirm-experiment-id", required=True)
    formal_live.add_argument(
        "--confirm-currency", required=True, choices=("USD", "CNY")
    )
    formal_live.add_argument("--confirm-hard-cost-micros", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    definition_root = args.definition_root.resolve()
    if args.command in {"run-pilot-live", "run-formal-live"}:
        plan_path = (args.plan or definition_root / "execution-plan.json").resolve()
        try:
            if args.command == "run-pilot-live":
                formal_root = args.formal_definition_root.resolve()
                formal_plan = (
                    args.formal_plan or formal_root / "execution-plan.json"
                ).resolve()
                summary = asyncio.run(
                    run_live_pilot(
                        PilotLaunchRequest(
                            definition_root=definition_root,
                            plan_path=plan_path,
                            manifest_path=args.manifest,
                            formal_definition_root=formal_root,
                            formal_plan_path=formal_plan,
                            output_root=args.output_root,
                            allow_live=args.allow_live,
                            confirmed_experiment_id=args.confirm_experiment_id,
                            confirmed_currency=args.confirm_currency,
                            confirmed_hard_cost_micros=args.confirm_hard_cost_micros,
                        ),
                        repository_root=REPOSITORY_ROOT,
                    )
                )
            else:
                summary = asyncio.run(
                    run_live_formal(
                        FormalLaunchRequest(
                            definition_root=definition_root,
                            plan_path=plan_path,
                            manifest_path=args.manifest,
                            output_root=args.output_root,
                            allow_live=args.allow_live,
                            confirmed_experiment_id=args.confirm_experiment_id,
                            confirmed_currency=args.confirm_currency,
                            confirmed_hard_cost_micros=args.confirm_hard_cost_micros,
                        ),
                        repository_root=REPOSITORY_ROOT,
                    )
                )
        except (
            ArtifactStoreError,
            ExperimentFixtureError,
            FactoryError,
            FormalPreflightError,
            FreezeError,
            PilotLaunchError,
            PilotPreflightError,
        ) as exc:
            print(f"Live launch aborted: {exc}", file=sys.stderr)
            return 2
        print(
            "Live execution complete: "
            f"experiment={summary.experiment_id} runs={summary.run_count} "
            f"statuses={dict(summary.status_counts)} "
            f"attempts={summary.provider_attempts}/"
            f"{summary.max_provider_requests} "
            f"observed_tokens={summary.observed_prompt_tokens}+"
            f"{summary.observed_completion_tokens} "
            f"currency={summary.currency} "
            f"observed_cost_micros={summary.observed_cost_micros} "
            f"hard_cost_micros={summary.hard_cost_limit_micros}"
        )
        return 0
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
    if args.command == "draft-formal-candidate":
        output = (args.output or definition_root / "freeze-candidate.json").resolve()
        candidate = build_formal_freeze_candidate(
            request=FormalCandidateDraftRequest(
                repository_root=REPOSITORY_ROOT,
                candidate_path=output,
                pilot_manifest_path=args.pilot_manifest.resolve(),
                pilot_report_path=args.pilot_report.resolve(),
                pilot_evidence_seal_path=args.pilot_evidence_seal.resolve(),
                pricing_source_url=args.pricing_source_url,
                pricing_captured_at=args.pricing_captured_at,
                created_at=args.created_at,
            ),
            dataset=dataset,
            plan=plan,
        )
        created = publish_formal_freeze_candidate(candidate, output)
        action = "created" if created else "verified"
        print(
            f"{action} formal candidate {output} "
            f"runs={len(plan.items)} requests="
            f"{candidate.cost_budget.estimated_provider_requests}/"
            f"{candidate.execution_manifest.limits.max_provider_requests} "
            f"currency={candidate.cost_budget.currency} cost_micros="
            f"{candidate.cost_budget.estimated_cost_micros}/"
            f"{candidate.cost_budget.hard_cost_limit_micros}"
        )
        return 0
    if args.command == "seal-pilot-evidence":
        manifest = load_frozen_experiment_manifest(args.manifest.resolve())
        if manifest.purpose is not ExperimentPurpose.PILOT:
            raise FreezeError("Pilot evidence requires a Pilot freeze Manifest")
        if manifest.experiment_id != dataset.definition.experiment_id:
            raise FreezeError("Pilot freeze Manifest does not match definition root")
        pilot_seal = build_pilot_evidence_seal(
            dataset=dataset,
            plan=plan,
            evidence_root=args.evidence_root,
            evidence_root_label=args.root_label,
            freeze_manifest_checksum=manifest.manifest_checksum,
            expected_execution_manifest_checksum=(
                manifest.execution_manifest.manifest_checksum
            ),
        )
        created = publish_pilot_evidence_seal(pilot_seal, args.output)
        action = "created" if created else "verified"
        print(
            f"{action} Pilot evidence seal {args.output.resolve()} "
            f"files={len(pilot_seal.files)} runs={pilot_seal.run_count} "
            f"attempts={pilot_seal.attempt_count} "
            f"sha256={pilot_seal.seal_checksum}"
        )
        return 0
    if args.command == "seal-formal-evidence":
        manifest = load_frozen_experiment_manifest(args.manifest.resolve())
        if manifest.purpose is not ExperimentPurpose.FORMAL:
            raise FreezeError("Formal evidence requires a formal freeze Manifest")
        if manifest.experiment_id != dataset.definition.experiment_id:
            raise FreezeError("Formal freeze Manifest does not match definition root")
        formal_seal = build_formal_evidence_seal(
            dataset=dataset,
            plan=plan,
            evidence_root=args.evidence_root,
            evidence_root_label=args.root_label,
            freeze_manifest_checksum=manifest.manifest_checksum,
            expected_execution_manifest_checksum=(
                manifest.execution_manifest.manifest_checksum
            ),
        )
        created = publish_formal_evidence_seal(formal_seal, args.output)
        action = "created" if created else "verified"
        print(
            f"{action} formal evidence seal {args.output.resolve()} "
            f"files={len(formal_seal.files)} runs={formal_seal.run_count} "
            f"attempts={formal_seal.attempt_count} "
            f"sha256={formal_seal.seal_checksum}"
        )
        return 0
    if args.command == "verify-audit-lineage":
        record = asyncio.run(
            run_audit_lineage_verification(
                database_path=args.database_path,
                migrations_dir=(
                    REPOSITORY_ROOT
                    / "src"
                    / "agent_factory"
                    / "infrastructure"
                    / "sqlite"
                    / "sql"
                ),
                experiment_id=dataset.definition.experiment_id,
            )
        )
        created = publish_audit_verification(record, args.output)
        action = "created" if created else "verified"
        print(
            f"{action} H5 audit verification {args.output.resolve()} "
            f"steps={len(record.steps)} completeness={record.completeness:.6f} "
            f"passed={str(record.passed).lower()} "
            f"sha256={audit_verification_checksum(record)}"
        )
        return 0
    if args.command == "verify-plan":
        print(
            f"verified {plan_path} runs={len(plan.items)} sha256={plan.plan_checksum}"
        )
        return 0
    if args.command == "build-blind-review":
        blind_result = build_blind_review_package(
            dataset=dataset,
            plan=plan,
            run_store=ArtifactStore(args.runs_root),
            review_root=args.review_root,
            mapping_root=args.mapping_root,
        )
        print(
            "blind review package published: "
            f"items={blind_result.package.item_count} "
            f"package_sha256={blind_result.package.package_checksum} "
            f"mapping_sha256={blind_result.mapping.mapping_checksum}"
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
        analysis_result = OfflineAnalysisPipeline(
            dataset=dataset,
            plan=plan,
            run_store=ArtifactStore(args.runs_root),
            output_store=ArtifactStore(args.output_root),
            config=config,
        ).run()
        print(
            "offline analysis published: "
            f"runs={analysis_result.score_manifest.run_count} "
            f"score_set_sha256={analysis_result.score_manifest.score_set_checksum} "
            f"analysis_sha256={analysis_result.analysis_manifest.analysis_checksum}"
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
        pilot_report = validate_pilot_preflight(
            pilot_dataset=dataset,
            pilot_plan=plan,
            candidate=candidate,
            formal_dataset=formal_dataset,
            formal_plan=formal_plan,
        )
        print(
            "verified Pilot preflight: "
            f"experiment={pilot_report.pilot_experiment_id} "
            f"tasks={pilot_report.task_count} runs={pilot_report.run_count} "
            f"requests={pilot_report.estimated_provider_requests}/"
            f"{pilot_report.max_provider_requests} "
            f"currency={pilot_report.currency} "
            f"cost_micros={pilot_report.estimated_cost_micros}/"
            f"{pilot_report.hard_cost_limit_micros}"
        )
        return 0
    if args.command == "verify-formal":
        candidate = load_freeze_candidate_spec(args.spec.resolve())
        formal_report = validate_formal_preflight(
            dataset=dataset,
            plan=plan,
            candidate=candidate,
        )
        print(
            "verified formal preflight: "
            f"experiment={formal_report.experiment_id} "
            f"pilot={formal_report.pilot_experiment_id} "
            f"tasks={formal_report.task_count} "
            f"repetitions={formal_report.repetitions} "
            f"runs={formal_report.manual_run_count}+"
            f"{formal_report.factory_run_count} "
            f"requests={formal_report.estimated_provider_requests}/"
            f"{formal_report.max_provider_requests} "
            f"currency={formal_report.currency} "
            f"cost_micros={formal_report.estimated_cost_micros}/"
            f"{formal_report.hard_cost_limit_micros}"
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
            f"currency={manifest.cost_budget.currency} "
            f"hard_cost_micros={manifest.cost_budget.hard_cost_limit_micros}"
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


def _add_definition_root(
    parser: argparse.ArgumentParser,
    *,
    default: Path = DEFAULT_DEFINITION_ROOT,
) -> None:
    parser.add_argument(
        "--definition-root",
        type=Path,
        default=default,
    )
