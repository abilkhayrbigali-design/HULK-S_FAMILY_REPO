from sqlalchemy import select, update

from app.db.session import log_after_commit
from app.models.data import Conversation, TranscriptVersion, now
from app.models.evaluations import Evaluation
from app.services.checklists import get_version
from app.services.conversations import transcript_snapshot
from app.services.errors import require

FAILURES = {
    "AI_NOT_CONFIGURED": "Провайдер оценки недоступен",
    "AI_PROVIDER_ERROR": "Провайдер оценки завершился ошибкой",
    "AI_INVALID_RESULT": "Ответ провайдера не прошёл проверку",
    "INTERRUPTED": "Оценка прервана до завершения",
    "EVALUATION_INTERNAL_ERROR": "Не удалось завершить оценку",
}


def failure(code):
    return dict(code=code, message=FAILURES[code], details=[])


def present(row):
    reasons = []
    if row.final is not None:
        reasons = list(
            dict.fromkeys(
                [
                    *row.provider_review_reasons,
                    *(
                        reason
                        for criterion in row.final["criteria"]
                        for reason in criterion["review_reasons"]
                    ),
                ]
            )
        )
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "conversation_id",
                "transcript_version_id",
                "checklist_version_id",
                "status",
                "revision",
                "created_at",
                "completed_at",
                "original",
                "final",
                "provenance",
                "error",
            )
        },
        "review_required": bool(reasons),
        "review_reasons": reasons,
    }


def create(db, payload):
    conversation = require(db.get(Conversation, str(payload.conversation_id)))
    checklist_version_id = str(payload.checklist_version_id)
    get_version(db, checklist_version_id)
    evaluation = Evaluation(
        conversation_id=conversation.id,
        transcript_version_id=conversation.current_transcript_version_id,
        checklist_version_id=checklist_version_id,
    )
    db.add(evaluation)
    db.flush()
    log_after_commit(db, "cqa.worker", "evaluation_queued")
    return present(evaluation)


def snapshots(db, row):
    return (
        transcript_snapshot(db, require(db.get(TranscriptVersion, row.transcript_version_id))),
        get_version(db, row.checklist_version_id),
    )


def recover(sessions):
    with sessions.begin() as db:
        return db.execute(
            update(Evaluation)
            .where(Evaluation.status == "running")
            .values(status="failed", completed_at=now(), error=failure("INTERRUPTED"))
        ).rowcount


def claim(sessions):
    candidate = (
        select(Evaluation.id)
        .where(Evaluation.status == "queued")
        .order_by(Evaluation.created_at, Evaluation.id)
        .limit(1)
        .scalar_subquery()
    )
    with sessions.begin() as db:
        return db.scalar(
            update(Evaluation)
            .where(Evaluation.id == candidate, Evaluation.status == "queued")
            .values(status="running")
            .returning(Evaluation.id)
        )


def update_running(sessions, evaluation_id, **values):
    with sessions.begin() as db:
        result = db.execute(
            update(Evaluation)
            .where(Evaluation.id == evaluation_id, Evaluation.status == "running")
            .values(**values)
        )
        return result.rowcount == 1
