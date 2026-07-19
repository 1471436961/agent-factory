"""Pure knowledge-slot binding policy."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from agent_factory.domain.common import semver_tuple
from agent_factory.domain.errors import (
    KnowledgeCardinalityError,
    KnowledgeKindMismatchError,
    KnowledgeNotFoundError,
    KnowledgeVersionMismatchError,
    MissingKnowledgeBindingError,
    UnknownKnowledgeSlotError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    DomainKnowledge,
    KnowledgeBinding,
)


class KnowledgeSelectionLike(Protocol):
    """Structural input accepted from application commands or other adapters."""

    slot_name: str
    knowledge_id: str
    version: str


class KnowledgeBindingPolicy:
    """Validate a complete binding set and derive canonical bindings."""

    def validate_and_build(
        self,
        *,
        definition: AgentDefinition,
        selections: Iterable[KnowledgeSelectionLike],
        packages: Iterable[DomainKnowledge],
        bound_at: datetime,
        bound_by: str,
    ) -> tuple[KnowledgeBinding, ...]:
        slot_by_name = {slot.name: slot for slot in definition.knowledge_slots}
        package_by_ref = {
            (package.knowledge_id, package.version): package for package in packages
        }
        grouped: dict[str, list[KnowledgeSelectionLike]] = defaultdict(list)
        materialized = tuple(selections)

        for selection in materialized:
            slot = slot_by_name.get(selection.slot_name)
            if slot is None:
                raise UnknownKnowledgeSlotError(
                    details={"slot_name": selection.slot_name}
                )
            package = package_by_ref.get((selection.knowledge_id, selection.version))
            if package is None:
                raise KnowledgeNotFoundError(
                    details={
                        "knowledge_id": selection.knowledge_id,
                        "version": selection.version,
                    }
                )
            if package.kind not in slot.accepted_kinds:
                raise KnowledgeKindMismatchError(
                    details={
                        "slot_name": slot.name,
                        "knowledge_id": package.knowledge_id,
                        "actual_kind": package.kind.value,
                        "accepted_kinds": sorted(
                            kind.value for kind in slot.accepted_kinds
                        ),
                    }
                )
            if not self._version_is_accepted(
                package.version,
                minimum=slot.min_version,
                maximum_exclusive=slot.max_version_exclusive,
            ):
                raise KnowledgeVersionMismatchError(
                    details={
                        "slot_name": slot.name,
                        "version": package.version,
                        "min_version": slot.min_version,
                        "max_version_exclusive": slot.max_version_exclusive,
                    }
                )
            grouped[slot.name].append(selection)

        for slot in definition.knowledge_slots:
            count = len(grouped[slot.name])
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

        bindings = (
            KnowledgeBinding(
                slot_name=selection.slot_name,
                knowledge_id=selection.knowledge_id,
                knowledge_version=selection.version,
                knowledge_checksum=package_by_ref[
                    (selection.knowledge_id, selection.version)
                ].checksum,
                injection_mode=slot_by_name[selection.slot_name].injection_mode,
                bound_at=bound_at,
                bound_by=bound_by,
            )
            for selection in materialized
        )
        return tuple(
            sorted(
                bindings,
                key=lambda binding: (
                    binding.slot_name,
                    binding.knowledge_id,
                    semver_tuple(binding.knowledge_version),
                ),
            )
        )

    @staticmethod
    def _version_is_accepted(
        version: str,
        *,
        minimum: str,
        maximum_exclusive: str | None,
    ) -> bool:
        current = semver_tuple(version)
        return current >= semver_tuple(minimum) and (
            maximum_exclusive is None or current < semver_tuple(maximum_exclusive)
        )
