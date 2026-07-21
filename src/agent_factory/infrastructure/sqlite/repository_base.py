"""Shared SQLite query primitives for repositories in one Unit of Work."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from agent_factory.infrastructure.sqlite.codec import raise_database_error

SqlParameters = Sequence[Any]


def format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class SqliteRepository:
    repository_name = "sqlite"

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def _fetchone(
        self,
        sql: str,
        parameters: SqlParameters = (),
    ) -> aiosqlite.Row | None:
        try:
            cursor = await self._connection.execute(sql, parameters)
            try:
                return await cursor.fetchone()
            finally:
                await cursor.close()
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def _fetchall(
        self,
        sql: str,
        parameters: SqlParameters = (),
    ) -> list[aiosqlite.Row]:
        try:
            cursor = await self._connection.execute(sql, parameters)
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def _execute(
        self,
        sql: str,
        parameters: SqlParameters = (),
    ) -> int:
        try:
            cursor = await self._connection.execute(sql, parameters)
            try:
                return cursor.rowcount
            finally:
                await cursor.close()
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)
