"""Deterministic application service for the M1 Agent production chain."""

from datetime import datetime
from uuid import UUID

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    DeprecatePrototypeCommand,
    KnowledgeSelection,
    PublishPrototypeCommand,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
)
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.ports import Clock, CorrelationContext, IdGenerator
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.application.repositories import (
    InstanceRepository,
    PrototypeRepository,
)
from agent_factory.application.tooling import ToolPolicy
from agent_factory.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import (
    canonical_json_bytes,
    checksum_knowledge_content,
    sha256_model,
)
from agent_factory.domain.enums import InstanceStatus, PrototypeStatus
from agent_factory.domain.errors import (
    InstanceBusyError,
    InstanceNotFoundError,
    InvalidPrototypeStatusError,
    InvalidStateTransitionError,
    KnowledgeAlreadyBoundError,
    KnowledgeChecksumMismatchError,
    KnowledgeInjectionModeMismatchError,
    KnowledgePayloadTooLargeError,
    PrototypeNotFoundError,
    PrototypeNotPublishedError,
    RepositoryUnavailableError,
    RevisionConflictError,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    KnowledgeBinding,
    PrototypeRef,
)
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.spec import AgentSpecBuilder

REGISTER_PROTOTYPE = "register-prototype"
PUBLISH_PROTOTYPE = "publish-prototype"
DEPRECATE_PROTOTYPE = "deprecate-prototype"
REGISTER_KNOWLEDGE = "register-knowledge"
CLONE_AGENT = "clone-agent"
BIND_KNOWLEDGE = "bind-knowledge"


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
        knowledge_policy: KnowledgeBindingPolicy,
        tool_policy: ToolPolicy,
        spec_builder: AgentSpecBuilder,
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
        self._knowledge_policy = knowledge_policy
        self._tool_policy = tool_policy
        self._spec_builder = spec_builder
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
            prototype = AgentPrototype(
                prototype_id=command.prototype_id,
                version=command.version,
                definition=command.definition,
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
            bindings = self._knowledge_policy.validate_and_build(
                definition=current.configuration,
                selections=final_selections,
                packages=packages,
                bound_at=now,
                bound_by=command.actor,
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

    def _correlation_id(self) -> UUID:
        value = self._correlation_context.get()
        if value is None:
            return self._id_generator.new()
        try:
            return UUID(value)
        except ValueError as exc:
            raise RuntimeError("correlation context must contain a UUID") from exc
