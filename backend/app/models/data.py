"""Immutable snapshot tables; mutable heads use optimistic version checks."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid():
    return str(uuid4())


def now():
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("source", "group_id", "external_id"),
        CheckConstraint("source IN ('upload', 'dataset')"),
        ForeignKeyConstraint(
            ["id", "current_transcript_version_id"],
            ["transcript_versions.conversation_id", "transcript_versions.id"],
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid)
    source: Mapped[str]
    group_id: Mapped[str]
    external_id: Mapped[str]
    current_transcript_version_id: Mapped[str | None]


class TranscriptVersion(Base):
    __tablename__ = "transcript_versions"
    __table_args__ = (
        UniqueConstraint("conversation_id", "content_hash"),
        UniqueConstraint("conversation_id", "id"),
        CheckConstraint("language IN ('RU', 'KZ', 'EN')"),
        CheckConstraint("channel IN ('agent chat', 'voice call')"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    content_hash: Mapped[str]
    language: Mapped[str]
    channel: Mapped[str]
    audio_url: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("transcript_version_id", "position"),
        CheckConstraint("position > 0"),
        CheckConstraint("speaker IN ('client', 'operator', 'bot', 'system')"),
    )
    transcript_version_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_versions.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    position: Mapped[int]
    timestamp: Mapped[str]
    speaker: Mapped[str]
    text: Mapped[str]


class Checklist(Base):
    __tablename__ = "checklists"
    __table_args__ = (
        CheckConstraint("current_version_number >= 1"),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["checklist_versions.checklist_id", "checklist_versions.id"],
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid)
    name: Mapped[str]
    current_version_number: Mapped[int] = mapped_column(default=1)
    current_version_id: Mapped[str | None]
    seed_key: Mapped[str | None] = mapped_column(unique=True)


class ChecklistVersion(Base):
    __tablename__ = "checklist_versions"
    __table_args__ = (
        UniqueConstraint("checklist_id", "version_number"),
        UniqueConstraint("checklist_id", "id"),
        CheckConstraint("version_number >= 1"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid)
    checklist_id: Mapped[str] = mapped_column(ForeignKey("checklists.id"), index=True)
    name: Mapped[str]
    version_number: Mapped[int]
    created_at: Mapped[str] = mapped_column(default=now)
    criteria: Mapped[list] = mapped_column(JSON)
