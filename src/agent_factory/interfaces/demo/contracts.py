"""UI-safe immutable state and display contracts for the fixed M3 demo."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DemoModel(BaseModel):
    """Strict immutable base without importing Agent Factory domain models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DemoPhase(StrEnum):
    NEW = "new"
    READY_TO_RUN = "ready-to-run"
    AWAITING_REVIEW = "awaiting-review"
    PROMOTED = "promoted"


class DemoSourceView(DemoModel):
    source_type: str = Field(min_length=1, max_length=32)
    source_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class DemoRunView(DemoModel):
    task_id: UUID
    status: str = Field(min_length=1, max_length=32)
    instance_revision: int = Field(ge=1)
    agent_spec_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_name: str = Field(min_length=1, max_length=64)
    tool_call_count: int = Field(ge=0)
    content_preview: str = Field(default="", max_length=1_000)
    structured_keys: tuple[str, ...] = ()


class DemoAuditRow(DemoModel):
    created_at: datetime
    event_type: str = Field(min_length=1, max_length=128)
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=1, max_length=128)
    entity_revision: int | None = Field(default=None, ge=1)
    correlation_id: UUID


class DemoErrorView(DemoModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=1_000)
    correlation_id: UUID | None = None


class DemoSession(DemoModel):
    """Checkpointed browser-session state; SQLite remains the truth source."""

    workflow_id: UUID = Field(default_factory=uuid4)
    phase: DemoPhase = DemoPhase.NEW
    completed_operations: frozenset[str] = frozenset()
    instance_id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    sources: tuple[DemoSourceView, ...] = ()
    active_nodes: tuple[str, ...] = ()
    spec_json: str | None = Field(default=None, max_length=256_000)
    run_result_json: str | None = Field(default=None, max_length=256_000)
    run_view: DemoRunView | None = None
    report_id: UUID | None = None
    review_id: UUID | None = None
    audit_rows: tuple[DemoAuditRow, ...] = ()

    @model_validator(mode="after")
    def phase_has_required_evidence(self) -> Self:
        if self.phase is not DemoPhase.NEW and (
            self.instance_id is None or self.revision is None or self.spec_json is None
        ):
            raise ValueError("advanced demo phases require instance and spec evidence")
        if self.phase in {DemoPhase.AWAITING_REVIEW, DemoPhase.PROMOTED} and (
            self.run_result_json is None
            or self.run_view is None
            or self.report_id is None
        ):
            raise ValueError("review phases require runtime and evaluation evidence")
        if self.phase is DemoPhase.PROMOTED and (
            self.review_id is None or self.active_nodes != ("mid-writer",)
        ):
            raise ValueError("promoted phase requires review and active mid-writer")
        source_types = [source.source_type for source in self.sources]
        if len(source_types) != len(set(source_types)):
            raise ValueError("demo sources must be unique by type")
        return self

    def is_completed(self, operation: str) -> bool:
        return operation in self.completed_operations

    def source(self, source_type: str) -> DemoSourceView | None:
        return next(
            (item for item in self.sources if item.source_type == source_type),
            None,
        )

    def checkpoint(self, operation: str, **changes: object) -> DemoSession:
        payload = self.model_dump(mode="python")
        payload.update(changes)
        payload["completed_operations"] = self.completed_operations | {operation}
        return DemoSession.model_validate(payload)

    def replace_source(self, source: DemoSourceView) -> DemoSession:
        retained = tuple(
            item for item in self.sources if item.source_type != source.source_type
        )
        payload = self.model_dump(mode="python")
        payload["sources"] = tuple(
            sorted((*retained, source), key=lambda item: item.source_type)
        )
        return DemoSession.model_validate(payload)


class DemoActionResult(DemoModel):
    session: DemoSession
    error: DemoErrorView | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
