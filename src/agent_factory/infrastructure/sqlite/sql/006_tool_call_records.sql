CREATE TABLE tool_call_records (
    call_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    instance_revision INTEGER NOT NULL CHECK (instance_revision >= 1),
    agent_spec_checksum TEXT NOT NULL CHECK (length(agent_spec_checksum) = 64),
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('succeeded', 'rejected', 'failed', 'timed-out')
    ),
    arguments_hash TEXT NOT NULL CHECK (length(arguments_hash) = 64),
    result_hash TEXT CHECK (result_hash IS NULL OR length(result_hash) = 64),
    error_code TEXT,
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_checksum TEXT NOT NULL CHECK (length(record_checksum) = 64),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    CHECK (
        (status = 'succeeded' AND result_hash IS NOT NULL AND error_code IS NULL)
        OR
        (status <> 'succeeded' AND result_hash IS NULL AND error_code IS NOT NULL)
    ),
    FOREIGN KEY (instance_id, instance_revision, agent_spec_checksum)
        REFERENCES agent_specs(instance_id, revision, checksum)
);

CREATE INDEX idx_tool_call_records_instance
    ON tool_call_records(instance_id, instance_revision, started_at, call_id);

CREATE INDEX idx_tool_call_records_task
    ON tool_call_records(task_id, started_at, call_id);
