CREATE TABLE IF NOT EXISTS prep_catalog (
 course_id CHAR(36) PRIMARY KEY,
 title VARCHAR(100) NOT NULL,
 catalog_version VARCHAR(40) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT IGNORE INTO prep_catalog(course_id,title,catalog_version)
VALUES ('10000000-0000-4000-8000-000000000001','Java程序设计预开发样例','java-prep-v1');
