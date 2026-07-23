"""SQLite persistence for immutable runtime tool-call records."""

from __future__ import annotations

import sqlite3
from uuid import UUID

import aiosqlite

from agent_factory.application.tool_contracts import ToolCallRecord
from agent_factory.domain.common import sha256_model
from agent_factory.domain.errors import ToolCallAlreadyExistsError
from agent_factory.infrastructure.sqlite.codec import (
    decode_model,
    encode_model,
    raise_database_error,
    require_projection,
)
from agent_factory.infrastructure.sqlite.repository_base import (
    SqliteRepository,
    format_datetime,
)


class SqliteToolCallRepository(SqliteRepository):
    repository_name = "tool-calls"

    async def add(self, record: ToolCallRecord) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO tool_call_records (
                    call_id, task_id, instance_id, instance_revision,
                    agent_spec_checksum, tool_name, tool_version, status,
                    arguments_hash, result_hash, error_code, duration_ms,
                    actor, correlation_id, record_json, record_checksum,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.call_id),
                    str(record.task_id),
                    str(record.instance_id),
                    record.instance_revision,
                    record.agent_spec_checksum,
                    record.tool_name,
                    record.tool_version,
                    record.status.value,
                    record.arguments_hash,
                    record.result_hash,
                    record.error_code,
                    record.duration_ms,
                    record.actor,
                    str(record.correlation_id),
                    encode_model(record),
                    sha256_model(record),
                    format_datetime(record.started_at),
                    format_datetime(record.completed_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname in {
                "SQLITE_CONSTRAINT_PRIMARYKEY",
                "SQLITE_CONSTRAINT_UNIQUE",
            }:
                raise ToolCallAlreadyExistsError(
                    details={"call_id": str(record.call_id)}
                ) from exc
            raise_database_error(self.repository_name, exc)
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(self, call_id: UUID) -> ToolCallRecord | None:
        row = await self._fetchone(
            """
            SELECT call_id, task_id, instance_id, instance_revision,
                   agent_spec_checksum, tool_name, tool_version, status,
                   arguments_hash, result_hash, error_code, duration_ms,
                   actor, correlation_id, record_json, record_checksum,
                   started_at, completed_at
            FROM tool_call_records
            WHERE call_id = ?
            """,
            (str(call_id),),
        )
        return None if row is None else self._decode(row)

    def _decode(self, row: aiosqlite.Row) -> ToolCallRecord:
        record = decode_model(
            str(row["record_json"]),
            ToolCallRecord,
            repository=self.repository_name,
        )
        projections = {
            "call_id": str(record.call_id) == row["call_id"],
            "task_id": str(record.task_id) == row["task_id"],
            "instance_id": str(record.instance_id) == row["instance_id"],
            "instance_revision": record.instance_revision == row["instance_revision"],
            "agent_spec_checksum": record.agent_spec_checksum
            == row["agent_spec_checksum"],
            "tool_name": record.tool_name == row["tool_name"],
            "tool_version": record.tool_version == row["tool_version"],
            "status": record.status.value == row["status"],
            "arguments_hash": record.arguments_hash == row["arguments_hash"],
            "result_hash": record.result_hash == row["result_hash"],
            "error_code": record.error_code == row["error_code"],
            "duration_ms": record.duration_ms == row["duration_ms"],
            "actor": record.actor == row["actor"],
            "correlation_id": str(record.correlation_id) == row["correlation_id"],
            "record_checksum": sha256_model(record) == row["record_checksum"],
            "started_at": format_datetime(record.started_at) == row["started_at"],
            "completed_at": format_datetime(record.completed_at) == row["completed_at"],
        }
        for field, valid in projections.items():
            require_projection(valid, repository=self.repository_name, field=field)
        return record
