CREATE UNIQUE INDEX uq_task_outcomes_evaluation_report
    ON task_outcomes(evaluation_report_id);

CREATE INDEX idx_task_outcomes_revision_observation_window
    ON task_outcomes(
        instance_id,
        instance_revision,
        skill_node_id,
        recorded_at DESC,
        task_id DESC
    );
