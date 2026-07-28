"""Deterministic H5 verification against the real SQLite production chain."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from anyio import Path as AsyncPath

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    EvaluateInstanceCommand,
    KnowledgeSelection,
    PromoteAgentCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
)
from agent_factory.application.controller import FactoryController
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.queries import AuditQuery
from agent_factory.application.tooling import ToolPolicy
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import checksum_knowledge_content, sha256_model
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    InjectionMode,
    KnowledgeKind,
    RuleKind,
    ToolPermission,
)
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    SubmittedCaseResult,
)
from agent_factory.domain.models import (
    AgentDefinition,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.degradation import DegradationPolicy
from agent_factory.domain.services.evaluation import DeterministicRuleEngine
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.lifecycle import LifecyclePolicy
from agent_factory.domain.services.promotion import PromotionPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import SkillNode, SkillTree, SkillTreeDraft
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)
from agent_factory.infrastructure.system import ContextVarCorrelationContext
from agent_factory.infrastructure.tool_catalog import default_tool_catalog
from experiments.artifacts import ArtifactStore
from experiments.contracts import AuditStepResult, AuditVerificationRecord

_ACTOR = "m5-audit-verifier"
_CHECKED_AT = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
_CORRELATION_ID = UUID("4f08803c-2ec3-5daf-8dc0-a4e6750c20d4")
_VERIFICATION_ID = UUID("eef90cc0-8ba4-5f59-a440-055258ce21b5")


class AuditLineageVerificationError(RuntimeError):
    """The isolated H5 fixture cannot be prepared or published safely."""


@dataclass(frozen=True, slots=True)
class AuditFixtureIdentity:
    """Stable identities needed to re-check one prepared production chain."""

    experiment_id: str
    prototype_id: str
    prototype_version: str
    prototype_checksum: str
    knowledge_id: str
    knowledge_version: str
    knowledge_checksum: str
    instance_id: UUID
    bound_revision: int
    promoted_revision: int
    agent_spec_checksum: str
    evaluation_report_id: UUID
    skill_tree: SkillTreeRef
    evaluation_suite: EvaluationSuiteRef
    target_node_id: str


class _FixedClock:
    def now(self) -> datetime:
        return _CHECKED_AT


class _SequenceIdGenerator:
    def __init__(self) -> None:
        self._sequence = 0

    def new(self) -> UUID:
        self._sequence += 1
        return uuid5(NAMESPACE_URL, f"agent-factory:m5:h5:{self._sequence:03d}")


@dataclass(frozen=True, slots=True)
class _AuditRuntime:
    controller: FactoryController
    uow_factory: SqliteUnitOfWorkFactory
    correlation_context: ContextVarCorrelationContext


async def run_audit_lineage_verification(
    *,
    database_path: Path,
    migrations_dir: Path,
    experiment_id: str,
) -> AuditVerificationRecord:
    """Create one fixed chain in fresh SQLite and verify its persisted lineage."""

    identity = await prepare_audit_lineage_fixture(
        database_path=database_path,
        migrations_dir=migrations_dir,
        experiment_id=experiment_id,
    )
    return await verify_audit_lineage(
        uow_factory=SqliteUnitOfWorkFactory(database_path),
        identity=identity,
    )


async def prepare_audit_lineage_fixture(
    *,
    database_path: Path,
    migrations_dir: Path,
    experiment_id: str,
) -> AuditFixtureIdentity:
    """Create the fixed H5 production chain and return its stable identities."""

    database = database_path
    if await AsyncPath(database).exists():
        raise AuditLineageVerificationError(
            "H5 verification database must not already exist"
        )
    runtime = await _build_runtime(database, migrations_dir)
    token = runtime.correlation_context.set(str(_CORRELATION_ID))
    try:
        identity = await _prepare_fixture(runtime.controller, experiment_id)
    finally:
        runtime.correlation_context.reset(token)
    return identity


async def verify_audit_lineage(
    *,
    uow_factory: SqliteUnitOfWorkFactory,
    identity: AuditFixtureIdentity,
) -> AuditVerificationRecord:
    """Cross-check six audit events against persisted aggregate snapshots."""

    async with uow_factory(read_only=True) as uow:
        prototype = await uow.prototypes.get(
            identity.prototype_id,
            identity.prototype_version,
        )
        knowledge = await uow.knowledge.get(
            identity.knowledge_id,
            identity.knowledge_version,
        )
        bound = await uow.instances.get(
            identity.instance_id,
            identity.bound_revision,
        )
        promoted = await uow.instances.get(
            identity.instance_id,
            identity.promoted_revision,
        )
        spec = await uow.specs.get(identity.instance_id, identity.bound_revision)
        report = await uow.evaluation_reports.get(identity.evaluation_report_id)
        tree = await uow.skill_trees.get(
            identity.skill_tree.tree_id,
            identity.skill_tree.version,
        )
        suite = await uow.evaluation_suites.get(
            identity.evaluation_suite.suite_id,
            identity.evaluation_suite.version,
        )
        events = (await uow.audit.query(AuditQuery(page_size=100))).items

    knowledge_registered = _matching_events(
        events,
        AuditEventType.KNOWLEDGE_REGISTERED,
        identity.knowledge_id,
    )
    steps = (
        _check_step(
            step_id="prototype-source",
            event_type=AuditEventType.PROTOTYPE_REGISTERED,
            entity_id=identity.prototype_id,
            events=events,
            predicate=lambda event: (
                prototype is not None
                and prototype.checksum == identity.prototype_checksum
                and event.payload.get("version") == identity.prototype_version
                and event.payload.get("checksum") == identity.prototype_checksum
            ),
        ),
        _check_step(
            step_id="instance-source",
            event_type=AuditEventType.INSTANCE_CLONED,
            entity_id=str(identity.instance_id),
            events=events,
            predicate=lambda event: (
                bound is not None
                and bound.prototype.prototype_id == identity.prototype_id
                and bound.prototype.version == identity.prototype_version
                and bound.prototype.checksum == identity.prototype_checksum
                and event.payload.get("prototype_checksum")
                == identity.prototype_checksum
            ),
        ),
        _check_step(
            step_id="knowledge-source",
            event_type=AuditEventType.KNOWLEDGE_BOUND,
            entity_id=str(identity.instance_id),
            events=events,
            predicate=lambda event: (
                knowledge is not None
                and knowledge.checksum == identity.knowledge_checksum
                and bound is not None
                and len(bound.knowledge_bindings) == 1
                and bound.knowledge_bindings[0].knowledge_checksum
                == identity.knowledge_checksum
                and len(knowledge_registered) == 1
                and knowledge_registered[0].payload.get("checksum")
                == identity.knowledge_checksum
                and event.entity_revision == identity.bound_revision
                and event.payload.get("knowledge_checksum")
                == identity.knowledge_checksum
            ),
        ),
        _check_step(
            step_id="agent-spec-source",
            event_type=AuditEventType.SPEC_EXPORTED,
            entity_id=str(identity.instance_id),
            events=events,
            predicate=lambda event: (
                spec is not None
                and spec.revision == identity.bound_revision
                and spec.spec_checksum == identity.agent_spec_checksum
                and event.entity_revision == identity.bound_revision
                and event.payload.get("spec_checksum") == identity.agent_spec_checksum
            ),
        ),
        _check_step(
            step_id="evaluation-source",
            event_type=AuditEventType.EVALUATION_COMPLETED,
            entity_id=str(identity.evaluation_report_id),
            events=events,
            predicate=lambda event: (
                report is not None
                and report.instance_id == identity.instance_id
                and report.instance_revision == identity.bound_revision
                and report.agent_spec_checksum == identity.agent_spec_checksum
                and report.skill_tree == identity.skill_tree
                and report.suite == identity.evaluation_suite
                and tree is not None
                and tree.checksum == identity.skill_tree.checksum
                and suite is not None
                and suite.checksum == identity.evaluation_suite.checksum
                and event.payload.get("agent_spec_checksum")
                == identity.agent_spec_checksum
            ),
        ),
        _check_step(
            step_id="promotion-source",
            event_type=AuditEventType.SKILL_PROMOTED,
            entity_id=str(identity.instance_id),
            events=events,
            predicate=lambda event: (
                promoted is not None
                and promoted.revision == identity.promoted_revision
                and promoted.active_skill_nodes == frozenset({identity.target_node_id})
                and event.entity_revision == identity.promoted_revision
                and event.payload.get("from_revision") == identity.bound_revision
                and event.payload.get("to_revision") == identity.promoted_revision
                and event.payload.get("node_id") == identity.target_node_id
                and event.payload.get("report_id") == str(identity.evaluation_report_id)
            ),
        ),
    )
    completeness = sum(step.passed for step in steps) / len(steps)
    return AuditVerificationRecord(
        verification_id=_VERIFICATION_ID,
        experiment_id=identity.experiment_id,
        instance_id=identity.instance_id,
        checked_at=_CHECKED_AT,
        steps=steps,
        completeness=completeness,
        passed=completeness == 1.0,
    )


def publish_audit_verification(
    record: AuditVerificationRecord,
    output_path: Path,
) -> bool:
    """Publish one canonical H5 record once and reject conflicting replay."""

    output = output_path.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    return ArtifactStore(output.parent).write_model_once(output.name, record)


def audit_verification_checksum(record: AuditVerificationRecord) -> str:
    return sha256_model(record)


async def _build_runtime(database: Path, migrations_dir: Path) -> _AuditRuntime:
    clock = _FixedClock()
    id_generator = _SequenceIdGenerator()
    correlation_context = ContextVarCorrelationContext()
    runner = SqliteMigrationRunner(database, migrations_dir, clock)
    await runner.migrate()
    uow_factory = SqliteUnitOfWorkFactory(database)
    tool_policy = ToolPolicy(
        default_tool_catalog(),
        allowed_permissions=frozenset({ToolPermission.READ_ONLY}),
    )
    controller = FactoryController(
        uow_factory=uow_factory,
        clock=clock,
        id_generator=id_generator,
        correlation_context=correlation_context,
        prototype_policy=PrototypePolicy(),
        promotion_policy=PromotionPolicy(),
        degradation_policy=DegradationPolicy(),
        lifecycle_policy=LifecyclePolicy(),
        knowledge_policy=KnowledgeBindingPolicy(),
        tool_policy=tool_policy,
        spec_builder=AgentSpecBuilder(),
        evaluation_engine=DeterministicRuleEngine(),
        idempotency=IdempotencyService(ttl_seconds=86_400),
        audit_factory=AuditEventFactory(id_generator),
        max_inline_knowledge_bytes=262_144,
    )
    return _AuditRuntime(controller, uow_factory, correlation_context)


async def _prepare_fixture(
    controller: FactoryController,
    experiment_id: str,
) -> AuditFixtureIdentity:
    suite = await controller.register_evaluation_suite(
        RegisterEvaluationSuiteCommand(
            suite=_evaluation_suite(),
            actor=_ACTOR,
        )
    )
    suite_ref = _suite_ref(suite)
    tree = await controller.register_skill_tree(
        RegisterSkillTreeCommand(
            tree=SkillTreeDraft(
                tree_id="h5-writer-skills",
                version="1.0.0",
                nodes=(
                    SkillNode(
                        node_id="audited-writer",
                        display_name="Audited Writer",
                        prompt_appendix="Cite the bound governance guide.",
                        evaluation_suite=suite_ref,
                    ),
                ),
            ),
            actor=_ACTOR,
        )
    )
    slot = KnowledgeSlot(
        name="governance-guide",
        required=True,
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        min_version="1.0.0",
        injection_mode=InjectionMode.RETRIEVAL,
    )
    prototype = await controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="h5-writer-agent",
            version="1.0.0",
            definition=AgentDefinition(
                agent_type="writer-agent",
                role="Technical Writer",
                system_prompt="Write only from governed source material.",
                capabilities=frozenset({Capability.WRITE}),
                output_schema={"type": "object"},
                knowledge_slots=(slot,),
            ),
            skill_tree=_tree_ref(tree),
            publish=True,
            actor=_ACTOR,
        )
    )
    content = "Agent provenance includes prototype, knowledge, spec, and skill history."
    knowledge = await controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=DomainKnowledgeDraft(
                knowledge_id="h5-governance-guide",
                version="1.0.0",
                name="H5 Governance Guide",
                kind=KnowledgeKind.DOCUMENT,
                content=content,
                checksum=checksum_knowledge_content(content),
            ),
            actor=_ACTOR,
        )
    )
    instance = await controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            actor=_ACTOR,
        )
    )
    bound = await controller.bind_knowledge(
        BindKnowledgeCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            selections=(
                KnowledgeSelection(
                    slot_name=slot.name,
                    knowledge_id=knowledge.knowledge_id,
                    version=knowledge.version,
                ),
            ),
            actor=_ACTOR,
        )
    )
    spec = await controller.export_spec(bound.instance_id, actor=_ACTOR)
    report = await controller.evaluate_instance(
        EvaluateInstanceCommand(
            submission=EvaluationSubmission(
                instance_id=bound.instance_id,
                instance_revision=bound.revision,
                suite=suite_ref,
                runtime_model="deterministic-rule-engine",
                case_results=(
                    SubmittedCaseResult(
                        case_id="trace-origin",
                        output_text="Use the bound provenance guide.",
                    ),
                ),
            ),
            actor=_ACTOR,
        )
    )
    promoted = await controller.promote_agent(
        PromoteAgentCommand(
            instance_id=bound.instance_id,
            expected_revision=bound.revision,
            target_node_id="audited-writer",
            evaluation_report_id=report.report_id,
            actor=_ACTOR,
        )
    )
    return AuditFixtureIdentity(
        experiment_id=experiment_id,
        prototype_id=prototype.prototype_id,
        prototype_version=prototype.version,
        prototype_checksum=prototype.checksum,
        knowledge_id=knowledge.knowledge_id,
        knowledge_version=knowledge.version,
        knowledge_checksum=knowledge.checksum,
        instance_id=instance.instance_id,
        bound_revision=bound.revision,
        promoted_revision=promoted.revision,
        agent_spec_checksum=spec.spec_checksum,
        evaluation_report_id=report.report_id,
        skill_tree=_tree_ref(tree),
        evaluation_suite=suite_ref,
        target_node_id="audited-writer",
    )


def _evaluation_suite() -> EvaluationSuiteDraft:
    return EvaluationSuiteDraft(
        suite_id="h5-origin-readiness",
        version="1.0.0",
        rules=(
            EvaluationRule(
                rule_id="mentions-provenance",
                kind=RuleKind.REQUIRED_TERMS,
                parameters={"terms": ("provenance",)},
            ),
        ),
        cases=(
            EvaluationCase(
                case_id="trace-origin",
                input="Explain how this Agent's origin can be traced.",
            ),
        ),
    )


def _suite_ref(suite: EvaluationSuite) -> EvaluationSuiteRef:
    return EvaluationSuiteRef(
        suite_id=suite.suite_id,
        version=suite.version,
        checksum=suite.checksum,
    )


def _tree_ref(tree: SkillTree) -> SkillTreeRef:
    return SkillTreeRef(
        tree_id=tree.tree_id,
        version=tree.version,
        checksum=tree.checksum,
    )


def _matching_events(
    events: tuple[AuditEvent, ...],
    event_type: AuditEventType,
    entity_id: str,
) -> tuple[AuditEvent, ...]:
    return tuple(
        event
        for event in events
        if event.event_type is event_type and event.entity_id == entity_id
    )


def _check_step(
    *,
    step_id: str,
    event_type: AuditEventType,
    entity_id: str,
    events: tuple[AuditEvent, ...],
    predicate: Callable[[AuditEvent], bool],
) -> AuditStepResult:
    matching = _matching_events(events, event_type, entity_id)
    if len(matching) != 1:
        return AuditStepResult(
            step_id=step_id,
            expected_event_type=event_type,
            passed=False,
            reason=f"expected exactly one event, found {len(matching)}",
        )
    event = matching[0]
    if not predicate(event):
        return AuditStepResult(
            step_id=step_id,
            expected_event_type=event_type,
            passed=False,
            reason="event does not match persisted source identities",
        )
    return AuditStepResult(
        step_id=step_id,
        expected_event_type=event_type,
        matched_event_id=event.event_id,
        passed=True,
    )
