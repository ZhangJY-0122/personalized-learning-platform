-- G4 additive repair. V1..V6 are immutable.
-- Keep question order in the immutable snapshot and keep resource activity
-- attribution separate from event_consume_log attribution.

ALTER TABLE question_snapshot
  ADD COLUMN sort_order INT NULL;

UPDATE question_snapshot s
JOIN question q ON q.question_id=s.question_id AND q.course_id=s.course_id
SET s.sort_order=q.sort_order
WHERE s.sort_order IS NULL;

ALTER TABLE question_snapshot
  MODIFY sort_order INT NOT NULL;

ALTER TABLE learning_path_node
  ADD COLUMN source_resource_activity_id CHAR(36) NULL AFTER source_event_id,
  ADD CONSTRAINT fk_learning_path_node_resource_activity
    FOREIGN KEY(source_resource_activity_id) REFERENCES resource_activity(event_id),
  ADD CONSTRAINT uq_learning_path_node_resource_activity UNIQUE(source_resource_activity_id);

-- V6 declared rebuild_job.status as VARCHAR(20), which is shorter than the
-- frozen SUCCEEDED_WITH_WARNINGS state used by cross-catalog rebuilds.
ALTER TABLE rebuild_job MODIFY status VARCHAR(32) NOT NULL DEFAULT 'PENDING';
