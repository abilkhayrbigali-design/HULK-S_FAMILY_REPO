"""Validate result structure and resolve evidence against the pinned transcript."""

from bisect import bisect_left
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from app.schemas.data import CriteriaInput, Criterion, Schema

MessageId = Annotated[str, StringConstraints(pattern=r"^m[0-9]{4,}$")]
NonBlank = Annotated[str, StringConstraints(pattern=r"\S")]
AnswerNumber = (
    Annotated[StrictInt, Field(ge=0)] | Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
)


class EvaluationValidationError(ValueError):
    """Expected rejection carrying a fixed, internal code safe to log."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _ProviderSchema(Schema):
    model_config = ConfigDict(revalidate_instances="always")


class MessageEvidence(_ProviderSchema):
    kind: Literal["message"]
    message_id: MessageId


class RangeEvidence(_ProviderSchema):
    kind: Literal["range"]
    start_message_id: MessageId
    end_message_id: MessageId


class CriterionResult(_ProviderSchema):
    criterion_id: NonBlank
    applicability: Literal["applicable", "not_applicable"]
    answer: StrictBool | AnswerNumber | None
    explanation: NonBlank
    evidence: Annotated[
        list[Annotated[MessageEvidence | RangeEvidence, Field(discriminator="kind")]],
        Field(min_length=1),
    ]
    review_reasons: list[NonBlank]


class _MessageLocation(BaseModel):
    id: MessageId
    position: Annotated[StrictInt, Field(ge=1)]
    speaker: Literal["operator", "client", "bot", "system"]


class _TranscriptLocations(BaseModel):
    # Only these fields are needed from the full, already persisted snapshot.
    id: str
    messages: Annotated[list[_MessageLocation], Field(min_length=1)]


_RESULTS = TypeAdapter(list[CriterionResult])


def validate_results(
    criteria: object,
    criterion_results: object,
    transcript_snapshot: object,
    transcript_version_id: str,
) -> list[tuple[Criterion, CriterionResult]]:
    """Return validated pairs in checklist order for scoring or appeal recalculation."""
    try:
        checklist = CriteriaInput.model_validate({"criteria": criteria})
        snapshot = _TranscriptLocations.model_validate(transcript_snapshot)
        results = _RESULTS.validate_python(criterion_results)
    except (ValidationError, OverflowError):
        raise EvaluationValidationError("INVALID_ASSESSMENT_INPUT") from None

    if snapshot.id != transcript_version_id:
        raise EvaluationValidationError("TRANSCRIPT_VERSION_MISMATCH")
    positions = {message.id: message.position for message in snapshot.messages}
    if len(positions) != len(snapshot.messages) or len(set(positions.values())) != len(positions):
        raise EvaluationValidationError("AMBIGUOUS_TRANSCRIPT")
    operators = sorted(m.position for m in snapshot.messages if m.speaker == "operator")

    by_id = {result.criterion_id: result for result in results}
    if len(by_id) != len(results):
        raise EvaluationValidationError("DUPLICATE_CRITERION_RESULT")
    if by_id.keys() != {criterion.id for criterion in checklist.criteria}:
        raise EvaluationValidationError("CRITERION_COVERAGE_MISMATCH")

    pairs = []
    for criterion in checklist.criteria:
        result = by_id[criterion.id]
        _validate_answer(criterion, result)
        reports_gross_error = (
            criterion.type == "flag" and criterion.id == "gross_errors" and result.answer is True
        )
        _validate_evidence(result, positions, operators, reports_gross_error)
        pairs.append((criterion, result))
    return pairs


def _validate_answer(criterion: Criterion, result: CriterionResult) -> None:
    answer = result.answer
    if result.applicability == "not_applicable":
        if not criterion.allow_na:
            raise EvaluationValidationError("NA_NOT_ALLOWED")
        if answer is not None:
            raise EvaluationValidationError("INVALID_CRITERION_ANSWER")
    elif criterion.type == "scale":
        if answer is None or isinstance(answer, bool):
            raise EvaluationValidationError("INVALID_CRITERION_ANSWER")
        if Decimal(str(answer)) not in (Decimal(str(step.value)) for step in criterion.scale or ()):
            raise EvaluationValidationError("INVALID_SCALE_STEP")
    elif not isinstance(answer, bool):
        raise EvaluationValidationError("INVALID_CRITERION_ANSWER")


def _validate_evidence(
    result: CriterionResult,
    positions: dict[str, int],
    operators: list[int],
    reports_gross_error: bool,
) -> None:
    cites_operator = False
    for item in result.evidence:
        if isinstance(item, MessageEvidence):
            start_id = end_id = item.message_id
        else:
            start_id, end_id = item.start_message_id, item.end_message_id
        if start_id not in positions or end_id not in positions:
            raise EvaluationValidationError("UNKNOWN_EVIDENCE_MESSAGE")
        start, end = positions[start_id], positions[end_id]
        if start > end:
            raise EvaluationValidationError("REVERSED_EVIDENCE_RANGE")
        index = bisect_left(operators, start)
        cites_operator |= index < len(operators) and operators[index] <= end

    # Unlike custom answers, this flag specifically asserts an operator's action.
    if reports_gross_error and not cites_operator:
        raise EvaluationValidationError("MISSING_OPERATOR_EVIDENCE")
