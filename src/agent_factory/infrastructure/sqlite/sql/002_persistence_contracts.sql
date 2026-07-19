ALTER TABLE audit_events ADD COLUMN causation_id TEXT;

CREATE TEMP TABLE idempotency_migration_guard (
    record_count INTEGER NOT NULL CHECK (record_count = 0)
);

INSERT INTO idempotency_migration_guard (record_count)
SELECT COUNT(*) FROM idempotency_records;

DROP TABLE idempotency_migration_guard;

DROP TABLE idempotency_records;

CREATE TABLE idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX idx_idempotency_expires_at
    ON idempotency_records(expires_at);
