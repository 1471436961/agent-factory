"""Stable enum values persisted by M1 domain snapshots."""

from enum import StrEnum


class Capability(StrEnum):
    CODE = "can-code"
    WRITE = "can-write"


class PrototypeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class KnowledgeKind(StrEnum):
    DOCUMENT = "document"
    POLICY = "policy"
    DATASET = "dataset"
    GLOSSARY = "glossary"


class InjectionMode(StrEnum):
    INLINE = "inline"
    RETRIEVAL = "retrieval"


class InstanceStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"
    TERMINATED = "terminated"


class ToolPermission(StrEnum):
    READ_ONLY = "read-only"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    WRITE_EXTERNAL = "write-external"


class RuleKind(StrEnum):
    JSON_SCHEMA = "json-schema"
    REQUIRED_TERMS = "required-terms"
    FORBIDDEN_TERMS = "forbidden-terms"
    REGEX = "regex"
    MAX_LENGTH = "max-length"
    TOOL_CALLED = "tool-called"


class EvaluationDecision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW_REQUIRED = "review-required"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditEntityType(StrEnum):
    PROTOTYPE = "prototype"
    INSTANCE = "instance"
    KNOWLEDGE = "knowledge"
    SKILL = "skill"
    TOOL_CALL = "tool-call"
    EVALUATION = "evaluation"


class AuditEventType(StrEnum):
    PROTOTYPE_REGISTERED = "prototype.registered"
    PROTOTYPE_PUBLISHED = "prototype.published"
    PROTOTYPE_DEPRECATED = "prototype.deprecated"
    KNOWLEDGE_REGISTERED = "knowledge.registered"
    INSTANCE_CLONED = "instance.cloned"
    KNOWLEDGE_BOUND = "knowledge.bound"
    SPEC_EXPORTED = "spec.exported"
    INSTANCE_TRANSITIONED = "instance.transitioned"
    EVALUATION_COMPLETED = "evaluation.completed"
    SKILL_PROMOTED = "skill.promoted"
    SKILL_DEGRADED = "skill.degraded"
    TOOL_CALLED = "tool.called"
