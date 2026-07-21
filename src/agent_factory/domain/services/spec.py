"""Pure AgentSpec construction from a validated instance snapshot."""

from collections import Counter
from datetime import datetime

from agent_factory.domain.common import semver_tuple, sha256_model
from agent_factory.domain.errors import (
    KnowledgeCardinalityError,
    MissingKnowledgeBindingError,
    UnknownKnowledgeSlotError,
)
from agent_factory.domain.models import (
    AgentInstance,
    AgentSpec,
    KnowledgeRef,
    ResolvedToolSpec,
)


def checksum_agent_spec(spec: AgentSpec) -> str:
    """Hash a spec while preserving the published AgentSpec 1.0 contract."""

    excluded = {"spec_checksum"}
    if spec.schema_version == "1.0":
        excluded.add("skill_tree")
    return sha256_model(spec, exclude=excluded)


class AgentSpecBuilder:
    """Revalidate export invariants and build a checksummed runtime contract."""

    def build(
        self,
        *,
        instance: AgentInstance,
        tools: tuple[ResolvedToolSpec, ...],
        generated_at: datetime,
    ) -> AgentSpec:
        slots = {slot.name: slot for slot in instance.configuration.knowledge_slots}
        counts = Counter(binding.slot_name for binding in instance.knowledge_bindings)

        for binding in instance.knowledge_bindings:
            if binding.slot_name not in slots:
                raise UnknownKnowledgeSlotError(
                    details={"slot_name": binding.slot_name}
                )
        for slot in instance.configuration.knowledge_slots:
            count = counts[slot.name]
            if slot.required and count == 0:
                raise MissingKnowledgeBindingError(details={"slot_name": slot.name})
            if count > slot.max_items:
                raise KnowledgeCardinalityError(
                    details={
                        "slot_name": slot.name,
                        "count": count,
                        "max_items": slot.max_items,
                    }
                )

        knowledge = tuple(
            sorted(
                (
                    KnowledgeRef(
                        slot_name=binding.slot_name,
                        knowledge_id=binding.knowledge_id,
                        version=binding.knowledge_version,
                        checksum=binding.knowledge_checksum,
                        injection_mode=binding.injection_mode,
                    )
                    for binding in instance.knowledge_bindings
                ),
                key=lambda ref: (
                    ref.slot_name,
                    ref.knowledge_id,
                    semver_tuple(ref.version),
                ),
            )
        )
        unsigned = AgentSpec(
            schema_version="1.1" if instance.skill_tree is not None else "1.0",
            instance_id=instance.instance_id,
            revision=instance.revision,
            prototype=instance.prototype,
            agent_type=instance.configuration.agent_type,
            role=instance.configuration.role,
            system_prompt=instance.configuration.system_prompt,
            tools=tuple(sorted(tools, key=lambda tool: tool.name)),
            knowledge=knowledge,
            output_schema=instance.configuration.output_schema,
            active_skill_nodes=instance.active_skill_nodes,
            skill_tree=instance.skill_tree,
            runtime_target=instance.runtime_target,
            generated_at=generated_at,
            spec_checksum="0" * 64,
            metadata=instance.configuration.metadata,
        )
        return AgentSpec.model_validate(
            {
                **unsigned.model_dump(mode="python"),
                "spec_checksum": checksum_agent_spec(unsigned),
            }
        )
