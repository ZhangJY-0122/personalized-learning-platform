CREATE TABLE resource_snapshot (
 resource_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 knowledge_id CHAR(36) NOT NULL,title VARCHAR(150) NOT NULL,content_text TEXT NOT NULL,
 resource_type VARCHAR(20) NOT NULL,difficulty DECIMAL(4,3) NOT NULL,sort_order INT NOT NULL,
 PRIMARY KEY(resource_id,catalog_version),UNIQUE(resource_id,course_id,catalog_version),
 FOREIGN KEY(course_id,catalog_version,knowledge_id) REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id)
);
INSERT INTO resource_snapshot SELECT r.resource_id,r.course_id,c.catalog_version,r.knowledge_id,r.title,r.content_text,r.resource_type,r.difficulty,r.sort_order FROM resource r JOIN course c ON c.course_id=r.course_id;
CREATE TABLE prerequisite_snapshot (
 course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,source_knowledge_id CHAR(36) NOT NULL,target_knowledge_id CHAR(36) NOT NULL,
 PRIMARY KEY(course_id,catalog_version,source_knowledge_id,target_knowledge_id),
 FOREIGN KEY(course_id,catalog_version,source_knowledge_id) REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id),
 FOREIGN KEY(course_id,catalog_version,target_knowledge_id) REFERENCES knowledge_snapshot(course_id,catalog_version,knowledge_id)
);
INSERT INTO prerequisite_snapshot SELECT p.course_id,c.catalog_version,p.source_knowledge_id,p.target_knowledge_id FROM knowledge_prerequisite p JOIN course c ON c.course_id=p.course_id;
CREATE TABLE recommendation_batch (
 batch_seq BIGINT AUTO_INCREMENT PRIMARY KEY,batch_id CHAR(36) NOT NULL UNIQUE,
 user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,
 state_revision BIGINT NOT NULL,catalog_hash CHAR(64) NOT NULL,strategy_version VARCHAR(80) NOT NULL,
 created_at DATETIME(6) NOT NULL,expires_at DATETIME(6) NOT NULL,response_json JSON NOT NULL,input_json JSON NOT NULL,
 FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
 INDEX(user_id,course_id,batch_seq)
);
CREATE TABLE recommendation_item (
 recommendation_id CHAR(36) PRIMARY KEY,batch_id CHAR(36) NOT NULL,item_type VARCHAR(16) NOT NULL,
 item_id CHAR(36) NOT NULL,knowledge_id CHAR(36) NOT NULL,sort_order INT NOT NULL,
 FOREIGN KEY(batch_id) REFERENCES recommendation_batch(batch_id),UNIQUE(batch_id,item_type,item_id)
);
CREATE TABLE recommendation_feedback (
 feedback_seq BIGINT AUTO_INCREMENT PRIMARY KEY,feedback_id CHAR(36) NOT NULL UNIQUE,
 recommendation_id CHAR(36) NOT NULL,user_id CHAR(36) NOT NULL,feedback_type VARCHAR(20) NOT NULL,
 trusted BOOLEAN NOT NULL,source_event_id CHAR(36),created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 FOREIGN KEY(recommendation_id) REFERENCES recommendation_item(recommendation_id),FOREIGN KEY(user_id) REFERENCES local_user(user_id),
 UNIQUE(recommendation_id,source_event_id),INDEX(recommendation_id,feedback_seq)
);
CREATE TABLE recommendation_request (
 user_id CHAR(36) NOT NULL,operation VARCHAR(32) NOT NULL,idempotency_key CHAR(36) NOT NULL,
 payload_hash CHAR(64) NOT NULL,response_json JSON NOT NULL,
 PRIMARY KEY(user_id,operation,idempotency_key),FOREIGN KEY(user_id) REFERENCES local_user(user_id)
);
CREATE TABLE resource_activity (
 event_id CHAR(36) PRIMARY KEY,user_id CHAR(36) NOT NULL,course_id CHAR(36) NOT NULL,
 resource_id CHAR(36) NOT NULL,catalog_version VARCHAR(64) NOT NULL,resource_type VARCHAR(20) NOT NULL,
 activity_type VARCHAR(16) NOT NULL,recommendation_id CHAR(36),occurred_at DATETIME(6) NOT NULL,
 FOREIGN KEY(user_id,course_id) REFERENCES learner_course_state(user_id,course_id),
 FOREIGN KEY(resource_id,course_id,catalog_version) REFERENCES resource_snapshot(resource_id,course_id,catalog_version),
 FOREIGN KEY(recommendation_id) REFERENCES recommendation_item(recommendation_id),
 INDEX(user_id,course_id,occurred_at),INDEX(user_id,resource_id,activity_type)
);
ALTER TABLE practice_submission ADD COLUMN recommendation_id CHAR(36),ADD FOREIGN KEY(recommendation_id) REFERENCES recommendation_item(recommendation_id);
