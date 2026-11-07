from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.api.routes.data import DB, Limit, Offset, page
from app.models.evaluations import Appeal, Evaluation, score_json
from app.schemas.evaluations import (
    AppealCreate,
    AppealStatus,
    DecisionCreate,
    EvaluationCreate,
    EvaluationStatus,
)
from app.services import appeals, evaluations
from app.services.errors import ServiceError, require

router = APIRouter(prefix="/api/v1")


class AssessmentResponse(Response):
    media_type = "application/json"

    def render(self, content):
        return score_json(content).encode("utf-8")


@router.post("/evaluations", status_code=202)
def create_evaluation(payload: EvaluationCreate, request: Request, db: DB):
    if getattr(request.app.state, "evaluation_provider", None) is None:
        error = evaluations.failure("AI_NOT_CONFIGURED")
        raise ServiceError(503, error["code"], error["message"])
    return AssessmentResponse(evaluations.create(db, payload), status_code=202)


@router.get("/evaluations")
def list_evaluations(
    db: DB,
    conversation_id: UUID | None = None,
    status: EvaluationStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    statement = select(Evaluation).order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
    if conversation_id is not None:
        statement = statement.where(Evaluation.conversation_id == str(conversation_id))
    if status is not None:
        statement = statement.where(Evaluation.status == status)
    return AssessmentResponse(page(db, statement, limit, offset, evaluations.present))


@router.get("/evaluations/{id}")
def get_evaluation(id: UUID, db: DB):
    return AssessmentResponse(evaluations.present(require(db.get(Evaluation, str(id)))))


@router.post("/evaluations/{id}/appeals", status_code=201)
def create_appeal(id: UUID, payload: AppealCreate, db: DB):
    return AssessmentResponse(appeals.create(db, str(id), payload), status_code=201)


@router.get("/appeals")
def list_appeals(
    db: DB,
    evaluation_id: UUID | None = None,
    status: AppealStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    statement = select(Appeal).order_by(Appeal.created_at.desc(), Appeal.id.desc())
    if evaluation_id is not None:
        statement = statement.where(Appeal.evaluation_id == str(evaluation_id))
    if status is not None:
        statement = statement.where(Appeal.status == status)
    return AssessmentResponse(
        page(db, statement, limit, offset, lambda row: appeals.present(db, row))
    )


@router.get("/appeals/{id}")
def get_appeal(id: UUID, db: DB):
    return AssessmentResponse(appeals.present(db, require(db.get(Appeal, str(id)))))


@router.post("/appeals/{id}/decisions")
def decide_appeal(id: UUID, payload: DecisionCreate, db: DB):
    return AssessmentResponse(appeals.decide(db, str(id), payload))
