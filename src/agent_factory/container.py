"""Application composition root for process-level dependencies."""

from dataclasses import dataclass, field

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.controller import FactoryController
from agent_factory.application.idempotency import IdempotencyService
from agent_factory.application.ports import (
    Clock,
    CorrelationContext,
    IdGenerator,
    ToolCatalog,
)
from agent_factory.application.tooling import ToolPolicy
from agent_factory.application.unit_of_work import UnitOfWorkFactory
from agent_factory.domain.enums import ToolPermission
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)
from agent_factory.infrastructure.system import (
    ContextVarCorrelationContext,
    SystemClock,
    UUID4Generator,
)
from agent_factory.infrastructure.tool_catalog import default_tool_catalog
from agent_factory.settings import Settings


@dataclass(slots=True)
class Container:
    """Own process resources and expose startup readiness."""

    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    correlation_context: CorrelationContext
    migration_runner: SqliteMigrationRunner
    uow_factory: UnitOfWorkFactory
    tool_catalog: ToolCatalog
    controller: FactoryController
    _ready: bool = field(default=False, init=False)

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        await self.migration_runner.migrate()
        self._ready = True

    async def close(self) -> None:
        self._ready = False


def build_container(settings: Settings) -> Container:
    """Build the process dependency graph without performing I/O."""

    clock = SystemClock()
    id_generator = UUID4Generator()
    correlation_context = ContextVarCorrelationContext()
    migration_runner = SqliteMigrationRunner.from_database_url(
        database_url=settings.database_url,
        migrations_dir=settings.migrations_dir,
        clock=clock,
    )
    uow_factory = SqliteUnitOfWorkFactory(
        migration_runner.database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
    tool_catalog = default_tool_catalog()
    controller = FactoryController(
        uow_factory=uow_factory,
        clock=clock,
        id_generator=id_generator,
        correlation_context=correlation_context,
        prototype_policy=PrototypePolicy(),
        knowledge_policy=KnowledgeBindingPolicy(),
        tool_policy=ToolPolicy(
            tool_catalog,
            allowed_permissions=frozenset({ToolPermission.READ_ONLY}),
        ),
        spec_builder=AgentSpecBuilder(),
        idempotency=IdempotencyService(ttl_seconds=settings.idempotency_ttl_seconds),
        audit_factory=AuditEventFactory(id_generator),
        max_inline_knowledge_bytes=settings.max_inline_knowledge_bytes,
    )
    return Container(
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        correlation_context=correlation_context,
        migration_runner=migration_runner,
        uow_factory=uow_factory,
        tool_catalog=tool_catalog,
        controller=controller,
    )
