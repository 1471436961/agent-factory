CREATE TABLE prototypes (
    prototype_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (prototype_id, version)
);

CREATE TABLE knowledge_packages (
    knowledge_id TEXT NOT NULL,
    version TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, version)
);

CREATE TABLE instance_snapshots (
    instance_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL,
    prototype_id TEXT NOT NULL,
    prototype_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, revision),
    FOREIGN KEY (prototype_id, prototype_version)
        REFERENCES prototypes(prototype_id, version)
);

CREATE TABLE instance_heads (
    instance_id TEXT PRIMARY KEY,
    current_revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (instance_id, current_revision)
        REFERENCES instance_snapshots(instance_id, revision)
);

CREATE TABLE agent_specs (
    instance_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, revision),
    FOREIGN KEY (instance_id, revision)
        REFERENCES instance_snapshots(instance_id, revision)
);

CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_revision INTEGER,
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_entity
    ON audit_events(entity_type, entity_id, created_at);

CREATE TABLE idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
