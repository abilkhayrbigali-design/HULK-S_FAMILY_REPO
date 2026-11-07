"""Conversation and checklist snapshots; SQLite initial schema."""

from alembic import op

revision = "c001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE conversations (
	id VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	group_id VARCHAR NOT NULL, 
	external_id VARCHAR NOT NULL, 
	current_transcript_version_id VARCHAR, 
	PRIMARY KEY (id), 
	UNIQUE (source, group_id, external_id), 
	CHECK (source IN ('upload', 'dataset')), 
	FOREIGN KEY(id, current_transcript_version_id)
        REFERENCES transcript_versions (conversation_id, id)
)

""")
    op.execute("""
CREATE TABLE transcript_versions (
	id VARCHAR NOT NULL, 
	conversation_id VARCHAR NOT NULL, 
	content_hash VARCHAR NOT NULL, 
	language VARCHAR NOT NULL, 
	channel VARCHAR NOT NULL, 
	audio_url VARCHAR, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (conversation_id, content_hash), 
	UNIQUE (conversation_id, id), 
	CHECK (language IN ('RU', 'KZ', 'EN')), 
	CHECK (channel IN ('agent chat', 'voice call')), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
)

""")
    op.execute(
        "CREATE INDEX ix_transcript_versions_conversation_id "
        "ON transcript_versions (conversation_id)"
    )
    op.execute("""
CREATE TABLE messages (
	transcript_version_id VARCHAR NOT NULL, 
	id VARCHAR NOT NULL, 
	position INTEGER NOT NULL, 
	timestamp VARCHAR NOT NULL, 
	speaker VARCHAR NOT NULL, 
	text VARCHAR NOT NULL, 
	PRIMARY KEY (transcript_version_id, id), 
	UNIQUE (transcript_version_id, position), 
	CHECK (position > 0), 
	CHECK (speaker IN ('client', 'operator', 'bot', 'system')), 
	FOREIGN KEY(transcript_version_id) REFERENCES transcript_versions (id)
)

""")
    op.execute("""
CREATE TABLE checklists (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	current_version_number INTEGER NOT NULL, 
	current_version_id VARCHAR, 
	seed_key VARCHAR, 
	PRIMARY KEY (id), 
	CHECK (current_version_number >= 1), 
	FOREIGN KEY(id, current_version_id) REFERENCES checklist_versions (checklist_id, id), 
	UNIQUE (seed_key)
)

""")
    op.execute("""
CREATE TABLE checklist_versions (
	id VARCHAR NOT NULL, 
	checklist_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	version_number INTEGER NOT NULL, 
	created_at VARCHAR NOT NULL, 
	criteria JSON NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (checklist_id, version_number), 
	UNIQUE (checklist_id, id), 
	CHECK (version_number >= 1), 
	FOREIGN KEY(checklist_id) REFERENCES checklists (id)
)

""")
    op.execute(
        "CREATE INDEX ix_checklist_versions_checklist_id ON checklist_versions (checklist_id)"
    )
    for table in ("transcript_versions", "messages", "checklist_versions"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER immutable_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END"
            )


def downgrade():
    raise RuntimeError("Destructive snapshot downgrade is intentionally unsupported")
