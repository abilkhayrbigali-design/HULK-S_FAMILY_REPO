import json
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.models.data import Base, now, uuid


def score_json(value):
    """Emit finite Decimal values as JSON numbers without a binary float conversion."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite score")
        return str(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(f"{json.dumps(key)}:{score_json(item)}" for key, item in value.items())
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(map(score_json, value)) + "]"
    return json.dumps(value, allow_nan=False)


class ScoreJSON(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else score_json(value)

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(value, parse_float=Decimal)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid)
    conversation_id: Mapped[str]
    transcript_version_id: Mapped[str]
    checklist_version_id: Mapped[str] = mapped_column(ForeignKey("checklist_versions.id"))
    status: Mapped[str] = mapped_column(default="queued")
    revision: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=now)
    completed_at: Mapped[str | None]
    original: Mapped[dict | None] = mapped_column(ScoreJSON)
    final: Mapped[dict | None] = mapped_column(ScoreJSON)
    provenance: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    provider_review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))


class Appeal(Base):
    __tablename__ = "appeals"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id"))
    criterion_id: Mapped[str]
    comment: Mapped[str]
    status: Mapped[str] = mapped_column(default="open")
    base_revision: Mapped[int]
    created_at: Mapped[str] = mapped_column(default=now)


class AppealDecision(Base):
    __tablename__ = "appeal_decisions"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid)
    appeal_id: Mapped[str] = mapped_column(ForeignKey("appeals.id"), unique=True)
    action: Mapped[str]
    reason: Mapped[str]
    expected_revision: Mapped[int]
    created_at: Mapped[str] = mapped_column(default=now)
    result: Mapped[dict] = mapped_column(ScoreJSON)
