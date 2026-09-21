-- Freeze the G1 catalog before recording any real practice evidence.
CREATE TABLE catalog_snapshot (
 course_id CHAR(36) NOT NULL, catalog_version VARCHAR(64) NOT NULL,
 title VARCHAR(120) NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY(course_id,catalog_version), FOREIGN KEY(course_id) REFERENCES course(course_id)
);
INSERT INTO catalog_snapshot(course_id,catalog_version,title) SELECT course_id,catalog_version,title FROM course;
CREATE TABLE knowledge_snapshot (
 course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,knowledge_id CHAR(36) NOT NULL,
 name VARCHAR(100) NOT NULL,sort_order INT NOT NULL,
 PRIMARY KEY(course_id,catalog_version,knowledge_id),
 FOREIGN KEY(course_id,catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version)
);
INSERT INTO knowledge_snapshot SELECT k.course_id,c.catalog_version,k.knowledge_id,k.name,k.sort_order
 FROM knowledge_point k JOIN course c ON c.course_id=k.course_id;
CREATE TABLE question_snapshot (
 question_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 knowledge_id CHAR(36) NOT NULL,question_type VARCHAR(20) NOT NULL,stem TEXT NOT NULL,
 options_json JSON NOT NULL,answer_json JSON NOT NULL,difficulty DECIMAL(4,3) NOT NULL,
 PRIMARY KEY(question_id,catalog_version),UNIQUE(question_id,course_id,catalog_version),
 FOREIGN KEY(course_id,catalog_version,knowledge_id) REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id)
);
INSERT INTO question_snapshot SELECT q.question_id,q.course_id,c.catalog_version,q.knowledge_id,q.question_type,q.stem,q.options_json,q.answer_json,q.difficulty
 FROM question q JOIN course c ON c.course_id=q.course_id;
CREATE TABLE learner_course_state (
 user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 state_revision BIGINT NOT NULL DEFAULT 0,replay_generation INT NOT NULL DEFAULT 0,
 status VARCHAR(32) NOT NULL DEFAULT 'READY',last_occurred_at DATETIME(6),last_event_seq BIGINT,last_event_id CHAR(36),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY(user_id,course_id),FOREIGN KEY(user_id) REFERENCES local_user(user_id),
 FOREIGN KEY(course_id,catalog_version) REFERENCES catalog_snapshot(course_id,catalog_version)
);
CREATE TABLE practice_submission (
 submission_id CHAR(36) PRIMARY KEY,user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,
 question_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 answer_json JSON NOT NULL,correct BOOLEAN NOT NULL,idempotency_key CHAR(36) NOT NULL,payload_hash CHAR(64) NOT NULL,
 created_at DATETIME(6) NOT NULL,
 UNIQUE(user_id,idempotency_key),FOREIGN KEY(user_id) REFERENCES local_user(user_id),
 FOREIGN KEY(question_id,course_id,catalog_version) REFERENCES question_snapshot(question_id,course_id,catalog_version)
);
CREATE TABLE event_consume_log (
 event_seq BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,event_id CHAR(36) NOT NULL UNIQUE,
 submission_id CHAR(36) NOT NULL UNIQUE,user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 event_type VARCHAR(32) NOT NULL,source_service VARCHAR(64) NOT NULL,payload_json JSON NOT NULL,payload_hash CHAR(64) NOT NULL,
 occurred_at DATETIME(6) NOT NULL,status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
 retry_count INT NOT NULL DEFAULT 0,next_retry_at DATETIME(6),error_code VARCHAR(64),consumed_at DATETIME(6),
 FOREIGN KEY(submission_id) REFERENCES practice_submission(submission_id),
 FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
 INDEX(user_id,course_id,occurred_at,event_seq,event_id),INDEX(status,next_retry_at)
);
CREATE TABLE learning_interaction (
 interaction_id CHAR(36) PRIMARY KEY,source_event_id CHAR(36) NOT NULL UNIQUE,
 user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,question_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 correct BOOLEAN NOT NULL,occurred_at DATETIME(6) NOT NULL,event_seq BIGINT NOT NULL,
 source_service VARCHAR(64) NOT NULL DEFAULT 'local-practice',event_type VARCHAR(32) NOT NULL DEFAULT 'QUESTION_ANSWERED',
 FOREIGN KEY(source_event_id) REFERENCES event_consume_log(event_id),
 FOREIGN KEY(question_id,course_id,catalog_version) REFERENCES question_snapshot(question_id,course_id,catalog_version),
 INDEX(user_id,course_id,occurred_at)
);
CREATE TABLE interaction_knowledge (
 interaction_id CHAR(36) NOT NULL,knowledge_id CHAR(36) NOT NULL,normalized_weight DOUBLE NOT NULL CHECK(normalized_weight>0 AND normalized_weight<=1),
 PRIMARY KEY(interaction_id,knowledge_id),FOREIGN KEY(interaction_id) REFERENCES learning_interaction(interaction_id)
);
CREATE TABLE mastery_state (
 user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,knowledge_id CHAR(36) NOT NULL,
 model_version VARCHAR(160) NOT NULL,mastery DOUBLE NOT NULL CHECK(mastery BETWEEN 0 AND 1),
 evidence_count INT NOT NULL,evidence_weight DOUBLE NOT NULL,
 PRIMARY KEY(user_id,course_id,knowledge_id,model_version),
 FOREIGN KEY(course_id,catalog_version,knowledge_id) REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id)
);
CREATE TABLE mastery_history (
 event_id CHAR(36) NOT NULL,knowledge_id CHAR(36) NOT NULL,model_version VARCHAR(160) NOT NULL,
 replay_generation INT NOT NULL,before_mastery DOUBLE NOT NULL,after_mastery DOUBLE NOT NULL,weight DOUBLE NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY(event_id,knowledge_id,model_version,replay_generation),FOREIGN KEY(event_id) REFERENCES event_consume_log(event_id)
);
CREATE TABLE student_profile (
 user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,total_answers INT NOT NULL,correct_answers INT NOT NULL,
 activity_days INT NOT NULL,last_activity_at DATETIME(6) NOT NULL,feature_version VARCHAR(64) NOT NULL DEFAULT 'practice-profile-v1',
 PRIMARY KEY(user_id,course_id),FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id)
);
