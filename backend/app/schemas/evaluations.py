from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt

from app.evaluation.evidence import AnswerNumber, MessageEvidence, RangeEvidence
from app.schemas.data import Schema, Text

EvaluationStatus = Literal["queued", "running", "completed", "failed"]
AppealStatus = Literal["open", "resolved"]


class EvaluationCreate(Schema):
    conversation_id: UUID
    checklist_version_id: UUID


class AppealCreate(Schema):
    criterion_id: Text
    comment: Text


class Decision(Schema):
    reason: Text
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class ConfirmDecision(Decision):
    action: Literal["confirm"]


class OverrideDecision(Decision):
    action: Literal["override"]
    applicability: Literal["applicable", "not_applicable"]
    answer: StrictBool | AnswerNumber | None
    evidence: Annotated[
        list[Annotated[MessageEvidence | RangeEvidence, Field(discriminator="kind")]],
        Field(min_length=1),
    ]


DecisionCreate = Annotated[ConfirmDecision | OverrideDecision, Field(discriminator="action")]
