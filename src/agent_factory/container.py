"""Application composition root for process-level dependencies."""

from dataclasses import dataclass, field

from agent_factory.application.ports import Clock, CorrelationContext, IdGenerator
from agent_factory.application.unit_of_work import UnitOfWorkFactory
from agent_factory.infrastructure.sqlite import (
    SqliteMigrationRunner,
    SqliteUnitOfWorkFactory,
)
from agent_factory.infrastructure.system import (
    ContextVarCorrelationContext,
    SystemClock,
    UUID4Generator,
)
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
    """Build the M0 dependency graph without performing I/O."""

    clock = SystemClock()
    migration_runner = SqliteMigrationRunner.from_database_url(
        database_url=settings.database_url,
        migrations_dir=settings.migrations_dir,
        clock=clock,
    )
    return Container(
        settings=settings,
        clock=clock,
        id_generator=UUID4Generator(),
        correlation_context=ContextVarCorrelationContext(),
        migration_runner=migration_runner,
        uow_factory=SqliteUnitOfWorkFactory(
            migration_runner.database_path,
            busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        ),
    )
