from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.models.data import Checklist, Conversation, TranscriptVersion
from app.schemas.data import (
    Channel,
    ChecklistCreate,
    ChecklistUpdate,
    DatasetImport,
    ImportRequest,
    Language,
)
from app.services import checklists, conversations, datasets

router = APIRouter(prefix="/api/v1")


def session(request: Request):
    with request.app.state.session_factory() as db:
        with db.begin():
            # SQLite legacy SELECT does not start a snapshot for multi-query views.
            if request.method == "GET":
                db.connection().exec_driver_sql("BEGIN")
            yield db


DB = Annotated[object, Depends(session, scope="function")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def page(db, statement, limit, offset, convert):
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    return dict(
        items=(
            [convert(row) for row in db.scalars(statement.limit(limit).offset(offset))]
            if offset < total
            else []
        ),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/checklists")
def list_checklists(db: DB, limit: Limit = 50, offset: Offset = 0):
    return page(db, select(Checklist).order_by(Checklist.id), limit, offset, checklists.summary)


@router.post("/checklists", status_code=201)
def create_checklist(payload: ChecklistCreate, db: DB):
    return checklists.create(db, payload)


@router.get("/checklists/{id}")
def get_checklist(id: UUID, db: DB):
    return checklists.detail(db, str(id))


@router.get("/checklist-versions/{id}")
def get_version(id: UUID, db: DB):
    return checklists.get_version(db, str(id))


@router.post("/checklists/{id}/versions", status_code=201)
def create_version(id: UUID, payload: ChecklistUpdate, db: DB):
    return checklists.add_version(db, str(id), payload)


@router.post("/conversations/import")
def import_upload(payload: ImportRequest, db: DB):
    return conversations.import_records(db, payload)


@router.post("/datasets/import")
def import_dataset(payload: DatasetImport, request: Request):
    # Fetch and validate before opening any DB transaction.
    records = datasets.fetch_records(
        request.app.state.dataset_url,
        payload.group_id,
        getattr(request.app.state, "dataset_transport", None),
    )
    with request.app.state.session_factory.begin() as db:
        return conversations.import_records(
            db, records, source="dataset", group_id=payload.group_id
        )


@router.get("/conversations")
def list_conversations(
    db: DB,
    language: Language | None = None,
    channel: Channel | None = None,
    group_id: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    statement = select(Conversation).join(
        TranscriptVersion,
        Conversation.current_transcript_version_id == TranscriptVersion.id,
    )
    for value, column in (
        (language, TranscriptVersion.language),
        (channel, TranscriptVersion.channel),
        (group_id, Conversation.group_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    return page(
        db,
        statement.order_by(Conversation.id),
        limit,
        offset,
        lambda row: conversations.summary(db, row),
    )


@router.get("/conversations/{id}")
def get_conversation(id: UUID, db: DB, transcript_version_id: UUID | None = None):
    return conversations.detail(
        db, str(id), str(transcript_version_id) if transcript_version_id else None
    )
