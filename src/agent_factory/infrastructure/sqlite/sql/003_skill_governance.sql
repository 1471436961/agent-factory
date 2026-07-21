CREATE UNIQUE INDEX uq_agent_specs_identity_checksum
    ON agent_specs(instance_id, revision, checksum);

CREATE TABLE evaluation_suites (
    suite_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (suite_id, version),
    UNIQUE (suite_id, version, checksum)
);

CREATE TABLE skill_trees (
    tree_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tree_id, version),
    UNIQUE (tree_id, version, checksum)
);

CREATE TABLE skill_node_suites (
    tree_id TEXT NOT NULL,
    tree_version TEXT NOT NULL,
    node_id TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_checksum TEXT NOT NULL,
    PRIMARY KEY (tree_id, tree_version, node_id),
    FOREIGN KEY (tree_id, tree_version)
        REFERENCES skill_trees(tree_id, version),
    FOREIGN KEY (suite_id, suite_version, suite_checksum)
        REFERENCES evaluation_suites(suite_id, version, checksum)
);

CREATE TABLE prototype_skill_trees (
    prototype_id TEXT NOT NULL,
    prototype_version TEXT NOT NULL,
    tree_id TEXT NOT NULL,
    tree_version TEXT NOT NULL,
    tree_checksum TEXT NOT NULL,
    PRIMARY KEY (prototype_id, prototype_version),
    FOREIGN KEY (prototype_id, prototype_version)
        REFERENCES prototypes(prototype_id, version),
    FOREIGN KEY (tree_id, tree_version, tree_checksum)
        REFERENCES skill_trees(tree_id, version, checksum)
);

CREATE TABLE instance_skill_trees (
    instance_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    tree_id TEXT NOT NULL,
    tree_version TEXT NOT NULL,
    tree_checksum TEXT NOT NULL,
    PRIMARY KEY (instance_id, revision),
    FOREIGN KEY (instance_id, revision)
        REFERENCES instance_snapshots(instance_id, revision),
    FOREIGN KEY (tree_id, tree_version, tree_checksum)
        REFERENCES skill_trees(tree_id, version, checksum)
);

CREATE TABLE evaluation_reports (
    report_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    instance_revision INTEGER NOT NULL CHECK (instance_revision >= 1),
    agent_spec_checksum TEXT NOT NULL,
    tree_id TEXT NOT NULL,
    tree_version TEXT NOT NULL,
    tree_checksum TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_checksum TEXT NOT NULL,
    decision TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE (report_id, instance_id, instance_revision),
    FOREIGN KEY (instance_id, instance_revision, agent_spec_checksum)
        REFERENCES agent_specs(instance_id, revision, checksum),
    FOREIGN KEY (tree_id, tree_version, tree_checksum)
        REFERENCES skill_trees(tree_id, version, checksum),
    FOREIGN KEY (suite_id, suite_version, suite_checksum)
        REFERENCES evaluation_suites(suite_id, version, checksum)
);

CREATE INDEX idx_evaluation_reports_instance
    ON evaluation_reports(instance_id, instance_revision, completed_at);

CREATE TABLE evaluation_reviews (
    review_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    decision TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES evaluation_reports(report_id)
);

CREATE TABLE task_outcomes (
    task_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    instance_revision INTEGER NOT NULL CHECK (instance_revision >= 1),
    skill_node_id TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    evaluation_report_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, instance_id, skill_node_id),
    FOREIGN KEY (evaluation_report_id, instance_id, instance_revision)
        REFERENCES evaluation_reports(report_id, instance_id, instance_revision)
);

CREATE INDEX idx_task_outcomes_observation_window
    ON task_outcomes(instance_id, skill_node_id, recorded_at DESC, task_id DESC);
