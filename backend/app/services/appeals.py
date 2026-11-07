import logging
from decimal import Decimal

from sqlalchemy import select, text

from app.db.session import log_after_commit
from app.evaluation.evidence import CriterionResult, EvaluationValidationError
from app.evaluation.scoring import build_assessment
from app.models.evaluations import Appeal, AppealDecision, Evaluation
from app.schemas.evaluations import OverrideDecision
from app.services import evaluations
from app.services.errors import ServiceError, require

logger = logging.getLogger("cqa.appeals")


def _conflict(code, message):
    logger.warning("event=appeal_conflict error=%s", code)
    raise ServiceError(409, code, message)


def _write_lock(db):
    # Claim the SQLite writer before reading the revision or open-appeal state.
    db.execute(text("BEGIN IMMEDIATE"))


def _criterion(assessment, criterion_id):
    return require(
        next((c for c in assessment["criteria"] if c["criterion_id"] == criterion_id), None)
    )


def _decision_view(decision):
    return {
        key: getattr(decision, key)
        for key in (
            "id",
            "appeal_id",
            "action",
            "reason",
            "expected_revision",
            "created_at",
            "result",
        )
    }


def present(db, appeal, evaluation=None):
    if evaluation is None:
        evaluation = require(db.get(Evaluation, appeal.evaluation_id))
    decision = db.scalar(select(AppealDecision).where(AppealDecision.appeal_id == appeal.id))
    return {
        **{
            key: getattr(appeal, key)
            for key in (
                "id",
                "evaluation_id",
                "criterion_id",
                "comment",
                "status",
                "base_revision",
                "created_at",
            )
        },
        "current_revision": evaluation.revision,
        "original": _criterion(evaluation.original, appeal.criterion_id),
        "current": _criterion(evaluation.final, appeal.criterion_id),
        "decision": _decision_view(decision) if decision is not None else None,
    }


def create(db, evaluation_id, payload):
    _write_lock(db)
    evaluation = require(db.get(Evaluation, evaluation_id))
    if evaluation.status != "completed":
        _conflict("EVALUATION_NOT_COMPLETED", "Оценка ещё не завершена")
    _criterion(evaluation.original, payload.criterion_id)
    existing = db.scalar(
        select(Appeal.id).where(
            Appeal.evaluation_id == evaluation_id,
            Appeal.criterion_id == payload.criterion_id,
            Appeal.status == "open",
        )
    )
    if existing is not None:
        _conflict("APPEAL_ALREADY_OPEN", "Для критерия уже есть открытая апелляция")
    appeal = Appeal(
        evaluation_id=evaluation_id,
        criterion_id=payload.criterion_id,
        comment=payload.comment,
        base_revision=evaluation.revision,
    )
    db.add(appeal)
    db.flush()
    log_after_commit(db, "cqa.appeals", "appeal_created")
    return present(db, appeal, evaluation)


def _result_fields(criterion):
    result = {key: criterion[key] for key in CriterionResult.model_fields}
    if isinstance(result["answer"], Decimal):
        # Stored decimal fractions originated as finite JSON float answers.
        result["answer"] = float(result["answer"])
    return result


def decide(db, appeal_id, payload):
    _write_lock(db)
    appeal = require(db.get(Appeal, appeal_id))
    evaluation = require(db.get(Evaluation, appeal.evaluation_id))
    if appeal.status != "open":
        _conflict("APPEAL_ALREADY_RESOLVED", "Апелляция уже рассмотрена")
    if evaluation.revision != payload.expected_revision:
        _conflict("VERSION_CONFLICT", "Оценка уже изменена")

    rows = {row["criterion_id"]: _result_fields(row) for row in evaluation.final["criteria"]}
    if isinstance(payload, OverrideDecision):
        rows[appeal.criterion_id].update(
            applicability=payload.applicability,
            answer=payload.answer,
            evidence=[item.model_dump() for item in payload.evidence],
            explanation=payload.reason,
            review_reasons=[],
        )
    transcript, checklist = evaluations.snapshots(db, evaluation)
    try:
        final = build_assessment(
            criteria=checklist["criteria"],
            criterion_results=list(rows.values()),
            transcript_snapshot=transcript,
            transcript_version_id=evaluation.transcript_version_id,
        )
    except EvaluationValidationError:
        raise ServiceError(422, "VALIDATION_ERROR", "Некорректное решение по критерию") from None

    accepted = _result_fields(_criterion(final, appeal.criterion_id))
    db.add(
        AppealDecision(
            appeal_id=appeal_id,
            action=payload.action,
            reason=payload.reason,
            expected_revision=payload.expected_revision,
            result=accepted,
        )
    )
    appeal.status = "resolved"
    evaluation.revision += 1
    evaluation.final = final
    db.flush()
    log_after_commit(db, "cqa.appeals", "appeal_decision_saved")
    return dict(appeal=present(db, appeal, evaluation), evaluation=evaluations.present(evaluation))
