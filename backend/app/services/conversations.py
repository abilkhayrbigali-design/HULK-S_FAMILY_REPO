import hashlib
import json

from sqlalchemy import func, select, text

from app.models.data import Conversation, Message, TranscriptVersion
from app.services.errors import require


def import_records(session, payload, source="upload", group_id="UPLOAD"):
    # Serialize the short import write transaction; no HTTP runs in this transaction.
    if session.bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    summary = dict(
        group_id=group_id,
        received=len(payload.records),
        created=0,
        new_versions=0,
        unchanged=0,
        conversation_ids=[],
    )
    for record in payload.records:
        row = session.scalar(
            select(Conversation).where(
                Conversation.source == source,
                Conversation.group_id == group_id,
                Conversation.external_id == record.conversation_id,
            )
        )
        fresh = row is None
        if fresh:
            row = Conversation(source=source, group_id=group_id, external_id=record.conversation_id)
            session.add(row)
            session.flush()
        digest = hashlib.sha256(
            json.dumps(
                record.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        version = session.scalar(
            select(TranscriptVersion).where(
                TranscriptVersion.conversation_id == row.id,
                TranscriptVersion.content_hash == digest,
            )
        )
        if version is None:
            version = TranscriptVersion(
                conversation_id=row.id,
                content_hash=digest,
                language=record.language,
                channel=record.channel,
                audio_url=record.audio_url,
            )
            session.add(version)
            session.flush()
            for position, message in enumerate(record.transcript, 1):
                session.add(
                    Message(
                        transcript_version_id=version.id,
                        id=f"m{position:04d}",
                        position=position,
                        **message.model_dump(),
                    )
                )
            summary["created" if fresh else "new_versions"] += 1
        else:
            summary["unchanged"] += 1
        row.current_transcript_version_id = version.id
        summary["conversation_ids"].append(row.id)
    session.flush()
    session.info["cqa_import_summary"] = {
        key: summary[key] for key in ("received", "created", "new_versions", "unchanged")
    }
    return summary


def summary(session, row):
    version = session.get(TranscriptVersion, row.current_transcript_version_id)
    count = session.scalar(
        select(func.count()).select_from(Message).where(Message.transcript_version_id == version.id)
    )
    return _summary(row, version, count)


def _summary(row, version, message_count):
    return dict(
        id=row.id,
        external_id=row.external_id,
        source=row.source,
        group_id=row.group_id,
        language=version.language,
        channel=version.channel,
        current_transcript_version_id=row.current_transcript_version_id,
        message_count=message_count,
        latest_evaluation=None,
    )


def detail(session, conversation_id, transcript_version_id=None):
    row = require(session.get(Conversation, conversation_id))
    version = require(
        session.scalar(
            select(TranscriptVersion).where(
                TranscriptVersion.id
                == (transcript_version_id or row.current_transcript_version_id),
                TranscriptVersion.conversation_id == row.id,
            )
        )
    )
    messages = session.scalars(
        select(Message)
        .where(Message.transcript_version_id == version.id)
        .order_by(Message.position)
    ).all()
    result = _summary(row, version, len(messages))
    result.update(
        transcript_version_id=version.id,
        audio_url=version.audio_url,
        messages=[
            {k: getattr(m, k) for k in ("id", "position", "timestamp", "speaker", "text")}
            for m in messages
        ],
    )
    return result
