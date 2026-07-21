"""Compatibility tests for M1 snapshots after adding M2 provenance fields."""

from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.domain.enums import InstanceStatus
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    PrototypeRef,
)
from agent_factory.domain.references import SkillTreeRef
from agent_factory.domain.services.spec import AgentSpecBuilder, checksum_agent_spec

CHECKSUM = "a" * 64
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")
M1_GOLDEN_SPEC_CHECKSUM = (
    "e979beccb60f339b3a846fd5ca8c1916a341b0a29daa1260fb0396e14a59dc0b"
)


def _m1_definition_payload() -> dict[str, object]:
    return {
        "agent_type": "writer-agent",
        "role": "Writer",
        "system_prompt": "Write.",
        "tools": [],
        "capabilities": [],
        "output_schema": {},
        "knowledge_slots": [],
        "metadata": {},
    }


def test_m1_snapshots_without_skill_tree_still_validate(fixed_now: datetime) -> None:
    prototype = AgentPrototype.model_validate(
        {
            "prototype_id": "writer-agent",
            "version": "1.0.0",
            "status": "draft",
            "definition": _m1_definition_payload(),
            "checksum": CHECKSUM,
            "created_at": fixed_now.isoformat(),
            "created_by": "owner",
            "published_at": None,
            "deprecation_reason": None,
        }
    )
    instance = AgentInstance.model_validate(
        {
            "instance_id": str(INSTANCE_ID),
            "prototype": {
                "prototype_id": "writer-agent",
                "version": "1.0.0",
                "checksum": CHECKSUM,
            },
            "revision": 1,
            "status": "created",
            "configuration": _m1_definition_payload(),
            "knowledge_bindings": [],
            "active_skill_nodes": [],
            "runtime_target": None,
            "created_at": fixed_now.isoformat(),
            "updated_at": fixed_now.isoformat(),
            "created_by": "owner",
        }
    )
    spec = AgentSpec.model_validate(
        {
            "schema_version": "1.0",
            "instance_id": str(INSTANCE_ID),
            "revision": 1,
            "prototype": instance.prototype.model_dump(mode="json"),
            "agent_type": "writer-agent",
            "role": "Writer",
            "system_prompt": "Write.",
            "tools": [],
            "knowledge": [],
            "output_schema": {},
            "active_skill_nodes": [],
            "runtime_target": None,
            "generated_at": fixed_now.isoformat(),
            "spec_checksum": M1_GOLDEN_SPEC_CHECKSUM,
            "metadata": {},
        }
    )

    assert prototype.skill_tree is None
    assert instance.skill_tree is None
    assert spec.skill_tree is None
    assert spec.schema_version == "1.0"


def test_m1_spec_checksum_remains_byte_compatible(fixed_now: datetime) -> None:
    definition = AgentDefinition(
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write.",
    )
    instance = AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=definition,
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="owner",
    )

    spec = AgentSpecBuilder().build(
        instance=instance,
        tools=(),
        generated_at=fixed_now,
    )

    assert spec.schema_version == "1.0"
    assert spec.spec_checksum == M1_GOLDEN_SPEC_CHECKSUM
    assert spec.spec_checksum == checksum_agent_spec(spec)


def test_skill_tree_specs_use_1_1_and_include_provenance_in_checksum(
    fixed_now: datetime,
) -> None:
    skill_tree = SkillTreeRef(
        tree_id="writer-skills",
        version="1.0.0",
        checksum="b" * 64,
    )
    instance = AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=PrototypeRef(
            prototype_id="writer-agent",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=AgentDefinition(
            agent_type="writer-agent",
            role="Writer",
            system_prompt="Write.",
        ),
        skill_tree=skill_tree,
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="owner",
    )

    spec = AgentSpecBuilder().build(
        instance=instance,
        tools=(),
        generated_at=fixed_now,
    )
    changed = spec.model_copy(
        update={"skill_tree": skill_tree.model_copy(update={"checksum": "c" * 64})}
    )

    assert spec.schema_version == "1.1"
    assert spec.skill_tree == skill_tree
    assert spec.spec_checksum == checksum_agent_spec(spec)
    assert checksum_agent_spec(changed) != spec.spec_checksum


def test_schema_versions_reject_incompatible_skill_tree_combinations(
    fixed_now: datetime,
) -> None:
    base = {
        "instance_id": INSTANCE_ID,
        "revision": 1,
        "prototype": {
            "prototype_id": "writer-agent",
            "version": "1.0.0",
            "checksum": CHECKSUM,
        },
        "agent_type": "writer-agent",
        "role": "Writer",
        "system_prompt": "Write.",
        "tools": (),
        "knowledge": (),
        "output_schema": {},
        "generated_at": fixed_now,
        "spec_checksum": CHECKSUM,
    }
    skill_tree = {
        "tree_id": "writer-skills",
        "version": "1.0.0",
        "checksum": CHECKSUM,
    }
    with pytest.raises(ValidationError, match=r"1\.0 cannot contain"):
        AgentSpec.model_validate(
            {**base, "schema_version": "1.0", "skill_tree": skill_tree}
        )
    with pytest.raises(ValidationError, match=r"1\.1 requires"):
        AgentSpec.model_validate({**base, "schema_version": "1.1", "skill_tree": None})
