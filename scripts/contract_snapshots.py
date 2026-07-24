"""Generate and verify reviewed OpenAPI and Writer semantic snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

from agent_factory.application.audit import AuditEventFactory
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import InstanceStatus, PrototypeStatus
from agent_factory.domain.models import (
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledge,
    KnowledgeBinding,
    PrototypeRef,
)
from agent_factory.domain.services.evaluation import checksum_evaluation_suite
from agent_factory.domain.services.skills import checksum_skill_tree
from agent_factory.domain.services.spec import AgentSpecBuilder
from agent_factory.infrastructure.runtime import default_tool_registry
from agent_factory.interfaces.api.app import create_app
from agent_factory.interfaces.demo.fixtures import (
    KNOWLEDGE_ID,
    KNOWLEDGE_VERSION,
    RUNTIME_NAME,
    evaluation_suite_request,
    knowledge_request,
    prototype_request,
    skill_tree_request,
)
from agent_factory.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        "openapi": Path("docs/generated/openapi-v1.json"),
        "writer_agent_spec": Path(
            "tests/regression/snapshots/writer-agent-spec-v1.json"
        ),
        "writer_audit_timeline": Path(
            "tests/regression/snapshots/writer-audit-timeline-v1.json"
        ),
    }
)

SNAPSHOT_ACTOR = "snapshot-owner"
SNAPSHOT_CORRELATION_ID = UUID("00000000-0000-0000-0000-000000004200")
SNAPSHOT_INSTANCE_ID = UUID("00000000-0000-0000-0000-000000004201")
SNAPSHOT_STARTED_AT = datetime(2026, 7, 24, 8, tzinfo=UTC)


class _SequentialIdGenerator:
    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def render_snapshot_json(document: object) -> bytes:
    """Return the repository's review-friendly canonical snapshot encoding."""

    rendered = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


def build_openapi_document() -> dict[str, object]:
    """Build OpenAPI without reading environment settings or running lifespan I/O."""

    snapshot_data_dir = PROJECT_ROOT / ".tmp" / "contract-snapshots"
    settings = Settings.model_validate(
        {
            "environment": "contract-snapshot",
            "database_url": (
                f"sqlite+aiosqlite:///{(snapshot_data_dir / 'unused.db').as_posix()}"
            ),
            "api_prefix": "/api/v1",
            "auth_token": None,
            "openai_api_key": None,
            "anthropic_api_key": None,
            "data_dir": snapshot_data_dir,
        }
    )
    return cast(dict[str, object], create_app(settings).openapi())


def build_writer_agent_spec() -> AgentSpec:
    """Build a deterministic revision-2 Writer contract from current domain code."""

    suite_checksum = checksum_evaluation_suite(evaluation_suite_request())
    tree_checksum = checksum_skill_tree(skill_tree_request(suite_checksum))
    request = prototype_request(tree_checksum)
    if request.skill_tree is None:
        raise RuntimeError("Writer snapshot requires a skill-tree reference")

    prototype_checksum = sha256_model(request.definition)
    prototype_ref = PrototypeRef(
        prototype_id=request.prototype_id,
        version=request.version,
        checksum=prototype_checksum,
    )
    slot = request.definition.knowledge_slots[0]
    binding = KnowledgeBinding(
        slot_name=slot.name,
        knowledge_id=KNOWLEDGE_ID,
        knowledge_version=KNOWLEDGE_VERSION,
        knowledge_checksum=knowledge_request().checksum,
        injection_mode=slot.injection_mode,
        bound_at=SNAPSHOT_STARTED_AT + timedelta(seconds=3),
        bound_by=SNAPSHOT_ACTOR,
    )
    instance = AgentInstance(
        instance_id=SNAPSHOT_INSTANCE_ID,
        prototype=prototype_ref,
        revision=2,
        status=InstanceStatus.CREATED,
        configuration=request.definition,
        knowledge_bindings=(binding,),
        skill_tree=request.skill_tree,
        runtime_target=RUNTIME_NAME,
        created_at=SNAPSHOT_STARTED_AT + timedelta(seconds=2),
        updated_at=SNAPSHOT_STARTED_AT + timedelta(seconds=3),
        created_by=SNAPSHOT_ACTOR,
    )
    tools = tuple(
        definition.resolved_spec()
        for definition in default_tool_registry().definitions()
        if definition.name in request.definition.tools
    )
    if {tool.name for tool in tools} != set(request.definition.tools):
        raise RuntimeError("Writer snapshot tool registry does not match definition")
    return AgentSpecBuilder().build(
        instance=instance,
        tools=tools,
        generated_at=SNAPSHOT_STARTED_AT + timedelta(seconds=4),
    )


def project_writer_agent_spec(spec: AgentSpec) -> dict[str, object]:
    """Project only reviewed runtime and provenance semantics from AgentSpec."""

    serialized = cast(dict[str, object], json.loads(spec.model_dump_json()))
    serialized_tools = cast(list[dict[str, object]], serialized["tools"])
    return {
        "schema_version": serialized["schema_version"],
        "revision": serialized["revision"],
        "prototype": serialized["prototype"],
        "agent_type": serialized["agent_type"],
        "role": serialized["role"],
        "tools": [
            {
                "name": tool["name"],
                "version": tool["version"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
                "output_schema": tool["output_schema"],
                "permission_tags": sorted(cast(list[str], tool["permission_tags"])),
            }
            for tool in serialized_tools
        ],
        "knowledge": serialized["knowledge"],
        "output_schema": serialized["output_schema"],
        "active_skill_nodes": sorted(cast(list[str], serialized["active_skill_nodes"])),
        "skill_tree": serialized["skill_tree"],
        "runtime_target": serialized["runtime_target"],
        "spec_checksum": serialized["spec_checksum"],
    }


def build_writer_audit_events(spec: AgentSpec) -> tuple[AuditEvent, ...]:
    """Build the six-event deterministic Writer production timeline."""

    suite_checksum = checksum_evaluation_suite(evaluation_suite_request())
    tree_checksum = checksum_skill_tree(skill_tree_request(suite_checksum))
    request = prototype_request(tree_checksum)
    if request.skill_tree is None:
        raise RuntimeError("Writer snapshot requires a skill-tree reference")

    prototype = AgentPrototype(
        prototype_id=request.prototype_id,
        version=request.version,
        status=PrototypeStatus.PUBLISHED,
        definition=request.definition,
        skill_tree=request.skill_tree,
        checksum=spec.prototype.checksum,
        created_at=SNAPSHOT_STARTED_AT,
        created_by=SNAPSHOT_ACTOR,
        published_at=SNAPSHOT_STARTED_AT,
    )
    knowledge_draft = knowledge_request()
    knowledge = DomainKnowledge.model_validate(
        {
            **knowledge_draft.model_dump(mode="python"),
            "created_at": SNAPSHOT_STARTED_AT + timedelta(seconds=1),
            "created_by": SNAPSHOT_ACTOR,
        }
    )
    unbound = AgentInstance(
        instance_id=spec.instance_id,
        prototype=spec.prototype,
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=request.definition,
        skill_tree=request.skill_tree,
        runtime_target=spec.runtime_target,
        created_at=SNAPSHOT_STARTED_AT + timedelta(seconds=2),
        updated_at=SNAPSHOT_STARTED_AT + timedelta(seconds=2),
        created_by=SNAPSHOT_ACTOR,
    )
    binding = KnowledgeBinding(
        slot_name=spec.knowledge[0].slot_name,
        knowledge_id=spec.knowledge[0].knowledge_id,
        knowledge_version=spec.knowledge[0].version,
        knowledge_checksum=spec.knowledge[0].checksum,
        injection_mode=spec.knowledge[0].injection_mode,
        bound_at=SNAPSHOT_STARTED_AT + timedelta(seconds=3),
        bound_by=SNAPSHOT_ACTOR,
    )
    bound = AgentInstance.model_validate(
        {
            **unbound.model_dump(mode="python"),
            "revision": 2,
            "knowledge_bindings": (binding,),
            "updated_at": SNAPSHOT_STARTED_AT + timedelta(seconds=3),
        }
    )
    factory = AuditEventFactory(_SequentialIdGenerator(start=4_300))
    return (
        factory.prototype_registered(
            prototype,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT,
        ),
        factory.prototype_published(
            prototype,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT,
        ),
        factory.knowledge_registered(
            knowledge,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT + timedelta(seconds=1),
        ),
        factory.instance_cloned(
            unbound,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT + timedelta(seconds=2),
        ),
        factory.knowledge_bound(
            bound,
            binding,
            replaced=False,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT + timedelta(seconds=3),
        ),
        factory.spec_exported(
            spec,
            actor=SNAPSHOT_ACTOR,
            correlation_id=SNAPSHOT_CORRELATION_ID,
            at=SNAPSHOT_STARTED_AT + timedelta(seconds=4),
        ),
    )


def project_writer_audit_timeline(
    events: Sequence[AuditEvent],
) -> list[dict[str, object]]:
    """Remove transport noise while preserving event order and audit semantics."""

    projected: list[dict[str, object]] = []
    for sequence, event in enumerate(events, start=1):
        serialized = cast(dict[str, object], json.loads(event.model_dump_json()))
        entity_id = (
            "$writer-instance"
            if event.entity_id == str(SNAPSHOT_INSTANCE_ID)
            else event.entity_id
        )
        projected.append(
            {
                "sequence": sequence,
                "event_type": event.event_type.value,
                "entity_type": event.entity_type.value,
                "entity_id": entity_id,
                "entity_revision": event.entity_revision,
                "actor": event.actor,
                "correlation": "$writer-production-chain",
                "causation": None,
                "payload": serialized["payload"],
            }
        )
    return projected


def build_snapshot_documents() -> dict[Path, bytes]:
    """Build every committed contract artifact without filesystem writes."""

    spec = build_writer_agent_spec()
    events = build_writer_audit_events(spec)
    return {
        SNAPSHOT_PATHS["openapi"]: render_snapshot_json(build_openapi_document()),
        SNAPSHOT_PATHS["writer_agent_spec"]: render_snapshot_json(
            project_writer_agent_spec(spec)
        ),
        SNAPSHOT_PATHS["writer_audit_timeline"]: render_snapshot_json(
            project_writer_audit_timeline(events)
        ),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_snapshots(root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    """Atomically replace every reviewed snapshot and return written paths."""

    written: list[Path] = []
    for relative_path, content in build_snapshot_documents().items():
        target = root / relative_path
        _atomic_write(target, content)
        written.append(target)
    return tuple(written)


def stale_snapshots(root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    """Return missing or byte-different snapshot paths without modifying them."""

    stale = [
        root / relative_path
        for relative_path, expected in build_snapshot_documents().items()
        if not (root / relative_path).is_file()
        or (root / relative_path).read_bytes() != expected
    ]
    return tuple(stale)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="replace snapshots")
    mode.add_argument("--check", action="store_true", help="verify snapshots")
    arguments = parser.parse_args(argv)

    if arguments.write:
        for path in write_snapshots():
            print(f"wrote {path.relative_to(PROJECT_ROOT)} sha256={_digest(path)}")
        return 0

    stale = stale_snapshots()
    if stale:
        for path in stale:
            print(f"stale {path.relative_to(PROJECT_ROOT)}")
        print("run: python -m scripts.contract_snapshots --write")
        return 1
    for relative_path in build_snapshot_documents():
        path = PROJECT_ROOT / relative_path
        print(f"verified {relative_path} sha256={_digest(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
