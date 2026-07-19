"""M1 domain snapshot validation tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import FrozenJsonObject
from agent_factory.domain.enums import (
    InjectionMode,
    InstanceStatus,
    KnowledgeKind,
    PrototypeStatus,
    ToolPermission,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    AgentSpec,
    DomainKnowledgeDraft,
    KnowledgeRef,
    KnowledgeSlot,
    PrototypeRef,
    ResolvedToolSpec,
)

CHECKSUM = "a" * 64
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_agent_definition_freezes_json_and_rejects_duplicate_tools(
    writer_definition: AgentDefinition,
) -> None:
    assert isinstance(writer_definition.output_schema, FrozenJsonObject)
    assert writer_definition.model_dump(mode="json")["output_schema"]["type"] == (
        "object"
    )

    with pytest.raises(ValidationError, match="tools contains duplicate names"):
        AgentDefinition(
            agent_type="writer-agent",
            role="Writer",
            system_prompt="Write.",
            tools=("document-search", "document-search"),
        )


def test_agent_definition_rejects_duplicate_slots(
    product_slot: KnowledgeSlot,
) -> None:
    with pytest.raises(ValidationError, match="slot names must be unique"):
        AgentDefinition(
            agent_type="writer-agent",
            role="Writer",
            system_prompt="Write.",
            knowledge_slots=(product_slot, product_slot),
        )


def test_metadata_values_must_be_strings() -> None:
    with pytest.raises(ValidationError, match="metadata values must be strings"):
        AgentDefinition(
            agent_type="writer-agent",
            role="Writer",
            system_prompt="Write.",
            metadata={"attempt": 1},
        )


def test_knowledge_slot_validates_cardinality_and_version_range() -> None:
    with pytest.raises(ValidationError, match="max_items must be 1"):
        KnowledgeSlot(
            name="product-docs",
            accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
            injection_mode=InjectionMode.RETRIEVAL,
            max_items=2,
        )
    with pytest.raises(ValidationError, match="min_version must be lower"):
        KnowledgeSlot(
            name="product-docs",
            accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
            min_version="2.0.0",
            max_version_exclusive="2.0.0",
            injection_mode=InjectionMode.RETRIEVAL,
        )
    with pytest.raises(ValidationError, match="at least 1"):
        KnowledgeSlot(
            name="product-docs",
            accepted_kinds=frozenset(),
            injection_mode=InjectionMode.RETRIEVAL,
        )


@pytest.mark.parametrize(
    ("content", "source_uri"),
    [(None, None), ("inline", "https://example.com/knowledge")],
)
def test_knowledge_draft_requires_exactly_one_source(
    content: str | None,
    source_uri: str | None,
) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        DomainKnowledgeDraft.model_validate(
            {
                "knowledge_id": "product-docs",
                "version": "1.0.0",
                "name": "Product Docs",
                "kind": KnowledgeKind.DOCUMENT,
                "content": content,
                "source_uri": source_uri,
                "checksum": CHECKSUM,
            }
        )


def test_knowledge_draft_accepts_immutable_json_content() -> None:
    draft = DomainKnowledgeDraft(
        knowledge_id="product-data",
        version="1.0.0",
        name="Product Data",
        kind=KnowledgeKind.DATASET,
        content={"records": [{"id": 1}]},
        checksum=CHECKSUM,
    )

    assert isinstance(draft.content, FrozenJsonObject)
    assert draft.model_dump(mode="json")["content"] == {"records": [{"id": 1}]}


@pytest.mark.parametrize("version", ["v1.0.0", "01.0.0", "1.0.0-beta"])
def test_prototype_ref_rejects_non_alpha_semver(version: str) -> None:
    with pytest.raises(ValidationError):
        PrototypeRef(
            prototype_id="writer-agent",
            version=version,
            checksum=CHECKSUM,
        )


def test_prototype_status_requires_consistent_metadata(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    base = {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": writer_definition,
        "checksum": CHECKSUM,
        "created_at": fixed_now,
        "created_by": "owner",
    }

    with pytest.raises(ValidationError, match="requires published_at"):
        AgentPrototype.model_validate({**base, "status": PrototypeStatus.PUBLISHED})
    with pytest.raises(ValidationError, match="requires publication"):
        AgentPrototype.model_validate(
            {
                **base,
                "status": PrototypeStatus.DEPRECATED,
                "deprecation_reason": "Replaced",
            }
        )

    published = AgentPrototype.model_validate(
        {
            **base,
            "status": PrototypeStatus.PUBLISHED,
            "published_at": fixed_now,
        }
    )
    assert published.status is PrototypeStatus.PUBLISHED


def test_persisted_timestamps_must_be_timezone_aware(
    writer_definition: AgentDefinition,
) -> None:
    with pytest.raises(ValidationError, match="timezone_aware"):
        AgentPrototype(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=writer_definition,
            checksum=CHECKSUM,
            created_at=datetime(2026, 7, 15),
            created_by="owner",
        )


def test_instance_rejects_updated_at_before_creation(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    with pytest.raises(ValidationError, match="must not precede"):
        AgentInstance(
            instance_id=INSTANCE_ID,
            prototype=PrototypeRef(
                prototype_id="writer-agent",
                version="1.0.0",
                checksum=CHECKSUM,
            ),
            revision=1,
            status=InstanceStatus.CREATED,
            configuration=writer_definition,
            created_at=fixed_now,
            updated_at=fixed_now - timedelta(seconds=1),
            created_by="owner",
        )


def test_agent_spec_serializes_runtime_neutral_contract(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    prototype = PrototypeRef(
        prototype_id="writer-agent",
        version="1.0.0",
        checksum=CHECKSUM,
    )
    spec = AgentSpec(
        instance_id=INSTANCE_ID,
        revision=2,
        prototype=prototype,
        agent_type=writer_definition.agent_type,
        role=writer_definition.role,
        system_prompt=writer_definition.system_prompt,
        tools=(
            ResolvedToolSpec(
                name="document-search",
                version="1.0.0",
                description="Search bound documents.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                permission_tags=frozenset({ToolPermission.READ_ONLY}),
            ),
        ),
        knowledge=(
            KnowledgeRef(
                slot_name="product-docs",
                knowledge_id="agent-factory-docs",
                version="1.0.0",
                checksum=CHECKSUM,
                injection_mode=InjectionMode.RETRIEVAL,
            ),
        ),
        output_schema=writer_definition.output_schema,
        generated_at=fixed_now.astimezone(UTC),
        spec_checksum="b" * 64,
    )

    payload = spec.model_dump(mode="json")
    assert payload["schema_version"] == "1.0"
    assert payload["prototype"]["checksum"] == CHECKSUM
    assert payload["tools"][0]["name"] == "document-search"
    assert payload["knowledge"][0]["version"] == "1.0.0"
