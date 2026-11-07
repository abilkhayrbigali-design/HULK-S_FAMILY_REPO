"""Durable evaluation queue, assessments and appeals."""

from alembic import op

revision = "c002"
down_revision = "c001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE evaluations (
            id TEXT NOT NULL PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            transcript_version_id TEXT NOT NULL,
            checklist_version_id TEXT NOT NULL REFERENCES checklist_versions(id),
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
            revision INTEGER NOT NULL CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            original TEXT,
            final TEXT,
            provenance JSON,
            provider_review_reasons JSON NOT NULL,
            error JSON,
            FOREIGN KEY (conversation_id, transcript_version_id)
                REFERENCES transcript_versions(conversation_id, id),
            CHECK ((completed_at IS NOT NULL) = (status IN ('completed', 'failed'))),
            CHECK ((original IS NOT NULL) = (status = 'completed')),
            CHECK ((final IS NOT NULL) = (status = 'completed')),
            CHECK ((error IS NOT NULL) = (status = 'failed')),
            CHECK (status = 'completed' OR revision = 0),
            CHECK (status <> 'completed' OR provenance IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX ix_evaluations_queue ON evaluations(status, created_at, id)")
    op.execute(
        "CREATE INDEX ix_evaluations_conversation ON evaluations(conversation_id, created_at)"
    )
    op.execute("""
        CREATE TRIGGER evaluations_immutable BEFORE UPDATE ON evaluations
        WHEN NEW.id IS NOT OLD.id
          OR NEW.conversation_id IS NOT OLD.conversation_id
          OR NEW.transcript_version_id IS NOT OLD.transcript_version_id
          OR NEW.checklist_version_id IS NOT OLD.checklist_version_id
          OR NEW.created_at IS NOT OLD.created_at
          OR (OLD.original IS NOT NULL AND NEW.original IS NOT OLD.original)
        BEGIN SELECT RAISE(ABORT, 'immutable evaluation data'); END
    """)
    op.execute("""
        CREATE TRIGGER evaluations_status_transition BEFORE UPDATE OF status ON evaluations
        WHEN OLD.status <> NEW.status AND NOT (
            (OLD.status = 'queued' AND NEW.status = 'running')
            OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
        )
        BEGIN SELECT RAISE(ABORT, 'invalid evaluation transition'); END
    """)
    op.execute("""
        CREATE TABLE appeals (
            id TEXT NOT NULL PRIMARY KEY,
            evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
            criterion_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
            base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX ix_appeals_open ON appeals(evaluation_id, criterion_id)
        WHERE status = 'open'
    """)
    op.execute("CREATE INDEX ix_appeals_evaluation ON appeals(evaluation_id, created_at)")
    op.execute("CREATE INDEX ix_appeals_status ON appeals(status, created_at)")
    op.execute("""
        CREATE TABLE appeal_decisions (
            id TEXT NOT NULL PRIMARY KEY,
            appeal_id TEXT NOT NULL UNIQUE REFERENCES appeals(id),
            action TEXT NOT NULL CHECK (action IN ('confirm', 'override')),
            reason TEXT NOT NULL,
            expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
            created_at TEXT NOT NULL,
            result TEXT NOT NULL
        )
    """)
    for table in ("evaluations", "appeals", "appeal_decisions"):
        op.execute(f"""
            CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, 'immutable evaluation history'); END
        """)
    op.execute("""
        CREATE TRIGGER appeal_decisions_no_update BEFORE UPDATE ON appeal_decisions
        BEGIN SELECT RAISE(ABORT, 'immutable appeal decision'); END
    """)
    op.execute("""
        CREATE TRIGGER appeals_immutable BEFORE UPDATE ON appeals
        WHEN NEW.id IS NOT OLD.id OR NEW.evaluation_id IS NOT OLD.evaluation_id
          OR NEW.criterion_id IS NOT OLD.criterion_id OR NEW.comment IS NOT OLD.comment
          OR NEW.base_revision IS NOT OLD.base_revision OR NEW.created_at IS NOT OLD.created_at
          OR OLD.status <> 'open' OR NEW.status <> 'resolved'
        BEGIN SELECT RAISE(ABORT, 'immutable appeal history'); END
    """)


def downgrade():
    raise NotImplementedError("Evaluation history is immutable")
