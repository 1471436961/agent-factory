"""SQLite repository adapters over one Unit of Work connection."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import UUID

import aiosqlite

from agent_factory.application.persistence import IdempotencyRecord
from agent_factory.application.queries import AuditQuery, Page, PrototypeListQuery
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import (
    checksum_knowledge_content,
    semver_tuple,
    sha256_model,
)
from agent_factory.domain.enums import PrototypeStatus
from agent_factory.domain.errors import (
    KnowledgeAlreadyExistsError,
    PrototypeAlreadyExistsError,
    RepositoryUnavailableError,
    RevisionConflictError,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
)
from agent_factory.domain.references import SkillTreeRef
from agent_factory.domain.services.spec import checksum_agent_spec
from agent_factory.infrastructure.sqlite.codec import (
    decode_json_object,
    decode_model,
    encode_json_object,
    encode_model,
    raise_database_error,
    require_projection,
)
from agent_factory.infrastructure.sqlite.repository_base import (
    SqliteRepository,
    format_datetime,
)


class SqlitePrototypeRepository(SqliteRepository):
    repository_name = "prototypes"

    async def add(self, prototype: AgentPrototype) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO prototypes (
                    prototype_id, version, status, payload_json,
                    checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    prototype.prototype_id,
                    prototype.version,
                    prototype.status.value,
                    encode_model(prototype),
                    prototype.checksum,
                    format_datetime(prototype.created_at),
                ),
            )
            await self._replace_skill_tree_projection(prototype)
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname in {
                "SQLITE_CONSTRAINT_PRIMARYKEY",
                "SQLITE_CONSTRAINT_UNIQUE",
            }:
                raise PrototypeAlreadyExistsError(
                    details={
                        "prototype_id": prototype.prototype_id,
                        "version": prototype.version,
                    }
                ) from exc
            raise_database_error(self.repository_name, exc)
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(
        self,
        prototype_id: str,
        version: str,
    ) -> AgentPrototype | None:
        row = await self._fetchone(
            """
            SELECT prototypes.prototype_id, prototypes.version, prototypes.status,
                   prototypes.payload_json, prototypes.checksum,
                   prototypes.created_at, skill_trees.tree_id,
                   skill_trees.tree_version, skill_trees.tree_checksum
            FROM prototypes
            LEFT JOIN prototype_skill_trees AS skill_trees
              ON skill_trees.prototype_id = prototypes.prototype_id
             AND skill_trees.prototype_version = prototypes.version
            WHERE prototypes.prototype_id = ? AND prototypes.version = ?
            """,
            (prototype_id, version),
        )
        return None if row is None else self._decode(row)

    async def list(
        self,
        query: PrototypeListQuery,
    ) -> Page[AgentPrototype]:
        if query.status is None:
            rows = await self._fetchall(
                """
                SELECT prototypes.prototype_id, prototypes.version,
                       prototypes.status, prototypes.payload_json,
                       prototypes.checksum, prototypes.created_at,
                       skill_trees.tree_id, skill_trees.tree_version,
                       skill_trees.tree_checksum
                FROM prototypes
                LEFT JOIN prototype_skill_trees AS skill_trees
                  ON skill_trees.prototype_id = prototypes.prototype_id
                 AND skill_trees.prototype_version = prototypes.version
                """
            )
        else:
            rows = await self._fetchall(
                """
                SELECT prototypes.prototype_id, prototypes.version,
                       prototypes.status, prototypes.payload_json,
                       prototypes.checksum, prototypes.created_at,
                       skill_trees.tree_id, skill_trees.tree_version,
                       skill_trees.tree_checksum
                FROM prototypes
                LEFT JOIN prototype_skill_trees AS skill_trees
                  ON skill_trees.prototype_id = prototypes.prototype_id
                 AND skill_trees.prototype_version = prototypes.version
                WHERE prototypes.status = ?
                """,
                (query.status.value,),
            )

        prototypes = [self._decode(row) for row in rows]
        if query.agent_type is not None:
            prototypes = [
                item
                for item in prototypes
                if item.definition.agent_type == query.agent_type
            ]

        # Stable sorts express mixed directions without treating SemVer as text.
        prototypes.sort(key=lambda item: semver_tuple(item.version), reverse=True)
        prototypes.sort(key=lambda item: item.prototype_id)
        prototypes.sort(key=lambda item: item.created_at, reverse=True)
        total = len(prototypes)
        offset = (query.page - 1) * query.page_size
        return Page[AgentPrototype](
            items=tuple(prototypes[offset : offset + query.page_size]),
            page=query.page,
            page_size=query.page_size,
            total=total,
        )

    async def replace(
        self,
        prototype: AgentPrototype,
        expected_status: PrototypeStatus,
    ) -> bool:
        changed = await self._execute(
            """
            UPDATE prototypes
            SET status = ?, payload_json = ?, checksum = ?, created_at = ?
            WHERE prototype_id = ? AND version = ? AND status = ?
            """,
            (
                prototype.status.value,
                encode_model(prototype),
                prototype.checksum,
                format_datetime(prototype.created_at),
                prototype.prototype_id,
                prototype.version,
                expected_status.value,
            ),
        )
        if changed == 1:
            try:
                await self._replace_skill_tree_projection(prototype)
            except sqlite3.Error as exc:
                raise_database_error(self.repository_name, exc)
        return changed == 1

    async def _replace_skill_tree_projection(
        self,
        prototype: AgentPrototype,
    ) -> None:
        await self._connection.execute(
            """
            DELETE FROM prototype_skill_trees
            WHERE prototype_id = ? AND prototype_version = ?
            """,
            (prototype.prototype_id, prototype.version),
        )
        if prototype.skill_tree is not None:
            await self._connection.execute(
                """
                INSERT INTO prototype_skill_trees (
                    prototype_id, prototype_version,
                    tree_id, tree_version, tree_checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    prototype.prototype_id,
                    prototype.version,
                    prototype.skill_tree.tree_id,
                    prototype.skill_tree.version,
                    prototype.skill_tree.checksum,
                ),
            )

    def _decode(self, row: aiosqlite.Row) -> AgentPrototype:
        prototype = decode_model(
            str(row["payload_json"]),
            AgentPrototype,
            repository=self.repository_name,
        )
        require_projection(
            prototype.prototype_id == row["prototype_id"],
            repository=self.repository_name,
            field="prototype_id",
        )
        require_projection(
            prototype.version == row["version"],
            repository=self.repository_name,
            field="version",
        )
        require_projection(
            prototype.status.value == row["status"],
            repository=self.repository_name,
            field="status",
        )
        require_projection(
            prototype.checksum == row["checksum"],
            repository=self.repository_name,
            field="checksum",
        )
        require_projection(
            prototype.checksum == sha256_model(prototype.definition),
            repository=self.repository_name,
            field="definition_checksum",
        )
        require_projection(
            format_datetime(prototype.created_at) == row["created_at"],
            repository=self.repository_name,
            field="created_at",
        )
        _require_skill_tree_projection(
            prototype.skill_tree,
            row,
            repository=self.repository_name,
        )
        return prototype


class SqliteKnowledgeRepository(SqliteRepository):
    repository_name = "knowledge"

    async def add(self, knowledge: DomainKnowledge) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO knowledge_packages (
                    knowledge_id, version, kind, payload_json,
                    checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    knowledge.knowledge_id,
                    knowledge.version,
                    knowledge.kind.value,
                    encode_model(knowledge),
                    knowledge.checksum,
                    format_datetime(knowledge.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeAlreadyExistsError(
                details={
                    "knowledge_id": knowledge.knowledge_id,
                    "version": knowledge.version,
                }
            ) from exc
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(
        self,
        knowledge_id: str,
        version: str,
    ) -> DomainKnowledge | None:
        row = await self._fetchone(
            """
            SELECT knowledge_id, version, kind, payload_json,
                   checksum, created_at
            FROM knowledge_packages
            WHERE knowledge_id = ? AND version = ?
            """,
            (knowledge_id, version),
        )
        return None if row is None else self._decode(row)

    async def get_many(
        self,
        refs: tuple[tuple[str, str], ...],
    ) -> tuple[DomainKnowledge, ...]:
        if not refs:
            return ()
        predicates = " OR ".join("(knowledge_id = ? AND version = ?)" for _ in refs)
        parameters = tuple(value for ref in refs for value in ref)
        rows = await self._fetchall(
            f"""
            SELECT knowledge_id, version, kind, payload_json,
                   checksum, created_at
            FROM knowledge_packages
            WHERE {predicates}
            """,
            parameters,
        )
        found = {
            (item.knowledge_id, item.version): item
            for item in (self._decode(row) for row in rows)
        }
        return tuple(found[ref] for ref in refs if ref in found)

    def _decode(self, row: aiosqlite.Row) -> DomainKnowledge:
        knowledge = decode_model(
            str(row["payload_json"]),
            DomainKnowledge,
            repository=self.repository_name,
        )
        projections = {
            "knowledge_id": knowledge.knowledge_id == row["knowledge_id"],
            "version": knowledge.version == row["version"],
            "kind": knowledge.kind.value == row["kind"],
            "checksum": knowledge.checksum == row["checksum"],
            "created_at": (format_datetime(knowledge.created_at) == row["created_at"]),
        }
        for field, valid in projections.items():
            require_projection(
                valid,
                repository=self.repository_name,
                field=field,
            )
        if knowledge.content is not None:
            require_projection(
                checksum_knowledge_content(knowledge.content) == knowledge.checksum,
                repository=self.repository_name,
                field="content_checksum",
            )
        return knowledge


class SqliteInstanceRepository(SqliteRepository):
    repository_name = "instances"

    async def add(self, instance: AgentInstance) -> None:
        if instance.revision != 1:
            raise RevisionConflictError(
                details={"expected_revision": 1, "actual_revision": instance.revision}
            )
        try:
            await self._insert_snapshot(instance)
            await self._insert_skill_tree_projection(instance)
            await self._connection.execute(
                """
                INSERT INTO instance_heads (
                    instance_id, current_revision, updated_at
                ) VALUES (?, ?, ?)
                """,
                (
                    str(instance.instance_id),
                    instance.revision,
                    format_datetime(instance.updated_at),
                ),
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def get(
        self,
        instance_id: UUID,
        revision: int | None = None,
    ) -> AgentInstance | None:
        if revision is None:
            row = await self._fetchone(
                """
                SELECT snapshots.*, skill_trees.tree_id,
                       skill_trees.tree_version, skill_trees.tree_checksum
                FROM instance_snapshots AS snapshots
                JOIN instance_heads AS heads
                  ON heads.instance_id = snapshots.instance_id
                 AND heads.current_revision = snapshots.revision
                LEFT JOIN instance_skill_trees AS skill_trees
                  ON skill_trees.instance_id = snapshots.instance_id
                 AND skill_trees.revision = snapshots.revision
                WHERE snapshots.instance_id = ?
                """,
                (str(instance_id),),
            )
        else:
            row = await self._fetchone(
                """
                SELECT snapshots.instance_id, snapshots.revision,
                       snapshots.status, snapshots.prototype_id,
                       snapshots.prototype_version, snapshots.payload_json,
                       snapshots.created_at, skill_trees.tree_id,
                       skill_trees.tree_version, skill_trees.tree_checksum
                FROM instance_snapshots AS snapshots
                LEFT JOIN instance_skill_trees AS skill_trees
                  ON skill_trees.instance_id = snapshots.instance_id
                 AND skill_trees.revision = snapshots.revision
                WHERE snapshots.instance_id = ? AND snapshots.revision = ?
                """,
                (str(instance_id), revision),
            )
        return None if row is None else self._decode(row)

    async def save_snapshot(
        self,
        instance: AgentInstance,
        expected_revision: int,
    ) -> None:
        if instance.revision != expected_revision + 1:
            raise RevisionConflictError(
                details={
                    "expected_revision": expected_revision,
                    "new_revision": instance.revision,
                }
            )
        try:
            await self._insert_snapshot(instance)
            await self._insert_skill_tree_projection(instance)
            cursor = await self._connection.execute(
                """
                UPDATE instance_heads
                SET current_revision = ?, updated_at = ?
                WHERE instance_id = ? AND current_revision = ?
                """,
                (
                    instance.revision,
                    format_datetime(instance.updated_at),
                    str(instance.instance_id),
                    expected_revision,
                ),
            )
            try:
                changed = cursor.rowcount
            finally:
                await cursor.close()
        except sqlite3.IntegrityError as exc:
            raise RevisionConflictError(
                details={
                    "instance_id": str(instance.instance_id),
                    "expected_revision": expected_revision,
                }
            ) from exc
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)
        if changed != 1:
            raise RevisionConflictError(
                details={
                    "instance_id": str(instance.instance_id),
                    "expected_revision": expected_revision,
                }
            )

    async def _insert_snapshot(self, instance: AgentInstance) -> None:
        await self._connection.execute(
            """
            INSERT INTO instance_snapshots (
                instance_id, revision, status, prototype_id,
                prototype_version, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(instance.instance_id),
                instance.revision,
                instance.status.value,
                instance.prototype.prototype_id,
                instance.prototype.version,
                encode_model(instance),
                format_datetime(instance.updated_at),
            ),
        )

    async def _insert_skill_tree_projection(self, instance: AgentInstance) -> None:
        if instance.skill_tree is None:
            return
        await self._connection.execute(
            """
            INSERT INTO instance_skill_trees (
                instance_id, revision, tree_id, tree_version, tree_checksum
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(instance.instance_id),
                instance.revision,
                instance.skill_tree.tree_id,
                instance.skill_tree.version,
                instance.skill_tree.checksum,
            ),
        )

    def _decode(self, row: aiosqlite.Row) -> AgentInstance:
        instance = decode_model(
            str(row["payload_json"]),
            AgentInstance,
            repository=self.repository_name,
        )
        projections = {
            "instance_id": str(instance.instance_id) == row["instance_id"],
            "revision": instance.revision == row["revision"],
            "status": instance.status.value == row["status"],
            "prototype_id": (instance.prototype.prototype_id == row["prototype_id"]),
            "prototype_version": (
                instance.prototype.version == row["prototype_version"]
            ),
            "created_at": (format_datetime(instance.updated_at) == row["created_at"]),
            "configuration_checksum": (
                sha256_model(instance.configuration) == instance.prototype.checksum
            ),
        }
        for field, valid in projections.items():
            require_projection(
                valid,
                repository=self.repository_name,
                field=field,
            )
        _require_skill_tree_projection(
            instance.skill_tree,
            row,
            repository=self.repository_name,
        )
        return instance


class SqliteAgentSpecRepository(SqliteRepository):
    repository_name = "agent-specs"

    async def get(
        self,
        instance_id: UUID,
        revision: int,
    ) -> AgentSpec | None:
        row = await self._fetchone(
            """
            SELECT instance_id, revision, payload_json, checksum, created_at
            FROM agent_specs
            WHERE instance_id = ? AND revision = ?
            """,
            (str(instance_id), revision),
        )
        return None if row is None else self._decode(row)

    async def add_if_absent(self, spec: AgentSpec) -> bool:
        try:
            cursor = await self._connection.execute(
                """
                INSERT INTO agent_specs (
                    instance_id, revision, payload_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(instance_id, revision) DO NOTHING
                """,
                (
                    str(spec.instance_id),
                    spec.revision,
                    encode_model(spec),
                    spec.spec_checksum,
                    format_datetime(spec.generated_at),
                ),
            )
            try:
                return cursor.rowcount == 1
            finally:
                await cursor.close()
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    def _decode(self, row: aiosqlite.Row) -> AgentSpec:
        spec = decode_model(
            str(row["payload_json"]),
            AgentSpec,
            repository=self.repository_name,
        )
        projections = {
            "instance_id": str(spec.instance_id) == row["instance_id"],
            "revision": spec.revision == row["revision"],
            "checksum": spec.spec_checksum == row["checksum"],
            "spec_checksum": checksum_agent_spec(spec) == spec.spec_checksum,
            "created_at": format_datetime(spec.generated_at) == row["created_at"],
        }
        for field, valid in projections.items():
            require_projection(
                valid,
                repository=self.repository_name,
                field=field,
            )
        return spec


class SqliteAuditRepository(SqliteRepository):
    repository_name = "audit"

    async def append(self, event: AuditEvent) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, event_type, entity_type, entity_id,
                    entity_revision, actor, correlation_id, causation_id,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_id),
                    event.event_type.value,
                    event.entity_type.value,
                    event.entity_id,
                    event.entity_revision,
                    event.actor,
                    str(event.correlation_id),
                    None if event.causation_id is None else str(event.causation_id),
                    encode_json_object(event.payload),
                    format_datetime(event.created_at),
                ),
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def query(self, query: AuditQuery) -> Page[AuditEvent]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query.entity_type is not None:
            clauses.append("entity_type = ?")
            parameters.append(query.entity_type.value)
        if query.entity_id is not None:
            clauses.append("entity_id = ?")
            parameters.append(query.entity_id)
        if query.event_types:
            values = sorted(item.value for item in query.event_types)
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"event_type IN ({placeholders})")
            parameters.extend(values)
        if query.actor is not None:
            clauses.append("actor = ?")
            parameters.append(query.actor)
        if query.created_from is not None:
            clauses.append("created_at >= ?")
            parameters.append(format_datetime(query.created_from))
        if query.created_to is not None:
            clauses.append("created_at <= ?")
            parameters.append(format_datetime(query.created_to))

        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        count_row = await self._fetchone(
            f"SELECT COUNT(*) AS total FROM audit_events{where}",
            parameters,
        )
        if count_row is None:
            raise RepositoryUnavailableError(
                details={"repository": self.repository_name, "reason": "missing-count"}
            )
        offset = (query.page - 1) * query.page_size
        rows = await self._fetchall(
            f"""
            SELECT event_id, event_type, entity_type, entity_id,
                   entity_revision, actor, correlation_id, causation_id,
                   payload_json, created_at
            FROM audit_events{where}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page_size, offset),
        )
        return Page[AuditEvent](
            items=tuple(self._decode(row) for row in rows),
            page=query.page,
            page_size=query.page_size,
            total=int(count_row["total"]),
        )

    def _decode(self, row: aiosqlite.Row) -> AuditEvent:
        try:
            return AuditEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                entity_revision=row["entity_revision"],
                actor=row["actor"],
                correlation_id=row["correlation_id"],
                causation_id=row["causation_id"],
                payload=decode_json_object(
                    str(row["payload_json"]),
                    repository=self.repository_name,
                ),
                created_at=row["created_at"],
            )
        except (ValueError, TypeError) as exc:
            error = RepositoryUnavailableError(
                details={
                    "repository": self.repository_name,
                    "reason": "invalid-persisted-data",
                }
            )
            raise error from exc


class SqliteIdempotencyRepository(SqliteRepository):
    repository_name = "idempotency"

    async def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        row = await self._fetchone(
            """
            SELECT idempotency_key, operation, request_hash,
                   response_json, created_at, expires_at
            FROM idempotency_records
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        )
        if row is None:
            return None
        try:
            return IdempotencyRecord(
                idempotency_key=row["idempotency_key"],
                operation=row["operation"],
                request_hash=row["request_hash"],
                response=decode_json_object(
                    str(row["response_json"]),
                    repository=self.repository_name,
                ),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
        except (ValueError, TypeError) as exc:
            error = RepositoryUnavailableError(
                details={
                    "repository": self.repository_name,
                    "reason": "invalid-persisted-data",
                }
            )
            raise error from exc

    async def add(self, record: IdempotencyRecord) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO idempotency_records (
                    idempotency_key, operation, request_hash,
                    response_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.idempotency_key,
                    record.operation,
                    record.request_hash,
                    encode_json_object(record.response),
                    format_datetime(record.created_at),
                    format_datetime(record.expires_at),
                ),
            )
        except sqlite3.Error as exc:
            raise_database_error(self.repository_name, exc)

    async def delete_expired(self, expired_at: datetime) -> int:
        return await self._execute(
            "DELETE FROM idempotency_records WHERE expires_at <= ?",
            (format_datetime(expired_at),),
        )


def _require_skill_tree_projection(
    reference: SkillTreeRef | None,
    row: aiosqlite.Row,
    *,
    repository: str,
) -> None:
    projected = (row["tree_id"], row["tree_version"], row["tree_checksum"])
    expected = (
        (None, None, None)
        if reference is None
        else (reference.tree_id, reference.version, reference.checksum)
    )
    require_projection(
        projected == expected,
        repository=repository,
        field="skill_tree",
    )
