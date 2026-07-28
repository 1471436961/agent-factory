"""Controlled, recoverable launcher for the reviewed M5.5 Writer Pilot."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import AnyHttpUrl, Field

from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    KnowledgeSelection,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.container import build_container
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import (
    FrozenModel,
    JsonObject,
    SemVer,
    Sha256,
    Slug,
    sha256_model,
)
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    InjectionMode,
    KnowledgeKind,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentSpec,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.settings import DEFAULT_MIGRATIONS_DIR, Settings
from experiments.artifacts import ArtifactStore
from experiments.contracts import (
    CurrencyCode,
    ExecutionPlan,
    ExperimentCondition,
    ExperimentPurpose,
    ExperimentRun,
    ExperimentTask,
    FrozenExperimentManifest,
    RenderedInvocation,
    calculate_conservative_cost_micros,
)
from experiments.executor import ExperimentExecutor, InvocationProvider
from experiments.formal import validate_formal_preflight
from experiments.freezing import (
    load_freeze_candidate_spec,
    load_frozen_experiment_manifest,
    verify_freeze_manifest,
)
from experiments.gateway import ExperimentGateway
from experiments.loader import LoadedExperimentDataset, load_experiment_dataset
from experiments.moonshot_gateway import create_moonshot_experiment_gateway
from experiments.pilot import validate_pilot_preflight
from experiments.planning import load_execution_plan
from experiments.rendering import (
    load_manual_system_prompt,
    render_factory_invocation,
    render_manual_invocation,
    validate_condition_pair,
)

_ACTOR = "experiment-owner"
_PROTOTYPE_VERSION = "1.0.0"
_PREPARATION_ROOT = "_factory-preparation"
_EXPECTED_EVENTS_PER_DOMAIN = 6


class PilotLaunchError(RuntimeError):
    """A live Pilot gate or recoverable preparation invariant failed."""


class PilotFactoryRecord(FrozenModel):
    """One controller-exported AgentSpec bound to one Pilot domain."""

    domain_id: Slug
    knowledge_id: Slug
    knowledge_version: SemVer
    knowledge_checksum: Sha256
    agent_spec: AgentSpec


class PilotFactoryPreparation(FrozenModel):
    """Write-once evidence that FACTORY inputs came from FactoryController."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: Slug
    dataset_checksum: Sha256
    execution_manifest_checksum: Sha256
    records: tuple[PilotFactoryRecord, ...] = Field(min_length=1)
    audit_events: tuple[AuditEvent, ...] = Field(min_length=1)
    preparation_checksum: Sha256


class PilotLaunchSummary(FrozenModel):
    """Observed execution facts; reserved limits remain separately visible."""

    experiment_id: Slug
    run_count: int = Field(ge=1)
    status_counts: JsonObject
    provider_attempts: int = Field(ge=0)
    observed_prompt_tokens: int = Field(ge=0)
    observed_completion_tokens: int = Field(ge=0)
    currency: CurrencyCode
    observed_cost_micros: int = Field(ge=0)
    max_provider_requests: int = Field(ge=1)
    hard_cost_limit_micros: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class PilotLaunchRequest:
    definition_root: Path
    plan_path: Path
    manifest_path: Path
    formal_definition_root: Path
    formal_plan_path: Path
    output_root: Path
    allow_live: bool
    confirmed_experiment_id: str
    confirmed_currency: CurrencyCode
    confirmed_hard_cost_micros: int


@dataclass(frozen=True, slots=True)
class FormalLaunchRequest:
    definition_root: Path
    plan_path: Path
    manifest_path: Path
    output_root: Path
    allow_live: bool
    confirmed_experiment_id: str
    confirmed_currency: CurrencyCode
    confirmed_hard_cost_micros: int


class LiveLaunchRequest(Protocol):
    @property
    def definition_root(self) -> Path: ...

    @property
    def plan_path(self) -> Path: ...

    @property
    def manifest_path(self) -> Path: ...

    @property
    def output_root(self) -> Path: ...

    @property
    def allow_live(self) -> bool: ...

    @property
    def confirmed_experiment_id(self) -> str: ...

    @property
    def confirmed_currency(self) -> CurrencyCode: ...

    @property
    def confirmed_hard_cost_micros(self) -> int: ...


class ManagedExperimentGateway(ExperimentGateway, Protocol):
    async def close(self) -> None:
        """Release the provider client even after failure or interruption."""


class FreezeVerifier(Protocol):
    def __call__(
        self,
        manifest: FrozenExperimentManifest,
        *,
        repository_root: Path,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        plan_path: Path,
        verify_environment: bool = True,
    ) -> None: ...


class ApiKeySource(Protocol):
    def read(self) -> str | None:
        """Read the live credential only after every offline gate passes."""


class GatewayFactory(Protocol):
    def __call__(self, *, api_key: str) -> ManagedExperimentGateway: ...


class InvocationPreparer(Protocol):
    async def __call__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        manifest: FrozenExperimentManifest,
        store: ArtifactStore,
    ) -> InvocationProvider: ...


@dataclass(frozen=True, slots=True)
class EnvironmentApiKeySource:
    variable_name: str = "MOONSHOT_API_KEY"

    def read(self) -> str | None:
        return os.environ.get(self.variable_name)


@dataclass(frozen=True, slots=True)
class PilotLauncherDependencies:
    freeze_verifier: FreezeVerifier = verify_freeze_manifest
    invocation_preparer: InvocationPreparer = field(
        default_factory=lambda: prepare_pilot_invocation_provider
    )
    api_key_source: ApiKeySource = field(default_factory=EnvironmentApiKeySource)
    gateway_factory: GatewayFactory = field(
        default_factory=lambda: _create_managed_moonshot_gateway
    )


class PilotInvocationProvider:
    """Render both conditions from reviewed bytes and prepared AgentSpecs."""

    def __init__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        manual_system_prompt: str,
        preparation: PilotFactoryPreparation,
    ) -> None:
        self._dataset = dataset
        self._manual_system_prompt = manual_system_prompt
        self._specs = {
            record.domain_id: record.agent_spec for record in preparation.records
        }

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
                manual_system_prompt=self._manual_system_prompt,
            )
        try:
            spec = self._specs[task.domain_id]
        except KeyError as exc:
            raise PilotLaunchError("Pilot domain has no prepared AgentSpec") from exc
        return render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge,
            agent_spec=spec,
        )


async def run_live_pilot(
    request: PilotLaunchRequest,
    *,
    repository_root: Path,
    dependencies: PilotLauncherDependencies | None = None,
) -> PilotLaunchSummary:
    """Run all frozen Pilot coordinates after every offline approval gate passes."""

    deps = dependencies or PilotLauncherDependencies()
    root, dataset, plan_path, plan, manifest = _load_launch_inputs(
        request,
        repository_root,
    )

    deps.freeze_verifier(
        manifest,
        repository_root=root,
        dataset=dataset,
        plan=plan,
        plan_path=plan_path,
        verify_environment=True,
    )
    candidate = load_freeze_candidate_spec(root / manifest.candidate_spec_path)
    formal_dataset = load_experiment_dataset(request.formal_definition_root)
    formal_plan = load_execution_plan(request.formal_plan_path, formal_dataset)
    validate_pilot_preflight(
        pilot_dataset=dataset,
        pilot_plan=plan,
        candidate=candidate,
        formal_dataset=formal_dataset,
        formal_plan=formal_plan,
    )
    return await _run_verified_live_experiment(
        request=request,
        repository_root=root,
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        dependencies=deps,
        expected_purpose=ExperimentPurpose.PILOT,
    )


async def run_live_formal(
    request: FormalLaunchRequest,
    *,
    repository_root: Path,
    dependencies: PilotLauncherDependencies | None = None,
) -> PilotLaunchSummary:
    """Run all 240 formal coordinates after exact frozen preflight succeeds."""

    deps = dependencies or PilotLauncherDependencies()
    root, dataset, plan_path, plan, manifest = _load_launch_inputs(
        request,
        repository_root,
    )
    deps.freeze_verifier(
        manifest,
        repository_root=root,
        dataset=dataset,
        plan=plan,
        plan_path=plan_path,
        verify_environment=True,
    )
    candidate = load_freeze_candidate_spec(root / manifest.candidate_spec_path)
    validate_formal_preflight(dataset=dataset, plan=plan, candidate=candidate)
    return await _run_verified_live_experiment(
        request=request,
        repository_root=root,
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        dependencies=deps,
        expected_purpose=ExperimentPurpose.FORMAL,
    )


async def _run_verified_live_experiment(
    *,
    request: LiveLaunchRequest,
    repository_root: Path,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    manifest: FrozenExperimentManifest,
    dependencies: PilotLauncherDependencies,
    expected_purpose: ExperimentPurpose,
) -> PilotLaunchSummary:
    _validate_approval(request, manifest, expected_purpose=expected_purpose)
    output_root = validate_live_output_root(request.output_root, repository_root)
    store = ArtifactStore(output_root)
    provider = await dependencies.invocation_preparer(
        dataset=dataset,
        plan=plan,
        manifest=manifest,
        store=store,
    )
    _validate_all_plan_invocations(dataset, plan, provider)

    api_key = dependencies.api_key_source.read()
    if api_key is None or not api_key.strip():
        raise PilotLaunchError("MOONSHOT_API_KEY is not set or is empty")
    try:
        gateway = dependencies.gateway_factory(api_key=api_key)
    except Exception:
        raise PilotLaunchError("Moonshot experiment client cannot be created") from None
    if not gateway.is_live:
        await _close_gateway(gateway)
        raise PilotLaunchError("Pilot gateway must identify itself as live")
    executor = ExperimentExecutor(
        dataset=dataset,
        plan=plan,
        manifest=manifest.execution_manifest,
        invocation_provider=provider,
        gateway=gateway,
        store=store,
        allow_live=True,
    )
    try:
        runs = await executor.execute()
    except BaseException as exc:
        try:
            await gateway.close()
        except Exception:
            exc.add_note("Moonshot experiment client also failed to close")
        raise
    await _close_gateway(gateway)
    return _summarize_runs(runs, manifest)


async def prepare_pilot_invocation_provider(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    manifest: FrozenExperimentManifest,
    store: ArtifactStore,
) -> PilotInvocationProvider:
    """Create or recover controller-backed AgentSpecs before any provider call."""

    preparation_path = _preparation_path(dataset.definition.experiment_id)
    if store.exists(preparation_path):
        preparation = store.read_model(preparation_path, PilotFactoryPreparation)
        _validate_preparation(preparation, dataset, manifest)
        provider = _build_provider(dataset, preparation)
        _validate_all_plan_invocations(dataset, plan, provider)
        return provider

    factory_root = store.root / _PREPARATION_ROOT / dataset.definition.experiment_id
    settings = Settings.model_validate(
        {
            "environment": "experiment-evidence",
            "database_url": (
                f"sqlite+aiosqlite:///{(factory_root / 'factory.db').as_posix()}"
            ),
            "data_dir": factory_root,
            "migrations_dir": DEFAULT_MIGRATIONS_DIR,
            "idempotency_ttl_seconds": 31_536_000,
        }
    )
    container = build_container(settings)
    await container.start()
    try:
        records: list[PilotFactoryRecord] = []
        for fixture in dataset.knowledge:
            tasks = tuple(
                task for task in dataset.tasks if task.domain_id == fixture.domain_id
            )
            output_schema = _shared_domain_schema(tasks)
            prototype_id = _prototype_id(
                dataset.definition.experiment_id,
                fixture.domain_id,
            )
            prototype = await container.controller.register_prototype(
                RegisterPrototypeCommand(
                    prototype_id=prototype_id,
                    version=_PROTOTYPE_VERSION,
                    definition=AgentDefinition(
                        agent_type="experiment-writer",
                        role="Technical Writer",
                        system_prompt=(
                            "Produce accurate documentation using only the supplied "
                            "current domain knowledge. Reject legacy values."
                        ),
                        capabilities=frozenset({Capability.WRITE}),
                        output_schema=output_schema,
                        knowledge_slots=(
                            KnowledgeSlot(
                                name="domain-knowledge",
                                accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
                                min_version=fixture.version,
                                injection_mode=InjectionMode.INLINE,
                            ),
                        ),
                        metadata={
                            "experiment_id": dataset.definition.experiment_id,
                            "domain_id": fixture.domain_id,
                        },
                    ),
                    publish=True,
                    actor=_ACTOR,
                    idempotency_key=_idempotency_key(fixture.domain_id, "prototype"),
                )
            )
            registered = await container.controller.register_knowledge(
                RegisterKnowledgeCommand(
                    knowledge=DomainKnowledgeDraft(
                        knowledge_id=fixture.knowledge_id,
                        version=fixture.version,
                        name=fixture.name,
                        kind=KnowledgeKind.DOCUMENT,
                        source_uri=AnyHttpUrl(
                            "https://fixtures.invalid/"
                            f"{dataset.definition.experiment_id}/"
                            f"{fixture.knowledge_id}/{fixture.version}.md"
                        ),
                        mime_type="text/markdown",
                        checksum=fixture.content_checksum,
                        tags=frozenset({"synthetic", "experiment"}),
                    ),
                    actor=_ACTOR,
                    idempotency_key=_idempotency_key(fixture.domain_id, "knowledge"),
                )
            )
            instance = await container.controller.clone_agent(
                CloneAgentCommand(
                    prototype_id=prototype.prototype_id,
                    prototype_version=prototype.version,
                    actor=_ACTOR,
                    idempotency_key=_idempotency_key(fixture.domain_id, "clone"),
                )
            )
            bound = await container.controller.bind_knowledge(
                BindKnowledgeCommand(
                    instance_id=instance.instance_id,
                    expected_revision=instance.revision,
                    selections=(
                        KnowledgeSelection(
                            slot_name="domain-knowledge",
                            knowledge_id=registered.knowledge_id,
                            version=registered.version,
                        ),
                    ),
                    actor=_ACTOR,
                    idempotency_key=_idempotency_key(fixture.domain_id, "binding"),
                )
            )
            spec = await container.controller.export_spec(
                bound.instance_id,
                actor=_ACTOR,
            )
            records.append(
                PilotFactoryRecord(
                    domain_id=fixture.domain_id,
                    knowledge_id=fixture.knowledge_id,
                    knowledge_version=fixture.version,
                    knowledge_checksum=fixture.content_checksum,
                    agent_spec=spec,
                )
            )

        audit_page = await container.controller.query_audit(
            AuditQuery(actor=_ACTOR, page_size=100)
        )
        if audit_page.total != len(audit_page.items):
            raise PilotLaunchError("Pilot factory audit evidence exceeds one page")
        unsigned = PilotFactoryPreparation(
            experiment_id=dataset.definition.experiment_id,
            dataset_checksum=dataset.dataset_checksum,
            execution_manifest_checksum=manifest.execution_manifest.manifest_checksum,
            records=tuple(sorted(records, key=lambda item: item.domain_id)),
            audit_events=tuple(
                sorted(audit_page.items, key=lambda item: str(item.event_id))
            ),
            preparation_checksum="0" * 64,
        )
        preparation = unsigned.model_copy(
            update={"preparation_checksum": _preparation_checksum(unsigned)}
        )
        _validate_preparation(preparation, dataset, manifest)
        store.write_model_once(preparation_path, preparation)
    finally:
        await container.close()
    provider = _build_provider(dataset, preparation)
    _validate_all_plan_invocations(dataset, plan, provider)
    return provider


def validate_live_output_root(output_root: Path, repository_root: Path) -> Path:
    """Allow only ignored in-repository output or a separate local tree."""

    root = repository_root.resolve(strict=True)
    unresolved = output_root if output_root.is_absolute() else Path.cwd() / output_root
    _reject_existing_symlink_segments(unresolved)
    resolved = unresolved.resolve(strict=False)
    if resolved == root:
        raise PilotLaunchError("Pilot output root cannot be the repository root")
    if resolved.is_relative_to(root):
        relative = resolved.relative_to(root)
        if not relative.parts or relative.parts[0] != ".tmp":
            raise PilotLaunchError("in-repository Pilot output must be below .tmp")
    return resolved


def _validate_approval(
    request: LiveLaunchRequest,
    manifest: FrozenExperimentManifest,
    *,
    expected_purpose: ExperimentPurpose,
) -> None:
    if manifest.purpose is not expected_purpose:
        raise PilotLaunchError("live execution purpose does not match freeze Manifest")
    if not request.allow_live:
        raise PilotLaunchError("live execution requires --allow-live")
    if request.confirmed_experiment_id != manifest.experiment_id:
        raise PilotLaunchError("confirmed experiment ID does not match Manifest")
    if request.confirmed_currency != manifest.cost_budget.currency:
        raise PilotLaunchError("confirmed currency does not match Manifest")
    hard_limit = manifest.cost_budget.hard_cost_limit_micros
    if request.confirmed_hard_cost_micros != hard_limit:
        raise PilotLaunchError("confirmed hard cost does not match Manifest")


def _validate_all_plan_invocations(
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    provider: InvocationProvider,
) -> None:
    tasks = {task.task_id: task for task in dataset.tasks}
    for task in dataset.tasks:
        validate_condition_pair(
            provider.render(task, ExperimentCondition.MANUAL),
            provider.render(task, ExperimentCondition.FACTORY),
        )
    for item in plan.items:
        rendered = provider.render(tasks[item.task_id], item.condition)
        if rendered.task_id != item.task_id or rendered.condition is not item.condition:
            raise PilotLaunchError("Pilot invocation does not match plan coordinate")


def _validate_preparation(
    preparation: PilotFactoryPreparation,
    dataset: LoadedExperimentDataset,
    manifest: FrozenExperimentManifest,
) -> None:
    if _preparation_checksum(preparation) != preparation.preparation_checksum:
        raise PilotLaunchError("Pilot factory preparation checksum is invalid")
    if (
        preparation.experiment_id != dataset.definition.experiment_id
        or preparation.dataset_checksum != dataset.dataset_checksum
        or preparation.execution_manifest_checksum
        != manifest.execution_manifest.manifest_checksum
    ):
        raise PilotLaunchError("Pilot factory preparation identity is stale")
    records = preparation.records
    if tuple(record.domain_id for record in records) != tuple(
        sorted(dataset.definition.domain_ids)
    ):
        raise PilotLaunchError("Pilot factory preparation domains are incomplete")
    expected_events: Counter[tuple[AuditEventType, str, int | None]] = Counter()
    for record in records:
        fixture = next(
            item for item in dataset.knowledge if item.domain_id == record.domain_id
        )
        spec = record.agent_spec
        if (
            record.knowledge_id != fixture.knowledge_id
            or record.knowledge_version != fixture.version
            or record.knowledge_checksum != fixture.content_checksum
            or spec.prototype.prototype_id
            != _prototype_id(dataset.definition.experiment_id, record.domain_id)
            or spec.prototype.version != _PROTOTYPE_VERSION
            or spec.revision != 2
        ):
            raise PilotLaunchError("Pilot factory AgentSpec provenance is stale")
        expected_events.update(
            {
                (
                    AuditEventType.PROTOTYPE_REGISTERED,
                    spec.prototype.prototype_id,
                    None,
                ): 1,
                (
                    AuditEventType.PROTOTYPE_PUBLISHED,
                    spec.prototype.prototype_id,
                    None,
                ): 1,
                (AuditEventType.KNOWLEDGE_REGISTERED, record.knowledge_id, None): 1,
                (AuditEventType.INSTANCE_CLONED, str(spec.instance_id), 1): 1,
                (AuditEventType.KNOWLEDGE_BOUND, str(spec.instance_id), 2): 1,
                (AuditEventType.SPEC_EXPORTED, str(spec.instance_id), 2): 1,
            }
        )
    actual_events = Counter(
        (event.event_type, event.entity_id, event.entity_revision)
        for event in preparation.audit_events
    )
    if (
        actual_events != expected_events
        or len(preparation.audit_events) != len(records) * _EXPECTED_EVENTS_PER_DOMAIN
        or any(event.actor != _ACTOR for event in preparation.audit_events)
    ):
        raise PilotLaunchError("Pilot factory audit chain is incomplete")
    _validate_audit_payloads(preparation)


def _validate_audit_payloads(preparation: PilotFactoryPreparation) -> None:
    records_by_instance = {
        str(record.agent_spec.instance_id): record for record in preparation.records
    }
    records_by_knowledge = {
        record.knowledge_id: record for record in preparation.records
    }
    records_by_prototype = {
        record.agent_spec.prototype.prototype_id: record
        for record in preparation.records
    }
    for event in preparation.audit_events:
        payload: Mapping[str, object] = event.payload
        if event.event_type is AuditEventType.PROTOTYPE_REGISTERED:
            record = records_by_prototype[event.entity_id]
            if payload.get("checksum") != record.agent_spec.prototype.checksum:
                raise PilotLaunchError("Pilot prototype audit checksum is invalid")
        elif event.event_type is AuditEventType.PROTOTYPE_PUBLISHED:
            record = records_by_prototype[event.entity_id]
            if (
                payload.get("version") != record.agent_spec.prototype.version
                or payload.get("status") != "published"
            ):
                raise PilotLaunchError("Pilot publication audit identity is invalid")
        elif event.event_type is AuditEventType.KNOWLEDGE_REGISTERED:
            record = records_by_knowledge[event.entity_id]
            if (
                payload.get("version") != record.knowledge_version
                or payload.get("checksum") != record.knowledge_checksum
            ):
                raise PilotLaunchError("Pilot knowledge audit checksum is invalid")
        elif event.event_type is AuditEventType.INSTANCE_CLONED:
            record = records_by_instance[event.entity_id]
            if payload.get("prototype_checksum") != (
                record.agent_spec.prototype.checksum
            ):
                raise PilotLaunchError("Pilot clone audit checksum is invalid")
        elif event.event_type is AuditEventType.KNOWLEDGE_BOUND:
            record = records_by_instance[event.entity_id]
            if payload.get("knowledge_checksum") != record.knowledge_checksum:
                raise PilotLaunchError("Pilot binding audit checksum is invalid")
        elif event.event_type is AuditEventType.SPEC_EXPORTED:
            record = records_by_instance[event.entity_id]
            if payload.get("spec_checksum") != record.agent_spec.spec_checksum:
                raise PilotLaunchError("Pilot AgentSpec audit checksum is invalid")


def _build_provider(
    dataset: LoadedExperimentDataset,
    preparation: PilotFactoryPreparation,
) -> PilotInvocationProvider:
    prompt, _prompt_bytes = load_manual_system_prompt(
        dataset.root / "conditions" / "manual-system.txt"
    )
    return PilotInvocationProvider(
        dataset=dataset,
        manual_system_prompt=prompt,
        preparation=preparation,
    )


def _load_launch_inputs(
    request: LiveLaunchRequest,
    repository_root: Path,
) -> tuple[
    Path,
    LoadedExperimentDataset,
    Path,
    ExecutionPlan,
    FrozenExperimentManifest,
]:
    root = repository_root.resolve(strict=True)
    dataset = load_experiment_dataset(request.definition_root)
    plan_path = request.plan_path.resolve(strict=True)
    plan = load_execution_plan(plan_path, dataset)
    manifest_path = request.manifest_path.resolve(strict=True)
    manifest = load_frozen_experiment_manifest(manifest_path)
    return root, dataset, plan_path, plan, manifest


def _create_managed_moonshot_gateway(*, api_key: str) -> ManagedExperimentGateway:
    return create_moonshot_experiment_gateway(api_key=api_key)


def _shared_domain_schema(tasks: tuple[ExperimentTask, ...]) -> JsonObject:
    if not tasks:
        raise PilotLaunchError("Pilot domain has no tasks")
    schema = tasks[0].output_schema
    if any(task.output_schema != schema for task in tasks[1:]):
        raise PilotLaunchError("Pilot domain tasks require different output schemas")
    return schema


def _prototype_id(experiment_id: str, domain_id: str) -> str:
    value = f"{experiment_id}-{domain_id}-writer"
    if len(value) > 64:
        raise PilotLaunchError("Pilot prototype identity exceeds domain limit")
    return value


def _idempotency_key(domain_id: str, operation: str) -> str:
    return f"m5-5-6-{domain_id}-{operation}"


def _preparation_path(experiment_id: str) -> str:
    return f"{_PREPARATION_ROOT}/{experiment_id}/preparation.json"


def _preparation_checksum(preparation: PilotFactoryPreparation) -> str:
    return sha256_model(preparation, exclude={"preparation_checksum"})


def _reject_existing_symlink_segments(path: Path) -> None:
    current = Path(path.anchor) if path.anchor else Path()
    for part in path.parts[1:] if path.anchor else path.parts:
        current = current / part
        if current.is_symlink():
            raise PilotLaunchError("Pilot output path cannot contain symbolic links")


async def _close_gateway(gateway: ManagedExperimentGateway) -> None:
    try:
        await gateway.close()
    except Exception:
        raise PilotLaunchError("Moonshot experiment client cannot be closed") from None


def _summarize_runs(
    runs: tuple[ExperimentRun, ...],
    manifest: FrozenExperimentManifest,
) -> PilotLaunchSummary:
    attempts = tuple(attempt for run in runs for attempt in run.attempts)
    prompt_tokens = sum(attempt.prompt_tokens or 0 for attempt in attempts)
    completion_tokens = sum(attempt.completion_tokens or 0 for attempt in attempts)
    status_counts = Counter(run.status.value for run in runs)
    return PilotLaunchSummary(
        experiment_id=manifest.experiment_id,
        run_count=len(runs),
        status_counts=dict(sorted(status_counts.items())),
        provider_attempts=len(attempts),
        observed_prompt_tokens=prompt_tokens,
        observed_completion_tokens=completion_tokens,
        currency=manifest.pricing.currency,
        observed_cost_micros=calculate_conservative_cost_micros(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            pricing=manifest.pricing,
        ),
        max_provider_requests=manifest.execution_manifest.limits.max_provider_requests,
        hard_cost_limit_micros=manifest.cost_budget.hard_cost_limit_micros,
    )
