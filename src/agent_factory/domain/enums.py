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
