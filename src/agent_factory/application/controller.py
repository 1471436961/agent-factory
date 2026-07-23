"""Deterministic application service for Agent production and governance."""

from datetime import datetime
from uuid import UUID

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    DeprecatePrototypeCommand,
    EvaluateInstanceCommand,
    KnowledgeSelection,
    PromoteAgentCommand,
    PublishPrototypeCommand,
    RecordTaskOutcomeCommand,
    RegisterEvaluationSuiteCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    RegisterSkillTreeCommand,
    ReviewEvaluationCommand,
    TransitionInstanceCommand,
)
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.ports import (
    Clock,
    CorrelationContext,
    EvaluationEngine,
    IdGenerator,
)
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.application.repositories import (
    EvaluationReportRepository,
    EvaluationSuiteRepository,
    InstanceRepository,
    PrototypeRepository,
    SkillTreeRepository,
)
from agent_factory.application.tooling import ToolPolicy
from agent_factory.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import (
    canonical_json_bytes,
    checksum_knowledge_content,
    semver_tuple,
    sha256_model,
)
from agent_factory.domain.enums import (
    EvaluationDecision,
    InstanceStatus,
    PrototypeStatus,
)
from agent_factory.domain.errors import (
    EvaluationReportNotFoundError,
    EvaluationReviewConflictError,
    EvaluationReviewNotRequiredError,
    EvaluationSuiteMismatchError,
    EvaluationSuiteNotFoundError,
    InstanceBusyError,
    InstanceNotFoundError,
    InvalidPrototypeStatusError,
    InvalidStateTransitionError,
    KnowledgeAlreadyBoundError,
    KnowledgeChecksumMismatchError,
    KnowledgeInjectionModeMismatchError,
    KnowledgePayloadTooLargeError,
    PromotionRejectedError,
    PrototypeNotFoundError,
    PrototypeNotPublishedError,
    RepositoryUnavailableError,
    RevisionConflictError,
    SkillTreeNotBoundError,
    SkillTreeNotFoundError,
    StaleEvaluationReportError,
)
from agent_factory.domain.evaluation import (
    EvaluationReport,
    EvaluationReview,
    EvaluationSuite,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    KnowledgeBinding,
    PrototypeRef,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.degradation import DegradationPolicy
from agent_factory.domain.services.evaluation import checksum_evaluation_suite
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.lifecycle import LifecyclePolicy
from agent_factory.domain.services.promotion import PromotionPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.skills import (
    apply_skill_nodes,
    checksum_skill_tree,
    descendants_of,
)
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.domain.skills import DegradationCheckResult, SkillTree, TaskOutcome

REGISTER_PROTOTYPE = "register-prototype"
PUBLISH_PROTOTYPE = "publish-prototype"
DEPRECATE_PROTOTYPE = "deprecate-prototype"
REGISTER_KNOWLEDGE = "register-knowledge"
CLONE_AGENT = "clone-agent"
BIND_KNOWLEDGE = "bind-knowledge"
REGISTER_EVALUATION_SUITE = "register-evaluation-suite"
REGISTER_SKILL_TREE = "register-skill-tree"
EVALUATE_INSTANCE = "evaluate-instance"
REVIEW_EVALUATION = "review-evaluation"
PROMOTE_AGENT = "promote-agent"
RECORD_TASK_OUTCOME = "record-task-outcome"
TRANSITION_INSTANCE = "transition-instance"


class FactoryController:
    """Coordinate pure policies and persistence under explicit transactions."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        id_generator: IdGenerator,
        correlation_context: CorrelationContext,
        prototype_policy: PrototypePolicy,
        promotion_policy: PromotionPolicy,
        degradation_policy: DegradationPolicy,
        lifecycle_policy: LifecyclePolicy,
        knowledge_policy: KnowledgeBindingPolicy,
        tool_policy: ToolPolicy,
        spec_builder: AgentSpecBuilder,
        evaluation_engine: EvaluationEngine,
        idempotency: IdempotencyService,
        audit_factory: AuditEventFactory,
        max_inline_knowledge_bytes: int,
    ) -> None:
        if max_inline_knowledge_bytes < 1:
            raise ValueError("max_inline_knowledge_bytes must be positive")
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._correlation_context = correlation_context
        self._prototype_policy = prototype_policy
        self._promotion_policy = promotion_policy
        self._degradation_policy = degradation_policy
        self._lifecycle_policy = lifecycle_policy
        self._knowledge_policy = knowledge_policy
        self._tool_policy = tool_policy
        self._spec_builder = spec_builder
        self._evaluation_engine = evaluation_engine
        self._idempotency = idempotency
        self._audit_factory = audit_factory
        self._max_inline_knowledge_bytes = max_inline_knowledge_bytes

    async def register_prototype(
        self,
        command: RegisterPrototypeCommand,
    ) -> AgentPrototype:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_PROTOTYPE,
                response_type=AgentPrototype,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            self._prototype_policy.validate_definition(command.definition)
            self._tool_policy.resolve(command.definition.tools)
            if command.skill_tree is not None:
                await self._require_skill_tree_ref(
                    uow.skill_trees,
                    command.skill_tree,
                )
            prototype = AgentPrototype(
                prototype_id=command.prototype_id,
                version=command.version,
                definition=command.definition,
                skill_tree=command.skill_tree,
                checksum=self._definition_checksum(command),
                created_at=now,
                created_by=command.actor,
            )
            if command.publish:
                prototype = self._prototype_policy.publish(prototype, at=now)

            await uow.prototypes.add(prototype)
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.prototype_registered(
                    prototype,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            if command.publish:
                await uow.audit.append(
                    self._audit_factory.prototype_published(
                        prototype,
                        actor=command.actor,
                        correlation_id=correlation_id,
                        at=now,
                    )
                )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_PROTOTYPE,
                response=prototype,
                now=now,
            )
            await uow.commit()
            return prototype

    async def list_prototypes(
        self,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]:
        async with self._uow_factory(read_only=True) as uow:
            return await uow.prototypes.list(query)

    async def publish_prototype(
        self,
        command: PublishPrototypeCommand,
    ) -> AgentPrototype:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=PUBLISH_PROTOTYPE,
                response_type=AgentPrototype,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay
            current = await self._require_prototype(
                uow.prototypes,
                command.prototype_id,
                command.version,
            )
            published = self._prototype_policy.publish(current, at=now)
            if not await uow.prototypes.replace(
                published,
                PrototypeStatus.DRAFT,
            ):
                raise InvalidPrototypeStatusError(
                    details={
                        "prototype_id": command.prototype_id,
                        "version": command.version,
                        "reason": "concurrent-status-change",
                    }
                )
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.prototype_published(
                    published,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=PUBLISH_PROTOTYPE,
                response=published,
                now=now,
            )
            await uow.commit()
            return published

    async def deprecate_prototype(
        self,
        command: DeprecatePrototypeCommand,
    ) -> AgentPrototype:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=DEPRECATE_PROTOTYPE,
                response_type=AgentPrototype,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay
            current = await self._require_prototype(
                uow.prototypes,
                command.prototype_id,
                command.version,
            )
            deprecated = self._prototype_policy.deprecate(
                current,
                reason=command.reason,
            )
            if not await uow.prototypes.replace(
                deprecated,
                PrototypeStatus.PUBLISHED,
            ):
                raise InvalidPrototypeStatusError(
                    details={
                        "prototype_id": command.prototype_id,
                        "version": command.version,
                        "reason": "concurrent-status-change",
                    }
                )
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.prototype_deprecated(
                    deprecated,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=DEPRECATE_PROTOTYPE,
                response=deprecated,
                now=now,
            )
            await uow.commit()
            return deprecated

    async def register_knowledge(
        self,
        command: RegisterKnowledgeCommand,
    ) -> DomainKnowledge:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_KNOWLEDGE,
                response_type=DomainKnowledge,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay
            self._validate_knowledge_payload(command)
            knowledge = DomainKnowledge.model_validate(
                {
                    **command.knowledge.model_dump(mode="python"),
                    "created_at": now,
                    "created_by": command.actor,
                }
            )
            await uow.knowledge.add(knowledge)
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.knowledge_registered(
                    knowledge,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_KNOWLEDGE,
                response=knowledge,
                now=now,
            )
            await uow.commit()
            return knowledge

    async def clone_agent(self, command: CloneAgentCommand) -> AgentInstance:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=CLONE_AGENT,
                response_type=AgentInstance,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay
            prototype = await self._require_prototype(
                uow.prototypes,
                command.prototype_id,
                command.prototype_version,
            )
            if prototype.status is not PrototypeStatus.PUBLISHED:
                raise PrototypeNotPublishedError(
                    details={
                        "prototype_id": prototype.prototype_id,
                        "version": prototype.version,
                        "status": prototype.status.value,
                    }
                )
            self._tool_policy.resolve(prototype.definition.tools)
            instance = AgentInstance(
                instance_id=self._id_generator.new(),
                prototype=PrototypeRef(
                    prototype_id=prototype.prototype_id,
                    version=prototype.version,
                    checksum=prototype.checksum,
                ),
                revision=1,
                status=InstanceStatus.CREATED,
                configuration=prototype.definition,
                skill_tree=prototype.skill_tree,
                runtime_target=command.runtime_target,
                created_at=now,
                updated_at=now,
                created_by=command.actor,
            )
            await uow.instances.add(instance)
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.instance_cloned(
                    instance,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=CLONE_AGENT,
                response=instance,
                now=now,
            )
            await uow.commit()
            return instance

    async def bind_knowledge(
        self,
        command: BindKnowledgeCommand,
    ) -> AgentInstance:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=BIND_KNOWLEDGE,
                response_type=AgentInstance,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay
            current = await uow.instances.get(command.instance_id)
            if current is None:
                raise InstanceNotFoundError(
                    details={"instance_id": str(command.instance_id)}
                )
            self._validate_bind_state(current, command.expected_revision)

            touched_slots = {item.slot_name for item in command.selections}
            replaced_slots = {
                binding.slot_name
                for binding in current.knowledge_bindings
                if binding.slot_name in touched_slots
            }
            if replaced_slots and not command.replace_existing:
                raise KnowledgeAlreadyBoundError(
                    details={"slot_names": sorted(replaced_slots)}
                )
            retained = tuple(
                binding
                for binding in current.knowledge_bindings
                if not command.replace_existing
                or binding.slot_name not in touched_slots
            )
            final_selections = self._binding_selections(retained) + command.selections
            packages = await uow.knowledge.get_many(
                tuple(
                    (selection.knowledge_id, selection.version)
                    for selection in final_selections
                )
            )
            validated = self._knowledge_policy.validate_and_build(
                definition=current.configuration,
                selections=final_selections,
                packages=packages,
                bound_at=now,
                bound_by=command.actor,
            )
            validated_by_ref = {
                (
                    binding.slot_name,
                    binding.knowledge_id,
                    binding.knowledge_version,
                ): binding
                for binding in validated
            }
            new_bindings = tuple(
                validated_by_ref[
                    (selection.slot_name, selection.knowledge_id, selection.version)
                ]
                for selection in command.selections
            )
            bindings = tuple(
                sorted(
                    (*retained, *new_bindings),
                    key=lambda binding: (
                        binding.slot_name,
                        binding.knowledge_id,
                        semver_tuple(binding.knowledge_version),
                    ),
                )
            )
            updated = AgentInstance.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "revision": current.revision + 1,
                    "knowledge_bindings": bindings,
                    "updated_at": now,
                }
            )
            await uow.instances.save_snapshot(
                updated,
                expected_revision=current.revision,
            )
            correlation_id = self._correlation_id()
            bindings_by_ref = {
                (
                    binding.slot_name,
                    binding.knowledge_id,
                    binding.knowledge_version,
                ): binding
                for binding in bindings
            }
            for selection in command.selections:
                binding = bindings_by_ref[
                    (selection.slot_name, selection.knowledge_id, selection.version)
                ]
                await uow.audit.append(
                    self._audit_factory.knowledge_bound(
                        updated,
                        binding,
                        replaced=selection.slot_name in replaced_slots,
                        actor=command.actor,
                        correlation_id=correlation_id,
                        at=now,
                    )
                )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=BIND_KNOWLEDGE,
                response=updated,
                now=now,
            )
            await uow.commit()
            return updated

    async def transition_instance(
        self,
        command: TransitionInstanceCommand,
    ) -> AgentInstance:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=TRANSITION_INSTANCE,
                response_type=AgentInstance,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            current = await self._require_instance(
                uow.instances,
                command.instance_id,
                None,
            )
            if current.revision != command.expected_revision:
                raise RevisionConflictError(
                    details={
                        "instance_id": str(current.instance_id),
                        "expected_revision": command.expected_revision,
                        "actual_revision": current.revision,
                    }
                )
            transitioned = self._lifecycle_policy.transition(
                current,
                command.target_status,
                reason=command.reason,
                retry=command.retry,
                now=now,
            )
            if command.target_status is InstanceStatus.RUNNING:
                await self._validate_runtime_readiness(uow, current, now=now)

            await uow.instances.save_snapshot(
                transitioned,
                expected_revision=current.revision,
            )
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.instance_transitioned(
                    current,
                    transitioned,
                    reason=command.reason,
                    retry=command.retry,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=TRANSITION_INSTANCE,
                response=transitioned,
                now=now,
            )
            await uow.commit()
            return transitioned

    async def register_evaluation_suite(
        self,
        command: RegisterEvaluationSuiteCommand,
    ) -> EvaluationSuite:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_EVALUATION_SUITE,
                response_type=EvaluationSuite,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            suite = EvaluationSuite.model_validate(
                {
                    **command.suite.model_dump(mode="python"),
                    "checksum": checksum_evaluation_suite(command.suite),
                    "created_at": now,
                    "created_by": command.actor,
                }
            )
            await uow.evaluation_suites.add(suite)
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.evaluation_suite_registered(
                    suite,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_EVALUATION_SUITE,
                response=suite,
                now=now,
            )
            await uow.commit()
            return suite

    async def get_evaluation_suite(
        self,
        suite_id: str,
        version: str,
    ) -> EvaluationSuite:
        async with self._uow_factory(read_only=True) as uow:
            return await self._require_evaluation_suite(
                uow.evaluation_suites,
                suite_id,
                version,
            )

    async def register_skill_tree(
        self,
        command: RegisterSkillTreeCommand,
    ) -> SkillTree:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_SKILL_TREE,
                response_type=SkillTree,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            suite_refs = {node.evaluation_suite for node in command.tree.nodes}
            for suite_ref in sorted(
                suite_refs,
                key=lambda ref: (ref.suite_id, semver_tuple(ref.version)),
            ):
                await self._require_evaluation_suite_ref(
                    uow.evaluation_suites,
                    suite_ref,
                )
            tree = SkillTree.model_validate(
                {
                    **command.tree.model_dump(mode="python"),
                    "checksum": checksum_skill_tree(command.tree),
                    "created_at": now,
                    "created_by": command.actor,
                }
            )
            await uow.skill_trees.add(tree)
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.skill_tree_registered(
                    tree,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=REGISTER_SKILL_TREE,
                response=tree,
                now=now,
            )
            await uow.commit()
            return tree

    async def get_skill_tree(
        self,
        tree_id: str,
        version: str,
    ) -> SkillTree:
        async with self._uow_factory(read_only=True) as uow:
            return await self._require_skill_tree(
                uow.skill_trees,
                tree_id,
                version,
            )

    async def evaluate_instance(
        self,
        command: EvaluateInstanceCommand,
    ) -> EvaluationReport:
        started_at = self._clock.now()
        if command.idempotency_key is not None:
            async with self._uow_factory() as uow:
                replay = await self._idempotency.replay(
                    repository=uow.idempotency,
                    command=command,
                    operation=EVALUATE_INSTANCE,
                    response_type=EvaluationReport,
                    now=started_at,
                )
                await uow.commit()
                if replay is not None:
                    return replay

        submission = command.submission
        async with self._uow_factory(read_only=True) as uow:
            instance = await self._require_instance(
                uow.instances,
                submission.instance_id,
                submission.instance_revision,
            )
            if instance.skill_tree is None:
                raise SkillTreeNotBoundError(
                    details={
                        "instance_id": str(instance.instance_id),
                        "revision": instance.revision,
                    }
                )
            tree = await self._require_skill_tree_ref(
                uow.skill_trees,
                instance.skill_tree,
            )
            suite = await self._require_evaluation_suite_ref(
                uow.evaluation_suites,
                submission.suite,
            )
            if all(node.evaluation_suite != submission.suite for node in tree.nodes):
                raise EvaluationSuiteMismatchError(
                    details={
                        "tree": instance.skill_tree.model_dump(mode="json"),
                        "suite": submission.suite.model_dump(mode="json"),
                        "reason": "suite-not-referenced-by-tree",
                    }
                )
            spec = await uow.specs.get(instance.instance_id, instance.revision)
            if spec is None:
                await self._revalidate_bindings(uow, instance, now=started_at)
                tools = self._tool_policy.resolve(instance.configuration.tools)
                spec = self._spec_builder.build(
                    instance=instance,
                    tools=tools,
                    generated_at=started_at,
                )
            else:
                self._validate_spec_source(spec, instance)

        outcome = self._evaluation_engine.evaluate(
            suite=suite,
            submission=submission,
        )
        completed_at = self._clock.now()
        correlation_id = self._correlation_id()

        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=EVALUATE_INSTANCE,
                response_type=EvaluationReport,
                now=completed_at,
            )
            if replay is not None:
                await uow.commit()
                return replay

            persisted_instance = await self._require_instance(
                uow.instances,
                submission.instance_id,
                submission.instance_revision,
            )
            persisted_spec = await uow.specs.get(
                persisted_instance.instance_id,
                persisted_instance.revision,
            )
            if persisted_spec is None:
                if not await uow.specs.add_if_absent(spec):
                    persisted_spec = await uow.specs.get(
                        persisted_instance.instance_id,
                        persisted_instance.revision,
                    )
                    if persisted_spec is None:
                        raise RepositoryUnavailableError(
                            details={
                                "repository": "agent-specs",
                                "reason": "insert-conflict-without-row",
                            }
                        )
                else:
                    persisted_spec = spec
                    await uow.audit.append(
                        self._audit_factory.spec_exported(
                            persisted_spec,
                            actor=command.actor,
                            correlation_id=correlation_id,
                            at=completed_at,
                        )
                    )
            self._validate_spec_source(persisted_spec, persisted_instance)
            if persisted_instance.skill_tree is None:
                raise SkillTreeNotBoundError(
                    details={
                        "instance_id": str(persisted_instance.instance_id),
                        "revision": persisted_instance.revision,
                    }
                )
            report = EvaluationReport.model_validate(
                {
                    **outcome.model_dump(mode="python"),
                    "report_id": self._id_generator.new(),
                    "instance_id": persisted_instance.instance_id,
                    "instance_revision": persisted_instance.revision,
                    "agent_spec_checksum": persisted_spec.spec_checksum,
                    "skill_tree": persisted_instance.skill_tree,
                    "suite": self._evaluation_suite_ref(suite),
                    "runtime_model": submission.runtime_model,
                    "started_at": started_at,
                    "completed_at": completed_at,
                }
            )
            await uow.evaluation_reports.add(report)
            await uow.audit.append(
                self._audit_factory.evaluation_completed(
                    report,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=completed_at,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=EVALUATE_INSTANCE,
                response=report,
                now=completed_at,
            )
            await uow.commit()
            return report

    async def review_evaluation(
        self,
        command: ReviewEvaluationCommand,
    ) -> EvaluationReview:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=REVIEW_EVALUATION,
                response_type=EvaluationReview,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            report = await self._require_evaluation_report(
                uow.evaluation_reports,
                command.report_id,
            )
            if report.decision is not EvaluationDecision.REVIEW_REQUIRED:
                raise EvaluationReviewNotRequiredError(
                    details={
                        "report_id": str(report.report_id),
                        "decision": report.decision.value,
                    }
                )
            existing = await uow.evaluation_reviews.get_for_report(report.report_id)
            if existing is not None:
                raise EvaluationReviewConflictError(
                    details={
                        "report_id": str(report.report_id),
                        "review_id": str(existing.review_id),
                    }
                )
            review = EvaluationReview(
                review_id=self._id_generator.new(),
                report_id=report.report_id,
                reviewer=command.actor,
                decision=command.decision,
                comment=command.comment,
                reviewed_at=now,
            )
            await uow.evaluation_reviews.add(review)
            await uow.audit.append(
                self._audit_factory.evaluation_reviewed(
                    review,
                    actor=command.actor,
                    correlation_id=self._correlation_id(),
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=REVIEW_EVALUATION,
                response=review,
                now=now,
            )
            await uow.commit()
            return review

    async def promote_agent(
        self,
        command: PromoteAgentCommand,
    ) -> AgentInstance:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=PROMOTE_AGENT,
                response_type=AgentInstance,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            current = await self._require_instance(
                uow.instances,
                command.instance_id,
                None,
            )
            if current.revision != command.expected_revision:
                raise RevisionConflictError(
                    details={
                        "instance_id": str(current.instance_id),
                        "expected_revision": command.expected_revision,
                        "actual_revision": current.revision,
                    }
                )
            if current.skill_tree is None:
                raise SkillTreeNotBoundError(
                    details={"instance_id": str(current.instance_id)}
                )
            tree = await self._require_skill_tree_ref(
                uow.skill_trees,
                current.skill_tree,
            )
            prototype = await self._require_prototype(
                uow.prototypes,
                current.prototype.prototype_id,
                current.prototype.version,
            )
            self._validate_prototype_source(prototype, current)
            expected_current_configuration = apply_skill_nodes(
                base=prototype.definition,
                tree=tree,
                active_node_ids=current.active_skill_nodes,
            )
            if current.configuration != expected_current_configuration:
                raise RepositoryUnavailableError(
                    details={
                        "repository": "instances",
                        "reason": "configuration-source-mismatch",
                        "instance_id": str(current.instance_id),
                        "revision": current.revision,
                    }
                )

            report = await self._require_evaluation_report(
                uow.evaluation_reports,
                command.evaluation_report_id,
            )
            if (
                report.instance_id != current.instance_id
                or report.instance_revision != current.revision
                or report.skill_tree != current.skill_tree
            ):
                raise StaleEvaluationReportError(
                    details={
                        "report_id": str(report.report_id),
                        "report_revision": report.instance_revision,
                        "instance_revision": current.revision,
                    }
                )
            spec = await uow.specs.get(current.instance_id, current.revision)
            if spec is None:
                raise RepositoryUnavailableError(
                    details={
                        "repository": "agent-specs",
                        "reason": "report-source-spec-missing",
                        "instance_id": str(current.instance_id),
                        "revision": current.revision,
                    }
                )
            self._validate_spec_source(spec, current)
            review = None
            if command.evaluation_review_id is not None:
                review = await uow.evaluation_reviews.get(command.evaluation_review_id)
                if review is None:
                    raise PromotionRejectedError(
                        details={
                            "report_id": str(report.report_id),
                            "review_id": str(command.evaluation_review_id),
                            "reason": "review-not-found",
                        }
                    )

            target = self._promotion_policy.validate(
                instance=current,
                spec=spec,
                tree=tree,
                target_node_id=command.target_node_id,
                report=report,
                review=review,
            )
            active_nodes = frozenset((*current.active_skill_nodes, target.node_id))
            configuration = apply_skill_nodes(
                base=prototype.definition,
                tree=tree,
                active_node_ids=active_nodes,
            )
            self._tool_policy.resolve(configuration.tools)
            bindings = await self._build_promotion_bindings(
                uow,
                current=current,
                configuration=configuration,
                selections=command.knowledge_selections,
                actor=command.actor,
                now=now,
            )
            promoted = AgentInstance.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "revision": current.revision + 1,
                    "configuration": configuration,
                    "knowledge_bindings": bindings,
                    "active_skill_nodes": active_nodes,
                    "updated_at": now,
                }
            )
            await uow.instances.save_snapshot(
                promoted,
                expected_revision=current.revision,
            )
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.skill_promoted(
                    current,
                    promoted,
                    node_id=target.node_id,
                    report_id=report.report_id,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=PROMOTE_AGENT,
                response=promoted,
                now=now,
            )
            await uow.commit()
            return promoted

    async def record_task_outcome(
        self,
        command: RecordTaskOutcomeCommand,
    ) -> DegradationCheckResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            replay = await self._idempotency.replay(
                repository=uow.idempotency,
                command=command,
                operation=RECORD_TASK_OUTCOME,
                response_type=DegradationCheckResult,
                now=now,
            )
            if replay is not None:
                await uow.commit()
                return replay

            current = await self._require_instance(
                uow.instances,
                command.instance_id,
                None,
            )
            if current.revision != command.expected_revision:
                raise RevisionConflictError(
                    details={
                        "instance_id": str(current.instance_id),
                        "expected_revision": command.expected_revision,
                        "actual_revision": current.revision,
                    }
                )
            if current.skill_tree is None:
                raise SkillTreeNotBoundError(
                    details={"instance_id": str(current.instance_id)}
                )
            tree = await self._require_skill_tree_ref(
                uow.skill_trees,
                current.skill_tree,
            )
            prototype = await self._require_prototype(
                uow.prototypes,
                current.prototype.prototype_id,
                current.prototype.version,
            )
            self._validate_prototype_source(prototype, current)
            expected_configuration = apply_skill_nodes(
                base=prototype.definition,
                tree=tree,
                active_node_ids=current.active_skill_nodes,
            )
            if current.configuration != expected_configuration:
                raise RepositoryUnavailableError(
                    details={
                        "repository": "instances",
                        "reason": "configuration-source-mismatch",
                        "instance_id": str(current.instance_id),
                        "revision": current.revision,
                    }
                )

            report = await self._require_evaluation_report(
                uow.evaluation_reports,
                command.evaluation_report_id,
            )
            spec = await uow.specs.get(current.instance_id, current.revision)
            if spec is None:
                raise RepositoryUnavailableError(
                    details={
                        "repository": "agent-specs",
                        "reason": "report-source-spec-missing",
                        "instance_id": str(current.instance_id),
                        "revision": current.revision,
                    }
                )
            self._validate_spec_source(spec, current)
            review = await uow.evaluation_reviews.get_for_report(report.report_id)
            target = self._degradation_policy.validate_observation(
                instance=current,
                spec=spec,
                tree=tree,
                skill_node_id=command.skill_node_id,
                report=report,
                review=review,
                passed=command.passed,
            )
            outcome = TaskOutcome(
                task_id=command.task_id,
                skill_node_id=target.node_id,
                passed=command.passed,
                evaluation_report_id=report.report_id,
                recorded_at=now,
            )
            await uow.task_outcomes.append(
                instance_id=current.instance_id,
                instance_revision=current.revision,
                outcome=outcome,
            )
            window = await uow.task_outcomes.list_for_node(
                instance_id=current.instance_id,
                instance_revision=current.revision,
                skill_node_id=target.node_id,
                limit=target.observation_policy.window_size,
            )
            decision = self._degradation_policy.evaluate(
                window,
                target.observation_policy,
            )
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.task_outcome_recorded(
                    current,
                    outcome,
                    decision,
                    actor=command.actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )

            result = DegradationCheckResult(
                instance_id=current.instance_id,
                checked_revision=current.revision,
                degraded=False,
                resulting_revision=current.revision,
            )
            if decision.should_degrade:
                removal_scope = frozenset(
                    {target.node_id, *descendants_of(tree, target.node_id)}
                )
                removed_nodes = current.active_skill_nodes & removal_scope
                active_nodes = current.active_skill_nodes - removed_nodes
                configuration = apply_skill_nodes(
                    base=prototype.definition,
                    tree=tree,
                    active_node_ids=active_nodes,
                )
                self._tool_policy.resolve(configuration.tools)
                bindings, removed_binding_slots = await self._build_degraded_bindings(
                    uow,
                    current=current,
                    configuration=configuration,
                    now=now,
                )
                degraded = AgentInstance.model_validate(
                    {
                        **current.model_dump(mode="python"),
                        "revision": current.revision + 1,
                        "status": InstanceStatus.DEGRADED,
                        "configuration": configuration,
                        "knowledge_bindings": bindings,
                        "active_skill_nodes": active_nodes,
                        "updated_at": now,
                    }
                )
                await uow.instances.save_snapshot(
                    degraded,
                    expected_revision=current.revision,
                )
                result = DegradationCheckResult(
                    instance_id=current.instance_id,
                    checked_revision=current.revision,
                    degraded=True,
                    resulting_revision=degraded.revision,
                    removed_nodes=removed_nodes,
                    removed_binding_slots=removed_binding_slots,
                )
                await uow.audit.append(
                    self._audit_factory.skill_degraded(
                        current,
                        degraded,
                        decision,
                        node_id=target.node_id,
                        removed_nodes=removed_nodes,
                        removed_binding_slots=removed_binding_slots,
                        actor=command.actor,
                        correlation_id=correlation_id,
                        at=now,
                    )
                )

            await self._idempotency.store(
                repository=uow.idempotency,
                command=command,
                operation=RECORD_TASK_OUTCOME,
                response=result,
                now=now,
            )
            await uow.commit()
            return result

    async def export_spec(
        self,
        instance_id: UUID,
        *,
        revision: int | None = None,
        actor: str,
    ) -> AgentSpec:
        if revision is not None and revision < 1:
            raise ValueError("revision must be positive")
        async with self._uow_factory(read_only=True) as uow:
            instance = await self._require_instance(
                uow.instances, instance_id, revision
            )
            existing = await uow.specs.get(instance_id, instance.revision)
            if existing is not None:
                return existing

        now = self._clock.now()
        async with self._uow_factory() as uow:
            instance = await self._require_instance(
                uow.instances, instance_id, revision
            )
            existing = await uow.specs.get(instance_id, instance.revision)
            if existing is not None:
                return existing
            await self._revalidate_bindings(uow, instance, now=now)
            tools = self._tool_policy.resolve(instance.configuration.tools)
            spec = self._spec_builder.build(
                instance=instance,
                tools=tools,
                generated_at=now,
            )
            if not await uow.specs.add_if_absent(spec):
                concurrent = await uow.specs.get(instance_id, instance.revision)
                if concurrent is None:
                    raise RepositoryUnavailableError(
                        details={
                            "repository": "agent-specs",
                            "reason": "insert-conflict-without-row",
                        }
                    )
                return concurrent
            correlation_id = self._correlation_id()
            await uow.audit.append(
                self._audit_factory.spec_exported(
                    spec,
                    actor=actor,
                    correlation_id=correlation_id,
                    at=now,
                )
            )
            await uow.commit()
            return spec

    async def query_audit(self, query: AuditQuery) -> Page[AuditEvent]:
        async with self._uow_factory(read_only=True) as uow:
            return await uow.audit.query(query)

    @staticmethod
    async def _require_prototype(
        repository: PrototypeRepository,
        prototype_id: str,
        version: str,
    ) -> AgentPrototype:
        prototype = await repository.get(prototype_id, version)
        if prototype is None:
            raise PrototypeNotFoundError(
                details={"prototype_id": prototype_id, "version": version}
            )
        return prototype

    @staticmethod
    async def _require_instance(
        repository: InstanceRepository,
        instance_id: UUID,
        revision: int | None,
    ) -> AgentInstance:
        instance = await repository.get(instance_id, revision)
        if instance is None:
            raise InstanceNotFoundError(
                details={
                    "instance_id": str(instance_id),
                    "revision": revision,
                }
            )
        return instance

    @staticmethod
    async def _require_evaluation_suite(
        repository: EvaluationSuiteRepository,
        suite_id: str,
        version: str,
    ) -> EvaluationSuite:
        suite = await repository.get(suite_id, version)
        if suite is None:
            raise EvaluationSuiteNotFoundError(
                details={"suite_id": suite_id, "version": version}
            )
        return suite

    @classmethod
    async def _require_evaluation_suite_ref(
        cls,
        repository: EvaluationSuiteRepository,
        reference: EvaluationSuiteRef,
    ) -> EvaluationSuite:
        suite = await cls._require_evaluation_suite(
            repository,
            reference.suite_id,
            reference.version,
        )
        expected = cls._evaluation_suite_ref(suite)
        if reference != expected:
            raise EvaluationSuiteMismatchError(
                details={
                    "expected": expected.model_dump(mode="json"),
                    "actual": reference.model_dump(mode="json"),
                }
            )
        return suite

    @staticmethod
    async def _require_skill_tree(
        repository: SkillTreeRepository,
        tree_id: str,
        version: str,
    ) -> SkillTree:
        tree = await repository.get(tree_id, version)
        if tree is None:
            raise SkillTreeNotFoundError(
                details={"tree_id": tree_id, "version": version}
            )
        return tree

    @classmethod
    async def _require_skill_tree_ref(
        cls,
        repository: SkillTreeRepository,
        reference: SkillTreeRef,
    ) -> SkillTree:
        tree = await cls._require_skill_tree(
            repository,
            reference.tree_id,
            reference.version,
        )
        expected = cls._skill_tree_ref(tree)
        if reference != expected:
            raise SkillTreeNotFoundError(
                details={
                    "expected": expected.model_dump(mode="json"),
                    "actual": reference.model_dump(mode="json"),
                    "reason": "reference-mismatch",
                },
            )
        return tree

    @staticmethod
    async def _require_evaluation_report(
        repository: EvaluationReportRepository,
        report_id: UUID,
    ) -> EvaluationReport:
        report = await repository.get(report_id)
        if report is None:
            raise EvaluationReportNotFoundError(details={"report_id": str(report_id)})
        return report

    @staticmethod
    def _evaluation_suite_ref(suite: EvaluationSuite) -> EvaluationSuiteRef:
        return EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        )

    @staticmethod
    def _skill_tree_ref(tree: SkillTree) -> SkillTreeRef:
        return SkillTreeRef(
            tree_id=tree.tree_id,
            version=tree.version,
            checksum=tree.checksum,
        )

    @staticmethod
    def _validate_spec_source(
        spec: AgentSpec,
        instance: AgentInstance,
    ) -> None:
        if (
            spec.instance_id != instance.instance_id
            or spec.revision != instance.revision
            or spec.prototype != instance.prototype
            or spec.skill_tree != instance.skill_tree
        ):
            raise RepositoryUnavailableError(
                details={
                    "repository": "agent-specs",
                    "reason": "spec-source-mismatch",
                    "instance_id": str(instance.instance_id),
                    "revision": instance.revision,
                }
            )

    @staticmethod
    def _validate_prototype_source(
        prototype: AgentPrototype,
        instance: AgentInstance,
    ) -> None:
        if (
            prototype.prototype_id != instance.prototype.prototype_id
            or prototype.version != instance.prototype.version
            or prototype.checksum != instance.prototype.checksum
            or prototype.skill_tree != instance.skill_tree
        ):
            raise RepositoryUnavailableError(
                details={
                    "repository": "prototypes",
                    "reason": "prototype-source-mismatch",
                    "instance_id": str(instance.instance_id),
                    "revision": instance.revision,
                }
            )

    @staticmethod
    def _definition_checksum(command: RegisterPrototypeCommand) -> str:
        return sha256_model(command.definition)

    def _validate_knowledge_payload(self, command: RegisterKnowledgeCommand) -> None:
        content = command.knowledge.content
        if content is None:
            return
        encoded = (
            content.encode("utf-8")
            if isinstance(content, str)
            else canonical_json_bytes(content)
        )
        if len(encoded) > self._max_inline_knowledge_bytes:
            raise KnowledgePayloadTooLargeError(
                details={
                    "actual_bytes": len(encoded),
                    "max_bytes": self._max_inline_knowledge_bytes,
                }
            )
        actual_checksum = checksum_knowledge_content(content)
        if actual_checksum != command.knowledge.checksum:
            raise KnowledgeChecksumMismatchError(
                details={
                    "knowledge_id": command.knowledge.knowledge_id,
                    "version": command.knowledge.version,
                }
            )

    @staticmethod
    def _validate_bind_state(
        instance: AgentInstance,
        expected_revision: int,
    ) -> None:
        if instance.revision != expected_revision:
            raise RevisionConflictError(
                details={
                    "instance_id": str(instance.instance_id),
                    "expected_revision": expected_revision,
                    "actual_revision": instance.revision,
                }
            )
        if instance.status is InstanceStatus.RUNNING:
            raise InstanceBusyError(details={"instance_id": str(instance.instance_id)})
        allowed = {
            InstanceStatus.CREATED,
            InstanceStatus.WAITING,
            InstanceStatus.DEGRADED,
        }
        if instance.status not in allowed:
            raise InvalidStateTransitionError(
                details={
                    "instance_id": str(instance.instance_id),
                    "status": instance.status.value,
                    "operation": BIND_KNOWLEDGE,
                }
            )

    @staticmethod
    def _binding_selections(
        bindings: tuple[KnowledgeBinding, ...],
    ) -> tuple[KnowledgeSelection, ...]:
        return tuple(
            KnowledgeSelection(
                slot_name=binding.slot_name,
                knowledge_id=binding.knowledge_id,
                version=binding.knowledge_version,
            )
            for binding in bindings
        )

    async def _revalidate_bindings(
        self,
        uow: UnitOfWork,
        instance: AgentInstance,
        *,
        now: datetime,
    ) -> None:
        selections = self._binding_selections(instance.knowledge_bindings)
        packages = await uow.knowledge.get_many(
            tuple((item.knowledge_id, item.version) for item in selections)
        )
        expected = self._knowledge_policy.validate_and_build(
            definition=instance.configuration,
            selections=selections,
            packages=packages,
            bound_at=now,
            bound_by=instance.created_by,
        )
        expected_by_ref = {
            (item.slot_name, item.knowledge_id, item.knowledge_version): item
            for item in expected
        }
        for binding in instance.knowledge_bindings:
            expected_binding = expected_by_ref[
                (
                    binding.slot_name,
                    binding.knowledge_id,
                    binding.knowledge_version,
                )
            ]
            if binding.knowledge_checksum != expected_binding.knowledge_checksum:
                raise KnowledgeChecksumMismatchError(
                    details={
                        "slot_name": binding.slot_name,
                        "knowledge_id": binding.knowledge_id,
                        "version": binding.knowledge_version,
                    }
                )
            if binding.injection_mode is not expected_binding.injection_mode:
                raise KnowledgeInjectionModeMismatchError(
                    details={"slot_name": binding.slot_name}
                )

    async def _validate_runtime_readiness(
        self,
        uow: UnitOfWork,
        instance: AgentInstance,
        *,
        now: datetime,
    ) -> None:
        await self._revalidate_bindings(uow, instance, now=now)
        tools = self._tool_policy.resolve(instance.configuration.tools)
        self._spec_builder.build(
            instance=instance,
            tools=tools,
            generated_at=now,
        )

    async def _build_promotion_bindings(
        self,
        uow: UnitOfWork,
        *,
        current: AgentInstance,
        configuration: AgentDefinition,
        selections: tuple[KnowledgeSelection, ...],
        actor: str,
        now: datetime,
    ) -> tuple[KnowledgeBinding, ...]:
        existing_keys = {
            (
                binding.slot_name,
                binding.knowledge_id,
                binding.knowledge_version,
            )
            for binding in current.knowledge_bindings
        }
        selection_keys = {
            (selection.slot_name, selection.knowledge_id, selection.version)
            for selection in selections
        }
        duplicate_keys = existing_keys & selection_keys
        if duplicate_keys:
            raise KnowledgeAlreadyBoundError(
                details={
                    "slot_names": sorted(key[0] for key in duplicate_keys),
                }
            )

        complete_selections = (
            *self._binding_selections(current.knowledge_bindings),
            *selections,
        )
        packages = await uow.knowledge.get_many(
            tuple(
                (selection.knowledge_id, selection.version)
                for selection in complete_selections
            )
        )
        validated = self._knowledge_policy.validate_and_build(
            definition=configuration,
            selections=complete_selections,
            packages=packages,
            bound_at=now,
            bound_by=actor,
        )
        validated_by_ref = {
            (
                binding.slot_name,
                binding.knowledge_id,
                binding.knowledge_version,
            ): binding
            for binding in validated
        }
        for binding in current.knowledge_bindings:
            expected = validated_by_ref[
                (
                    binding.slot_name,
                    binding.knowledge_id,
                    binding.knowledge_version,
                )
            ]
            if binding.knowledge_checksum != expected.knowledge_checksum:
                raise KnowledgeChecksumMismatchError(
                    details={
                        "slot_name": binding.slot_name,
                        "knowledge_id": binding.knowledge_id,
                        "version": binding.knowledge_version,
                    }
                )
            if binding.injection_mode is not expected.injection_mode:
                raise KnowledgeInjectionModeMismatchError(
                    details={"slot_name": binding.slot_name}
                )

        new_bindings = tuple(
            validated_by_ref[
                (selection.slot_name, selection.knowledge_id, selection.version)
            ]
            for selection in selections
        )
        return tuple(
            sorted(
                (*current.knowledge_bindings, *new_bindings),
                key=lambda binding: (
                    binding.slot_name,
                    binding.knowledge_id,
                    semver_tuple(binding.knowledge_version),
                ),
            )
        )

    async def _build_degraded_bindings(
        self,
        uow: UnitOfWork,
        *,
        current: AgentInstance,
        configuration: AgentDefinition,
        now: datetime,
    ) -> tuple[tuple[KnowledgeBinding, ...], frozenset[str]]:
        declared_slots = {slot.name for slot in configuration.knowledge_slots}
        retained = tuple(
            binding
            for binding in current.knowledge_bindings
            if binding.slot_name in declared_slots
        )
        removed_binding_slots = frozenset(
            binding.slot_name
            for binding in current.knowledge_bindings
            if binding.slot_name not in declared_slots
        )
        selections = self._binding_selections(retained)
        packages = await uow.knowledge.get_many(
            tuple(
                (selection.knowledge_id, selection.version) for selection in selections
            )
        )
        validated = self._knowledge_policy.validate_and_build(
            definition=configuration,
            selections=selections,
            packages=packages,
            bound_at=now,
            bound_by=current.created_by,
        )
        validated_by_ref = {
            (
                binding.slot_name,
                binding.knowledge_id,
                binding.knowledge_version,
            ): binding
            for binding in validated
        }
        for binding in retained:
            expected = validated_by_ref[
                (
                    binding.slot_name,
                    binding.knowledge_id,
                    binding.knowledge_version,
                )
            ]
            if binding.knowledge_checksum != expected.knowledge_checksum:
                raise KnowledgeChecksumMismatchError(
                    details={
                        "slot_name": binding.slot_name,
                        "knowledge_id": binding.knowledge_id,
                        "version": binding.knowledge_version,
                    }
                )
            if binding.injection_mode is not expected.injection_mode:
                raise KnowledgeInjectionModeMismatchError(
                    details={"slot_name": binding.slot_name}
                )
        return retained, removed_binding_slots

    def _correlation_id(self) -> UUID:
        value = self._correlation_context.get()
        if value is None:
            return self._id_generator.new()
        try:
            return UUID(value)
        except ValueError as exc:
            raise RuntimeError("correlation context must contain a UUID") from exc
