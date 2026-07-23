"""Application composition root for process-level dependencies."""

from dataclasses import dataclass, field

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.controller import FactoryController
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.ports import (
    Clock,
    CorrelationContext,
    IdGenerator,
    MonotonicClock,
    ToolCatalog,
)
from agent_factory.application.security import (
    Authenticator,
    AuthorizationPolicy,
    Principal,
)
from agent_factory.application.tool_contracts import ToolRegistry
from agent_factory.application.tool_execution import ToolExecutor
from agent_factory.application.tooling import ToolPolicy
from agent_factory.application.unit_of_work import UnitOfWorkFactory
from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.services.degradation import DegradationPolicy
from agent_factory.domain.services.evaluation import DeterministicRuleEngine
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.lifecycle import LifecyclePolicy
from agent_factory.domain.services.promotion import PromotionPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.infrastructure.authentication import (
    StaticBearerAuthenticator,
    UnavailableAuthenticator,
)
from agent_factory.infrastructure.runtime import (
    OfflineDemoRuntimeAdapter,
    default_tool_registry,
)
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)
from agent_factory.infrastructure.system import (
    ContextVarCorrelationContext,
    SystemClock,
    SystemMonotonicClock,
    UUID4Generator,
)
from agent_factory.infrastructure.tool_catalog import InMemoryToolCatalog
from agent_factory.interfaces.factory_tools import FactoryToolAdapter
from agent_factory.settings import Settings


@dataclass(slots=True)
class Container:
    """Own process resources and expose startup readiness."""

    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    monotonic_clock: MonotonicClock
    correlation_context: CorrelationContext
    authenticator: Authenticator
    authorization_policy: AuthorizationPolicy
    migration_runner: SqliteMigrationRunner
    uow_factory: UnitOfWorkFactory
    tool_catalog: ToolCatalog
    tool_registry: ToolRegistry
    controller: FactoryController
    factory_tools: FactoryToolAdapter
    tool_executor: ToolExecutor
    demo_runtime: OfflineDemoRuntimeAdapter
    _ready: bool = field(default=False, init=False)

    @property
    def ready(self) -> bool:
        return self._ready and self.authenticator.ready

    async def start(self) -> None:
        await self.migration_runner.migrate()
        self._ready = True

    async def close(self) -> None:
        self._ready = False


def build_container(settings: Settings) -> Container:
    """Build the process dependency graph without performing I/O."""

    clock = SystemClock()
    id_generator = UUID4Generator()
    monotonic_clock = SystemMonotonicClock()
    correlation_context = ContextVarCorrelationContext()
    principal = Principal(
        subject=settings.auth_subject,
        roles=settings.auth_roles,
    )
    authenticator: Authenticator
    if settings.auth_token is None:
        authenticator = UnavailableAuthenticator()
    else:
        authenticator = StaticBearerAuthenticator.from_secret(
            settings.auth_token,
            principal,
        )
    authorization_policy = AuthorizationPolicy()
    migration_runner = SqliteMigrationRunner.from_database_url(
        database_url=settings.database_url,
        migrations_dir=settings.migrations_dir,
        clock=clock,
    )
    uow_factory = SqliteUnitOfWorkFactory(
        migration_runner.database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
    tool_registry = default_tool_registry()
    tool_catalog = InMemoryToolCatalog(
        definition.resolved_spec() for definition in tool_registry.definitions()
    )
    audit_factory = AuditEventFactory(id_generator)
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
        tool_policy=ToolPolicy(
            tool_catalog,
            allowed_permissions=frozenset({ToolPermission.READ_ONLY}),
        ),
        spec_builder=AgentSpecBuilder(),
        evaluation_engine=DeterministicRuleEngine(),
        idempotency=IdempotencyService(ttl_seconds=settings.idempotency_ttl_seconds),
        audit_factory=audit_factory,
        max_inline_knowledge_bytes=settings.max_inline_knowledge_bytes,
    )
    factory_tools = FactoryToolAdapter(
        controller=controller,
        authorization_policy=authorization_policy,
        correlation_context=correlation_context,
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        uow_factory=uow_factory,
        clock=clock,
        monotonic_clock=monotonic_clock,
        audit_factory=audit_factory,
    )
    demo_runtime = OfflineDemoRuntimeAdapter(
        tool_executor=tool_executor,
        clock=clock,
        id_generator=id_generator,
    )
    return Container(
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        monotonic_clock=monotonic_clock,
        correlation_context=correlation_context,
        authenticator=authenticator,
        authorization_policy=authorization_policy,
        migration_runner=migration_runner,
        uow_factory=uow_factory,
        tool_catalog=tool_catalog,
        tool_registry=tool_registry,
        controller=controller,
        factory_tools=factory_tools,
        tool_executor=tool_executor,
        demo_runtime=demo_runtime,
    )
