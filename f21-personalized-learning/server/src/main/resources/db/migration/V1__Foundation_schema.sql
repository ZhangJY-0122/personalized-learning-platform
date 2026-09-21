CREATE TABLE local_user (
 user_id CHAR(36) PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
 display_name VARCHAR(100) NOT NULL, password_hash VARCHAR(100) NOT NULL,
 role VARCHAR(16) NOT NULL CHECK(role IN ('STUDENT','TEACHER','ADMIN')),
 status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED')),
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
);
CREATE TABLE course (
 course_id CHAR(36) PRIMARY KEY, title VARCHAR(120) NOT NULL, description TEXT NOT NULL,
 catalog_version VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
);
CREATE TABLE teacher_course_scope (
 user_id CHAR(36) NOT NULL, course_id CHAR(36) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 PRIMARY KEY(user_id,course_id), FOREIGN KEY(user_id) REFERENCES local_user(user_id),
 FOREIGN KEY(course_id) REFERENCES course(course_id)
);
CREATE TABLE course_enrollment (
 user_id CHAR(36) NOT NULL, course_id CHAR(36) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 enrolled_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 PRIMARY KEY(user_id,course_id), FOREIGN KEY(user_id) REFERENCES local_user(user_id),
 FOREIGN KEY(course_id) REFERENCES course(course_id)
);
CREATE TABLE chapter (
 chapter_id CHAR(36) PRIMARY KEY, course_id CHAR(36) NOT NULL,title VARCHAR(120) NOT NULL,sort_order INT NOT NULL,
 UNIQUE(chapter_id,course_id),FOREIGN KEY(course_id) REFERENCES course(course_id)
);
CREATE TABLE knowledge_point (
 knowledge_id CHAR(36) PRIMARY KEY,course_id CHAR(36) NOT NULL,chapter_id CHAR(36) NOT NULL,
 name VARCHAR(100) NOT NULL,description TEXT NOT NULL,difficulty DECIMAL(4,3) NOT NULL CHECK(difficulty BETWEEN 0 AND 1),
 sort_order INT NOT NULL,UNIQUE(knowledge_id,course_id),
 FOREIGN KEY(chapter_id,course_id) REFERENCES chapter(chapter_id,course_id)
);
CREATE TABLE knowledge_prerequisite (
 course_id CHAR(36) NOT NULL,source_knowledge_id CHAR(36) NOT NULL,target_knowledge_id CHAR(36) NOT NULL,
 PRIMARY KEY(course_id,source_knowledge_id,target_knowledge_id),
 CHECK(source_knowledge_id<>target_knowledge_id),
 FOREIGN KEY(source_knowledge_id,course_id) REFERENCES knowledge_point(knowledge_id,course_id),
 FOREIGN KEY(target_knowledge_id,course_id) REFERENCES knowledge_point(knowledge_id,course_id)
);
CREATE TABLE resource (
 resource_id CHAR(36) PRIMARY KEY,course_id CHAR(36) NOT NULL,knowledge_id CHAR(36) NOT NULL,
 title VARCHAR(150) NOT NULL,content_text TEXT NOT NULL,resource_type VARCHAR(20) NOT NULL DEFAULT 'ARTICLE',
 difficulty DECIMAL(4,3) NOT NULL CHECK(difficulty BETWEEN 0 AND 1),sort_order INT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 FOREIGN KEY(knowledge_id,course_id) REFERENCES knowledge_point(knowledge_id,course_id),INDEX(course_id,status)
);
CREATE TABLE question (
 question_id CHAR(36) PRIMARY KEY,course_id CHAR(36) NOT NULL,knowledge_id CHAR(36) NOT NULL,
 question_type VARCHAR(20) NOT NULL CHECK(question_type IN ('SINGLE','MULTIPLE','TRUE_FALSE')),
 stem TEXT NOT NULL,options_json JSON NOT NULL,answer_json JSON NOT NULL,
 difficulty DECIMAL(4,3) NOT NULL CHECK(difficulty BETWEEN 0 AND 1),sort_order INT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 FOREIGN KEY(knowledge_id,course_id) REFERENCES knowledge_point(knowledge_id,course_id),INDEX(course_id,status)
);
CREATE TABLE revoked_token (
 jti VARCHAR(64) PRIMARY KEY,expires_at DATETIME(3) NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),INDEX(expires_at)
);
