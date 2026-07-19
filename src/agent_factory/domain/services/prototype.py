"""Pure prototype validation and state-transition policy."""

from datetime import datetime

from agent_factory.domain.enums import PrototypeStatus
from agent_factory.domain.errors import InvalidPrototypeStatusError
from agent_factory.domain.models import AgentDefinition, AgentPrototype
from agent_factory.domain.validation import validate_output_schema


class PrototypePolicy:
    """Validate definitions and derive immutable prototype state snapshots."""

    def validate_definition(self, definition: AgentDefinition) -> None:
        validate_output_schema(definition.output_schema)

    def publish(self, prototype: AgentPrototype, *, at: datetime) -> AgentPrototype:
        if prototype.status is not PrototypeStatus.DRAFT:
            raise InvalidPrototypeStatusError(
                details={
                    "prototype_id": prototype.prototype_id,
                    "version": prototype.version,
                    "current_status": prototype.status.value,
                    "target_status": PrototypeStatus.PUBLISHED.value,
                }
            )
        return AgentPrototype.model_validate(
            {
                **prototype.model_dump(mode="python"),
                "status": PrototypeStatus.PUBLISHED,
                "published_at": at,
            }
        )

    def deprecate(
        self,
        prototype: AgentPrototype,
        *,
        reason: str,
    ) -> AgentPrototype:
        if prototype.status is not PrototypeStatus.PUBLISHED:
            raise InvalidPrototypeStatusError(
                details={
                    "prototype_id": prototype.prototype_id,
                    "version": prototype.version,
                    "current_status": prototype.status.value,
                    "target_status": PrototypeStatus.DEPRECATED.value,
                }
            )
        return AgentPrototype.model_validate(
            {
                **prototype.model_dump(mode="python"),
                "status": PrototypeStatus.DEPRECATED,
                "deprecation_reason": reason,
            }
        )
