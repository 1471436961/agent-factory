"""Unit tests for pure M1 production policies."""

from datetime import datetime
from uuid import UUID

import pytest

from agent_factory.application.commands import KnowledgeSelection
from agent_factory.domain.common import checksum_knowledge_content, sha256_model
from agent_factory.domain.enums import (
    InjectionMode,
    InstanceStatus,
    KnowledgeKind,
    PrototypeStatus,
)
from agent_factory.domain.errors import (
    InvalidPrototypeStatusError,
    KnowledgeCardinalityError,
    KnowledgeKindMismatchError,
    KnowledgeVersionMismatchError,
    MissingKnowledgeBindingError,
    UnknownKnowledgeSlotError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentInstance,
    AgentPrototype,
    DomainKnowledge,
    KnowledgeBinding,
    KnowledgeSlot,
    PrototypeRef,
)
from agent_factory.domain.services.knowledge import KnowledgeBindingPolicy
from agent_factory.domain.services.prototype import PrototypePolicy
from agent_factory.domain.services.spec import AgentSpecBuilder, checksum_agent_spec

CHECKSUM = "a" * 64
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")


def _knowledge(
    *,
    fixed_now: datetime,
    knowledge_id: str = "agent-factory-docs",
    version: str = "1.0.0",
    kind: KnowledgeKind = KnowledgeKind.DOCUMENT,
) -> DomainKnowledge:
    content = f"{knowledge_id}:{version}"
    return DomainKnowledge(
        knowledge_id=knowledge_id,
        version=version,
        name=knowledge_id,
        kind=kind,
        content=content,
        checksum=checksum_knowledge_content(content),
        created_at=fixed_now,
        created_by="owner",
    )


def _prototype(
    *,
    fixed_now: datetime,
    definition: AgentDefinition,
    status: PrototypeStatus = PrototypeStatus.DRAFT,
) -> AgentPrototype:
    return AgentPrototype(
        prototype_id="writer-agent",
        version="1.0.0",
        status=status,
        definition=definition,
        checksum=sha256_model(definition),
        created_at=fixed_now,
        created_by="owner",
        published_at=(fixed_now if status is not PrototypeStatus.DRAFT else None),
        deprecation_reason=(
            "replaced" if status is PrototypeStatus.DEPRECATED else None
        ),
    )


def test_prototype_policy_derives_valid_immutable_transitions(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    policy = PrototypePolicy()
    draft = _prototype(fixed_now=fixed_now, definition=writer_definition)

    published = policy.publish(draft, at=fixed_now)
    deprecated = policy.deprecate(published, reason="replaced")

    assert draft.status is PrototypeStatus.DRAFT
    assert published.status is PrototypeStatus.PUBLISHED
    assert deprecated.status is PrototypeStatus.DEPRECATED
    with pytest.raises(InvalidPrototypeStatusError):
        policy.publish(published, at=fixed_now)
    with pytest.raises(InvalidPrototypeStatusError):
        policy.deprecate(draft, reason="invalid")


def test_knowledge_policy_builds_canonical_bindings(
    fixed_now: datetime,
) -> None:
    definition = AgentDefinition(
        agent_type="writer-agent",
        role="Writer",
        system_prompt="Write.",
        knowledge_slots=(
            KnowledgeSlot(
                name="product-docs",
                accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
                min_version="1.0.0",
                max_version_exclusive="2.0.0",
                injection_mode=InjectionMode.RETRIEVAL,
                multiple=True,
                max_items=2,
            ),
        ),
    )
    selections = (
        KnowledgeSelection(
            slot_name="product-docs",
            knowledge_id="release-notes",
            version="1.10.0",
        ),
        KnowledgeSelection(
            slot_name="product-docs",
            knowledge_id="agent-factory-docs",
            version="1.2.0",
        ),
    )
    packages = tuple(
        _knowledge(
            fixed_now=fixed_now,
            knowledge_id=selection.knowledge_id,
            version=selection.version,
        )
        for selection in selections
    )

    bindings = KnowledgeBindingPolicy().validate_and_build(
        definition=definition,
        selections=selections,
        packages=packages,
        bound_at=fixed_now,
        bound_by="owner",
    )

    assert [binding.knowledge_id for binding in bindings] == [
        "agent-factory-docs",
        "release-notes",
    ]


@pytest.mark.parametrize(
    ("selection", "package", "error"),
    [
        (
            KnowledgeSelection(
                slot_name="unknown-slot",
                knowledge_id="agent-factory-docs",
                version="1.0.0",
            ),
            None,
            UnknownKnowledgeSlotError,
        ),
        (
            KnowledgeSelection(
                slot_name="product-docs",
                knowledge_id="agent-factory-docs",
                version="0.9.0",
            ),
            "document",
            KnowledgeVersionMismatchError,
        ),
        (
            KnowledgeSelection(
                slot_name="product-docs",
                knowledge_id="agent-factory-docs",
                version="1.0.0",
            ),
            "policy",
            KnowledgeKindMismatchError,
        ),
    ],
)
def test_knowledge_policy_rejects_slot_kind_and_version_mismatches(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
    selection: KnowledgeSelection,
    package: str | None,
    error: type[Exception],
) -> None:
    packages = (
        ()
        if package is None
        else (
            _knowledge(
                fixed_now=fixed_now,
                version=selection.version,
                kind=KnowledgeKind(package),
            ),
        )
    )

    with pytest.raises(error):
        KnowledgeBindingPolicy().validate_and_build(
            definition=writer_definition,
            selections=(selection,),
            packages=packages,
            bound_at=fixed_now,
            bound_by="owner",
        )


def test_knowledge_policy_requires_slots_and_enforces_cardinality(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    policy = KnowledgeBindingPolicy()
    with pytest.raises(MissingKnowledgeBindingError):
        policy.validate_and_build(
            definition=writer_definition,
            selections=(),
            packages=(),
            bound_at=fixed_now,
            bound_by="owner",
        )

    selections = tuple(
        KnowledgeSelection(
            slot_name="product-docs",
            knowledge_id=knowledge_id,
            version="1.0.0",
        )
        for knowledge_id in ("product-docs-one", "product-docs-two")
    )
    packages = tuple(
        _knowledge(fixed_now=fixed_now, knowledge_id=item.knowledge_id)
        for item in selections
    )
    with pytest.raises(KnowledgeCardinalityError):
        policy.validate_and_build(
            definition=writer_definition,
            selections=selections,
            packages=packages,
            bound_at=fixed_now,
            bound_by="owner",
        )


def test_spec_builder_revalidates_required_slots_and_is_deterministic(
    fixed_now: datetime,
    writer_definition: AgentDefinition,
) -> None:
    prototype = PrototypeRef(
        prototype_id="writer-agent",
        version="1.0.0",
        checksum=CHECKSUM,
    )
    empty = AgentInstance(
        instance_id=INSTANCE_ID,
        prototype=prototype,
        revision=1,
        status=InstanceStatus.CREATED,
        configuration=writer_definition,
        created_at=fixed_now,
        updated_at=fixed_now,
        created_by="owner",
    )
    builder = AgentSpecBuilder()
    with pytest.raises(MissingKnowledgeBindingError):
        builder.build(instance=empty, tools=(), generated_at=fixed_now)

    bound = AgentInstance.model_validate(
        {
            **empty.model_dump(mode="python"),
            "revision": 2,
            "knowledge_bindings": (
                KnowledgeBinding(
                    slot_name="product-docs",
                    knowledge_id="agent-factory-docs",
                    knowledge_version="1.0.0",
                    knowledge_checksum=CHECKSUM,
                    injection_mode=InjectionMode.RETRIEVAL,
                    bound_at=fixed_now,
                    bound_by="owner",
                ),
            ),
        }
    )
    first = builder.build(instance=bound, tools=(), generated_at=fixed_now)
    second = builder.build(instance=bound, tools=(), generated_at=fixed_now)

    assert first == second
    assert first.spec_checksum == checksum_agent_spec(first)
