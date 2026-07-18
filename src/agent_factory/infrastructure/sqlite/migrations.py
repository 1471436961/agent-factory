"""Forward-only, checksummed SQLite migrations for the Alpha deployment model."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite
from sqlalchemy.engine import make_url

from agent_factory.application.ports import Clock

_MIGRATION_FILE = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")
_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


class MigrationError(RuntimeError):
    """Base class for migration failures that must abort application startup."""


class MigrationConfigurationError(MigrationError):
    """The configured database or migration location is unsupported."""


class MigrationDefinitionError(MigrationError):
    """Migration files are missing, malformed, duplicated, or non-contiguous."""


class MigrationHistoryError(MigrationError):
    """Persisted migration history cannot be reconciled with local files."""


class MigrationChecksumError(MigrationHistoryError):
    """An already-applied migration has been modified."""


class MigrationExecutionError(MigrationError):
    """SQLite rejected a pending migration."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied_versions: tuple[int, ...]
    current_version: int


class SqliteMigrationRunner:
    """Discover and atomically apply immutable, forward-only SQL files."""

    def __init__(
        self,
        database_path: Path,
        migrations_dir: Path,
        clock: Clock,
    ) -> None:
        self._database_path = database_path
        self._migrations_dir = migrations_dir
        self._clock = clock

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        migrations_dir: Path,
        clock: Clock,
    ) -> SqliteMigrationRunner:
        url = make_url(database_url)
        if url.drivername not in {"sqlite", "sqlite+aiosqlite"}:
            raise MigrationConfigurationError(
                "M0 migration runner supports only sqlite or sqlite+aiosqlite URLs"
            )
        database = url.database
        if database is None or database in {"", ":memory:"}:
            raise MigrationConfigurationError(
                "M0 migrations require a file-backed SQLite database"
            )
        return cls(Path(database), migrations_dir, clock)

    @property
    def database_path(self) -> Path:
        return self._database_path

    def discover(self) -> tuple[Migration, ...]:
        if not self._migrations_dir.is_dir():
            raise MigrationConfigurationError(
                f"migration directory does not exist: {self._migrations_dir}"
            )

        migrations: list[Migration] = []
        for path in sorted(self._migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILE.fullmatch(path.name)
            if match is None:
                raise MigrationDefinitionError(
                    f"invalid migration filename: {path.name}"
                )
            payload = path.read_bytes()
            migrations.append(
                Migration(
                    version=int(match.group("version")),
                    name=match.group("name"),
                    path=path,
                    checksum=hashlib.sha256(payload).hexdigest(),
                    sql=payload.decode("utf-8"),
                )
            )

        if not migrations:
            raise MigrationDefinitionError("at least one migration is required")

        versions = [migration.version for migration in migrations]
        expected = list(range(1, len(migrations) + 1))
        if versions != expected:
            raise MigrationDefinitionError(
                f"migration versions must be contiguous from 001: {versions}"
            )
        return tuple(migrations)

    async def migrate(self) -> MigrationResult:
        migrations = self.discover()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript(_HISTORY_DDL)
            await connection.commit()

            history = await self._load_history(connection)
            self._validate_history(migrations, history)

            applied: list[int] = []
            for migration in migrations:
                if migration.version in history:
                    continue
                await self._apply(connection, migration)
                applied.append(migration.version)

        return MigrationResult(
            applied_versions=tuple(applied),
            current_version=migrations[-1].version,
        )

    async def ping(self) -> bool:
        if not self._database_path.is_file():
            return False
        try:
            async with aiosqlite.connect(self._database_path) as connection:
                cursor = await connection.execute("SELECT 1")
                row = await cursor.fetchone()
        except aiosqlite.Error:
            return False
        return row == (1,)

    async def _load_history(
        self,
        connection: aiosqlite.Connection,
    ) -> dict[int, tuple[str, str]]:
        cursor = await connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
        rows = await cursor.fetchall()
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}

    def _validate_history(
        self,
        migrations: tuple[Migration, ...],
        history: dict[int, tuple[str, str]],
    ) -> None:
        local_by_version = {migration.version: migration for migration in migrations}
        missing = sorted(set(history) - set(local_by_version))
        if missing:
            raise MigrationHistoryError(
                f"database contains migrations missing locally: {missing}"
            )

        for version, (applied_name, applied_checksum) in history.items():
            local = local_by_version[version]
            if applied_name != local.name:
                raise MigrationHistoryError(
                    f"migration {version:03d} name changed: "
                    f"{applied_name!r} != {local.name!r}"
                )
            if applied_checksum != local.checksum:
                raise MigrationChecksumError(
                    f"migration {version:03d}_{local.name}.sql checksum changed"
                )

    async def _apply(
        self,
        connection: aiosqlite.Connection,
        migration: Migration,
    ) -> None:
        applied_at = _format_timestamp(self._clock.now())
        metadata_sql = (
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            f"VALUES ({migration.version}, {_sql_literal(migration.name)}, "
            f"{_sql_literal(migration.checksum)}, {_sql_literal(applied_at)});"
        )
        script = f"BEGIN IMMEDIATE;\n{migration.sql}\n{metadata_sql}\nCOMMIT;"
        try:
            await connection.executescript(script)
        except aiosqlite.Error as exc:
            await connection.rollback()
            raise MigrationExecutionError(
                f"failed to apply migration {migration.path.name}"
            ) from exc


def _sql_literal(value: str) -> str:
    """Quote trusted migration metadata for an atomic ``executescript`` call."""

    return "'" + value.replace("'", "''") + "'"


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MigrationConfigurationError("Clock.now() must return an aware datetime")
    return value.isoformat().replace("+00:00", "Z")
