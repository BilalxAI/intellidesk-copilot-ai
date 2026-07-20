-- MySQL schema for IT Support Assistant conversation storage.
-- DevOps/DBA should create these tables; the app will not run migrations.

CREATE TABLE sessions (
  session_id VARCHAR(64) PRIMARY KEY,
  conversation_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  created_at VARCHAR(64) NOT NULL,
  updated_at VARCHAR(64) NOT NULL
);

CREATE TABLE messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  user_input TEXT NOT NULL,
  assistant_response TEXT NOT NULL,
  created_at VARCHAR(64) NOT NULL
);
