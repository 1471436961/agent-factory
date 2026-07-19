"""Connection-per-operation SQLite Unit of Work implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from agent_factory.application.repositories import (
    AgentSpecRepository,
    AuditRepository,
    IdempotencyRepository,
    InstanceRepository,
    KnowledgeRepository,
    PrototypeRepository,
)
from agent_factory.infrastructure.sqlite.codec import raise_database_error
from agent_factory.infrastructure.sqlite.repositories import (
    SqliteAgentSpecRepository,
    SqliteAuditRepository,
    SqliteIdempotencyRepository,
    SqliteInstanceRepository,
    SqliteKnowledgeRepository,
    SqlitePrototypeRepository,
)


class SqliteUnitOfWork:
    prototypes: PrototypeRepository
    knowledge: KnowledgeRepository
    instances: InstanceRepository
    specs: AgentSpecRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    def __init__(
        self,
        database_path: Path,
        *,
        read_only: bool,
        busy_timeout_ms: int,
    ) -> None:
        self._database_path = database_path
        self._read_only = read_only
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: aiosqlite.Connection | None = None
        self._completed = False

    async def __aenter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("unit of work cannot be entered more than once")
        try:
            connection = await aiosqlite.connect(
                self._database_path,
                timeout=self._busy_timeout_ms / 1_000,
            )
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            if self._read_only:
                await connection.execute("PRAGMA query_only = ON")
                await connection.execute("BEGIN")
            else:
                await connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            if "connection" in locals():
                await connection.close()
            raise_database_error("unit-of-work", exc)

        self._connection = connection
        self.prototypes = SqlitePrototypeRepository(connection)
        self.knowledge = SqliteKnowledgeRepository(connection)
        self.instances = SqliteInstanceRepository(connection)
        self.specs = SqliteAgentSpecRepository(connection)
        self.audit = SqliteAuditRepository(connection)
        self.idempotency = SqliteIdempotencyRepository(connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            if connection.in_transaction:
                await connection.rollback()
        finally:
            await connection.close()
            self._connection = None

    async def commit(self) -> None:
        connection = self._require_active()
        if self._completed or not connection.in_transaction:
            raise RuntimeError("unit of work transaction is already completed")
        try:
            await connection.commit()
        except sqlite3.Error as exc:
            raise_database_error("unit-of-work", exc)
        self._completed = True

    async def rollback(self) -> None:
        connection = self._require_active()
        if self._completed or not connection.in_transaction:
            raise RuntimeError("unit of work transaction is already completed")
        try:
            await connection.rollback()
        except sqlite3.Error as exc:
            raise_database_error("unit-of-work", exc)
        self._completed = True

    def _require_active(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("unit of work is not active")
        return self._connection


class SqliteUnitOfWorkFactory:
    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms

    def __call__(self, *, read_only: bool = False) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(
            self._database_path,
            read_only=read_only,
            busy_timeout_ms=self._busy_timeout_ms,
        )
