CREATE DATABASE IF NOT EXISTS harness CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE harness;

CREATE TABLE conversations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	title VARCHAR(255) NOT NULL, 
	preview VARCHAR(512) NOT NULL, 
	icon_key VARCHAR(32) NOT NULL, 
	icon_color VARCHAR(16) NOT NULL, 
	workspace TEXT NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	permission_mode VARCHAR(16) NOT NULL, 
	summary TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE mcp_servers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(64) NOT NULL, 
	command TEXT NOT NULL, 
	args JSON, 
	env JSON, 
	enabled BOOL NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE memories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	title VARCHAR(255) NOT NULL, 
	content TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE settings (
	name VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	PRIMARY KEY (name)
);

CREATE TABLE skills (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(512) NOT NULL, 
	body TEXT NOT NULL, 
	enabled BOOL NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE usage_events (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	conversation_id INTEGER, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	prompt_tokens INTEGER NOT NULL, 
	completion_tokens INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE messages (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	conversation_id INTEGER NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	reasoning TEXT NOT NULL, 
	tool_calls JSON, 
	tool_call_id VARCHAR(80) NOT NULL, 
	tool_name VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	in_context BOOL NOT NULL, 
	prompt_tokens INTEGER NOT NULL, 
	completion_tokens INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE schedules (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	conversation_id INTEGER, 
	prompt TEXT NOT NULL, 
	every_seconds INTEGER NOT NULL, 
	next_run DATETIME, 
	enabled BOOL NOT NULL, 
	last_run DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE tasks (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	conversation_id INTEGER, 
	title VARCHAR(255) NOT NULL, 
	detail TEXT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE attachments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	conversation_id INTEGER NOT NULL, 
	message_id INTEGER, 
	filename VARCHAR(255) NOT NULL, 
	mime VARCHAR(128) NOT NULL, 
	size INTEGER NOT NULL, 
	path TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE SET NULL
);
