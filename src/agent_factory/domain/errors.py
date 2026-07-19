"""Transport-neutral domain and application error contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from agent_factory.domain.common import FrozenJsonObject


class FactoryError(Exception):
    """Base error with a stable code and immutable structured details."""

    code: ClassVar[str] = "FACTORY_ERROR"
    default_message: ClassVar[str] = "Agent Factory operation failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, object] | FrozenJsonObject | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = (
            details
            if isinstance(details, FrozenJsonObject)
            else FrozenJsonObject(details)
        )
        super().__init__(self.message)


class InvalidOutputSchemaError(FactoryError):
    code = "INVALID_OUTPUT_SCHEMA"
    default_message = "Output schema is not valid JSON Schema Draft 2020-12"


class PrototypeNotFoundError(FactoryError):
    code = "PROTOTYPE_NOT_FOUND"
    default_message = "Prototype was not found"


class PrototypeAlreadyExistsError(FactoryError):
    code = "PROTOTYPE_ALREADY_EXISTS"
    default_message = "Prototype version already exists"


class PrototypeNotPublishedError(FactoryError):
    code = "PROTOTYPE_NOT_PUBLISHED"
    default_message = "Prototype is not published"


class InvalidPrototypeStatusError(FactoryError):
    code = "INVALID_PROTOTYPE_STATUS"
    default_message = "Prototype status transition is not allowed"


class KnowledgeNotFoundError(FactoryError):
    code = "KNOWLEDGE_NOT_FOUND"
    default_message = "Knowledge package was not found"


class KnowledgeAlreadyExistsError(FactoryError):
    code = "KNOWLEDGE_ALREADY_EXISTS"
    default_message = "Knowledge package version already exists"


class KnowledgeAlreadyBoundError(FactoryError):
    code = "KNOWLEDGE_ALREADY_BOUND"
    default_message = "Knowledge slot already has a binding"


class UnknownKnowledgeSlotError(FactoryError):
    code = "UNKNOWN_KNOWLEDGE_SLOT"
    default_message = "Knowledge slot is not declared by the instance"


class MissingKnowledgeBindingError(FactoryError):
    code = "MISSING_KNOWLEDGE_BINDING"
    default_message = "Required knowledge binding is missing"


class KnowledgeKindMismatchError(FactoryError):
    code = "KNOWLEDGE_KIND_MISMATCH"
    default_message = "Knowledge kind is not accepted by the slot"


class KnowledgeVersionMismatchError(FactoryError):
    code = "KNOWLEDGE_VERSION_MISMATCH"
    default_message = "Knowledge version is outside the slot range"


class KnowledgeCardinalityError(FactoryError):
    code = "KNOWLEDGE_CARDINALITY_INVALID"
    default_message = "Knowledge binding cardinality is invalid"


class KnowledgeChecksumMismatchError(FactoryError):
    code = "KNOWLEDGE_CHECKSUM_MISMATCH"
    default_message = "Knowledge checksum does not match its content"


class KnowledgePayloadTooLargeError(FactoryError):
    code = "KNOWLEDGE_PAYLOAD_TOO_LARGE"
    default_message = "Knowledge payload exceeds the configured size limit"


class InstanceNotFoundError(FactoryError):
    code = "INSTANCE_NOT_FOUND"
    default_message = "Agent instance was not found"


class InstanceBusyError(FactoryError):
    code = "INSTANCE_BUSY"
    default_message = "Agent instance is currently running"


class InstanceNotReadyError(FactoryError):
    code = "INSTANCE_NOT_READY"
    default_message = "Agent instance is not ready for this operation"


class InvalidStateTransitionError(FactoryError):
    code = "INVALID_STATE_TRANSITION"
    default_message = "Agent instance state transition is not allowed"


class RevisionConflictError(FactoryError):
    code = "REVISION_CONFLICT"
    default_message = "Instance revision no longer matches"


class IdempotencyKeyReusedError(FactoryError):
    code = "IDEMPOTENCY_KEY_REUSED"
    default_message = "Idempotency key was reused with a different request"


class ToolNotGrantedError(FactoryError):
    code = "TOOL_NOT_GRANTED"
    default_message = "Tool is not granted to this instance"


class UnknownToolError(FactoryError):
    code = "UNKNOWN_TOOL"
    default_message = "Tool is not registered in the catalog"


class ToolPermissionDeniedError(FactoryError):
    code = "TOOL_PERMISSION_DENIED"
    default_message = "Tool requests a permission that is not allowed"


class RepositoryUnavailableError(FactoryError):
    code = "REPOSITORY_UNAVAILABLE"
    default_message = "Repository is temporarily unavailable"
