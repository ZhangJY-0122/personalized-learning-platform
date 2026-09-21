-- G4 only: immutable catalog history, durable paths/jobs and trusted import recovery.
-- Never edit V1..V5.  This migration is intentionally additive except for making
-- event_consume_log.submission_id nullable so an administrator-imported event can
-- be persisted without inventing a local practice submission.

ALTER TABLE catalog_snapshot
  ADD COLUMN description TEXT NULL,
  ADD COLUMN content_hash CHAR(64) NULL,
  ADD COLUMN snapshot_status VARCHAR(16) NOT NULL DEFAULT 'PUBLISHED',
  ADD COLUMN published_at DATETIME(6) NULL,
  ADD CONSTRAINT chk_catalog_snapshot_status
    CHECK (snapshot_status IN ('DRAFT','PUBLISHED','ARCHIVED'));

UPDATE catalog_snapshot c
JOIN course p ON p.course_id=c.course_id AND p.catalog_version=c.catalog_version
SET c.description=p.description,
    c.snapshot_status='PUBLISHED',
    c.published_at=COALESCE(c.created_at,UTC_TIMESTAMP(6))
WHERE c.description IS NULL;

CREATE TABLE chapter_snapshot (
  course_id CHAR(36) NOT NULL,
  catalog_version VARCHAR(64) NOT NULL,
  chapter_id CHAR(36) NOT NULL,
  title VARCHAR(120) NOT NULL,
  sort_order INT NOT NULL,
  PRIMARY KEY(course_id,catalog_version,chapter_id),
  FOREIGN KEY(course_id,catalog_version)
    REFERENCES catalog_snapshot(course_id,catalog_version)
);

INSERT INTO chapter_snapshot(course_id,catalog_version,chapter_id,title,sort_order)
SELECT c.course_id,p.catalog_version,c.chapter_id,c.title,c.sort_order
FROM chapter c
JOIN course p ON p.course_id=c.course_id;

ALTER TABLE knowledge_snapshot
  ADD COLUMN chapter_id CHAR(36) NULL,
  ADD COLUMN description TEXT NULL,
  ADD COLUMN difficulty DECIMAL(4,3) NULL;

UPDATE knowledge_snapshot s
JOIN knowledge_point k ON k.knowledge_id=s.knowledge_id AND k.course_id=s.course_id
SET s.chapter_id=k.chapter_id,
    s.description=k.description,
    s.difficulty=k.difficulty;

ALTER TABLE knowledge_snapshot
  MODIFY chapter_id CHAR(36) NOT NULL,
  MODIFY description TEXT NOT NULL,
  MODIFY difficulty DECIMAL(4,3) NOT NULL,
  ADD CONSTRAINT fk_knowledge_snapshot_chapter
    FOREIGN KEY(course_id,catalog_version,chapter_id)
    REFERENCES chapter_snapshot(course_id,catalog_version,chapter_id),
  ADD CONSTRAINT chk_knowledge_snapshot_difficulty
    CHECK (difficulty BETWEEN 0 AND 1);

CREATE TABLE import_job (
  job_id CHAR(36) PRIMARY KEY,
  job_type VARCHAR(32) NOT NULL,
  file_name VARCHAR(255) NULL,
  content_hash CHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
  request_json JSON NOT NULL,
  total_count INT NOT NULL DEFAULT 0,
  success_count INT NOT NULL DEFAULT 0,
  failure_count INT NOT NULL DEFAULT 0,
  error_code VARCHAR(64) NULL,
  created_by CHAR(36) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  completed_at DATETIME(6) NULL,
  UNIQUE(job_type,content_hash),
  FOREIGN KEY(created_by) REFERENCES local_user(user_id),
  CONSTRAINT chk_import_job_type
    CHECK (job_type IN ('CATALOG_JSON','EVENT_JSON','EVENT_CSV')),
  CONSTRAINT chk_import_job_status
    CHECK (status IN ('PENDING','VALIDATING','SUCCEEDED','SUCCEEDED_WITH_WARNINGS',
                      'VALIDATION_FAILED','FAILED','NEEDS_ATTENTION')),
  INDEX(status,created_at)
);

CREATE TABLE import_error (
  job_id CHAR(36) NOT NULL,
  row_no INT NOT NULL,
  field_path VARCHAR(255) NOT NULL,
  error_code VARCHAR(64) NOT NULL,
  message VARCHAR(1000) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY(job_id,row_no,field_path,error_code),
  FOREIGN KEY(job_id) REFERENCES import_job(job_id),
  INDEX(job_id,row_no)
);

CREATE TABLE learning_path (
  path_seq BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  path_id CHAR(36) NOT NULL UNIQUE,
  user_id CHAR(36) NOT NULL,
  course_id CHAR(36) NOT NULL,
  catalog_version VARCHAR(64) NOT NULL,
  path_version INT NOT NULL,
  origin_state_revision BIGINT NOT NULL,
  state_revision BIGINT NOT NULL,
  planner_version VARCHAR(80) NOT NULL,
  catalog_hash CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  reason_code VARCHAR(64) NULL,
  reason_json JSON NOT NULL,
  previous_path_id CHAR(36) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE(user_id,course_id,path_version),
  FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
  FOREIGN KEY(course_id,catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version),
  FOREIGN KEY(previous_path_id) REFERENCES learning_path(path_id),
  CONSTRAINT chk_learning_path_status
    CHECK (status IN ('ACTIVE','COMPLETED','INVALIDATED','BLOCKED')),
  INDEX(user_id,course_id,path_seq),
  INDEX(user_id,course_id,status)
);

CREATE TABLE learning_path_node (
  node_id CHAR(36) PRIMARY KEY,
  path_id CHAR(36) NOT NULL,
  sequence_no INT NOT NULL,
  knowledge_id CHAR(36) NOT NULL,
  item_type VARCHAR(16) NOT NULL,
  item_id CHAR(36) NOT NULL,
  phase VARCHAR(24) NOT NULL,
  round_no TINYINT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  source_event_id CHAR(36) NULL,
  carried_from_node_id CHAR(36) NULL,
  reason_json JSON NOT NULL,
  started_at DATETIME(6) NULL,
  completed_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE(path_id,sequence_no),
  UNIQUE(path_id,item_type,item_id),
  UNIQUE(source_event_id),
  FOREIGN KEY(path_id) REFERENCES learning_path(path_id),
  FOREIGN KEY(source_event_id) REFERENCES event_consume_log(event_id),
  FOREIGN KEY(carried_from_node_id) REFERENCES learning_path_node(node_id),
  CONSTRAINT chk_learning_path_node_type
    CHECK (item_type IN ('RESOURCE','QUESTION')),
  CONSTRAINT chk_learning_path_node_phase
    CHECK (phase IN ('RESOURCE','BASIC_PRACTICE','ADVANCED_PRACTICE','REMEDIATION','RETEST')),
  CONSTRAINT chk_learning_path_node_round
    CHECK (round_no IN (1,2)),
  CONSTRAINT chk_learning_path_node_status
    CHECK (status IN ('PENDING','IN_PROGRESS','COMPLETED','SKIPPED')),
  INDEX(path_id,status,sequence_no),
  INDEX(knowledge_id,item_type,item_id)
);

ALTER TABLE practice_submission
  ADD COLUMN path_node_id CHAR(36) NULL,
  ADD CONSTRAINT fk_practice_submission_path_node
    FOREIGN KEY(path_node_id) REFERENCES learning_path_node(node_id),
  ADD CONSTRAINT uq_practice_submission_path_node UNIQUE(path_node_id);

ALTER TABLE resource_activity
  ADD COLUMN path_node_id CHAR(36) NULL,
  ADD CONSTRAINT fk_resource_activity_path_node
    FOREIGN KEY(path_node_id) REFERENCES learning_path_node(node_id),
  ADD CONSTRAINT uq_resource_activity_path_activity UNIQUE(path_node_id,activity_type);

CREATE TABLE learning_path_request (
  user_id CHAR(36) NOT NULL,
  operation VARCHAR(32) NOT NULL,
  idempotency_key CHAR(36) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  response_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY(user_id,operation,idempotency_key),
  FOREIGN KEY(user_id) REFERENCES local_user(user_id),
  CONSTRAINT chk_learning_path_request_operation CHECK (operation IN ('GENERATE'))
);

CREATE TABLE path_replan_job (
  job_id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  course_id CHAR(36) NOT NULL,
  catalog_version VARCHAR(64) NOT NULL,
  trigger_state_revision BIGINT NOT NULL,
  trigger_type VARCHAR(32) NOT NULL,
  dedupe_key CHAR(64) NOT NULL UNIQUE,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  attempt_count INT NOT NULL DEFAULT 0,
  next_retry_at DATETIME(6) NULL,
  error_code VARCHAR(64) NULL,
  result_path_id CHAR(36) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
  FOREIGN KEY(course_id,catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version),
  FOREIGN KEY(result_path_id) REFERENCES learning_path(path_id),
  CONSTRAINT chk_path_replan_trigger CHECK (trigger_type IN ('NON_PATH_ACTIVITY','RETEST_FAILED','REBUILD_COMPLETED','CATALOG_MIGRATED','MANUAL_RETRY')),
  CONSTRAINT chk_path_replan_status CHECK (status IN ('PENDING','SUCCEEDED','FAILED','NEEDS_ATTENTION')),
  INDEX(status,next_retry_at,created_at),
  INDEX(user_id,course_id,trigger_state_revision)
);

CREATE TABLE catalog_knowledge_map (
  course_id CHAR(36) NOT NULL,
  from_catalog_version VARCHAR(64) NOT NULL,
  from_knowledge_id CHAR(36) NOT NULL,
  to_catalog_version VARCHAR(64) NOT NULL,
  to_knowledge_id CHAR(36) NOT NULL,
  mapping_type VARCHAR(16) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY(course_id,from_catalog_version,from_knowledge_id,to_catalog_version),
  FOREIGN KEY(course_id,from_catalog_version,from_knowledge_id)
    REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id),
  FOREIGN KEY(course_id,to_catalog_version,to_knowledge_id)
    REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id),
  CONSTRAINT chk_catalog_knowledge_map_type CHECK (mapping_type IN ('SAME_UUID','EXPLICIT'))
);

CREATE TABLE rebuild_job (
  job_id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  course_id CHAR(36) NOT NULL,
  from_catalog_version VARCHAR(64) NOT NULL,
  to_catalog_version VARCHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  attempt_count INT NOT NULL DEFAULT 0,
  processed_events INT NOT NULL DEFAULT 0,
  mapped_events INT NOT NULL DEFAULT 0,
  unmapped_events INT NOT NULL DEFAULT 0,
  error_code VARCHAR(64) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  completed_at DATETIME(6) NULL,
  FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
  FOREIGN KEY(course_id,from_catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version),
  FOREIGN KEY(course_id,to_catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version),
  CONSTRAINT chk_rebuild_job_status CHECK (status IN ('PENDING','REBUILDING','SUCCEEDED','SUCCEEDED_WITH_WARNINGS','FAILED','NEEDS_ATTENTION')),
  INDEX(status,created_at),
  INDEX(user_id,course_id,created_at)
);

ALTER TABLE event_consume_log
  MODIFY submission_id CHAR(36) NULL,
  ADD COLUMN source_event_seq BIGINT NULL,
  ADD COLUMN import_job_id CHAR(36) NULL,
  ADD COLUMN trace_id CHAR(36) NULL,
  ADD CONSTRAINT fk_event_consume_import_job
    FOREIGN KEY(import_job_id) REFERENCES import_job(job_id),
  ADD INDEX ix_event_order_import
    (user_id,course_id,occurred_at,source_event_seq,event_seq,event_id);
