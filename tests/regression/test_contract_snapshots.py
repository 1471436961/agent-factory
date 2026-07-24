"""Golden regression evidence for public and provenance contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from agent_factory.domain.services.spec import checksum_agent_spec
from agent_factory.interfaces.demo.fixtures import KNOWLEDGE_CONTENT
from scripts.contract_snapshots import (
    PROJECT_ROOT,
    SNAPSHOT_PATHS,
    build_snapshot_documents,
    build_writer_agent_spec,
    build_writer_audit_events,
    project_writer_agent_spec,
    project_writer_audit_timeline,
    render_snapshot_json,
    stale_snapshots,
    write_snapshots,
)

SPEC_KEYS = {
    "schema_version",
    "revision",
    "prototype",
    "agent_type",
    "role",
    "tools",
    "knowledge",
    "output_schema",
    "active_skill_nodes",
    "skill_tree",
    "runtime_target",
    "spec_checksum",
}
AUDIT_KEYS = {
    "sequence",
    "event_type",
    "entity_type",
    "entity_id",
    "entity_revision",
    "actor",
    "correlation",
    "causation",
    "payload",
}
AUDIT_PAYLOAD_KEYS = {
    "prototype.registered": {"version", "checksum", "status", "skill_tree"},
    "prototype.published": {"version", "status"},
    "knowledge.registered": {"version", "kind", "checksum", "source"},
    "instance.cloned": {
        "prototype_id",
        "prototype_version",
        "prototype_checksum",
        "runtime_target",
        "skill_tree",
    },
    "knowledge.bound": {
        "slot_name",
        "knowledge_id",
        "knowledge_version",
        "knowledge_checksum",
        "injection_mode",
        "replaced",
    },
    "spec.exported": {"schema_version", "spec_checksum", "runtime_target"},
}


def test_snapshot_generation_is_byte_stable_and_canonical() -> None:
    first = build_snapshot_documents()
    second = build_snapshot_documents()

    assert first == second
    assert set(first) == set(SNAPSHOT_PATHS.values())
    for content in first.values():
        assert content.endswith(b"\n")
        parsed = json.loads(content)
        assert content == render_snapshot_json(parsed)


def test_committed_snapshots_exactly_match_current_contracts() -> None:
    expected = build_snapshot_documents()

    for relative_path, content in expected.items():
        assert (PROJECT_ROOT / relative_path).read_bytes() == content
    assert stale_snapshots() == ()


def test_snapshot_writer_detects_missing_and_stale_files(tmp_path: Path) -> None:
    written = write_snapshots(tmp_path)
    assert len(written) == len(SNAPSHOT_PATHS)
    assert stale_snapshots(tmp_path) == ()

    stale_path = tmp_path / SNAPSHOT_PATHS["writer_agent_spec"]
    stale_path.write_text("{}\n", encoding="utf-8")

    assert stale_snapshots(tmp_path) == (stale_path,)


def test_writer_agent_spec_projection_has_an_explicit_semantic_allowlist() -> None:
    spec = build_writer_agent_spec()
    projection = project_writer_agent_spec(spec)

    assert set(projection) == SPEC_KEYS
    assert projection["schema_version"] == "1.1"
    assert projection["revision"] == 2
    assert projection["active_skill_nodes"] == []
    assert projection["spec_checksum"] == checksum_agent_spec(spec)
    assert "instance_id" not in projection
    assert "generated_at" not in projection
    assert "system_prompt" not in projection
    assert "metadata" not in projection

    tools = cast(list[dict[str, object]], projection["tools"])
    assert [(tool["name"], tool["version"]) for tool in tools] == [
        ("document-search", "1.0.0")
    ]
    assert tools[0]["permission_tags"] == ["read-only"]


def test_writer_audit_projection_preserves_order_and_allowlisted_payloads() -> None:
    spec = build_writer_agent_spec()
    timeline = project_writer_audit_timeline(build_writer_audit_events(spec))

    assert [row["sequence"] for row in timeline] == list(range(1, 7))
    assert [row["event_type"] for row in timeline] == list(AUDIT_PAYLOAD_KEYS)
    for row in timeline:
        assert set(row) == AUDIT_KEYS
        event_type = cast(str, row["event_type"])
        payload = cast(dict[str, object], row["payload"])
        assert set(payload) == AUDIT_PAYLOAD_KEYS[event_type]
        assert row["correlation"] == "$writer-production-chain"
        assert row["causation"] is None

    serialized = json.dumps(timeline, ensure_ascii=False)
    assert KNOWLEDGE_CONTENT not in serialized
    assert "Write concise technical documentation from verified knowledge." not in (
        serialized
    )
