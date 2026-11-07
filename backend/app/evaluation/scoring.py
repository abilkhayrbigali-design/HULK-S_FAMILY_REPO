"""Build a validated Assessment without a database or an AI provider."""

import logging
from collections import defaultdict
from collections.abc import Collection
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any, NamedTuple

from app.evaluation.evidence import CriterionResult, EvaluationValidationError, validate_results
from app.schemas.data import Criterion

logger = logging.getLogger("cqa.evaluation")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_CENT = Decimal("0.01")


def build_assessment(
    *,
    criteria: object,
    criterion_results: object,
    transcript_snapshot: object,
    transcript_version_id: str,
) -> dict[str, Any]:
    """Build the internal Assessment with Decimal fields from the pinned versions.

    Pass ChecklistVersion.criteria, ProviderResult.criterion_results (or the
    final rows after an appeal), and the TranscriptSnapshot loaded for the
    evaluation's independently stored transcript_version_id.
    """
    try:
        pairs = validate_results(
            criteria, criterion_results, transcript_snapshot, transcript_version_id
        )
    except EvaluationValidationError as exc:
        logger.warning("event=assessment_rejected error=%s", exc.code)
        raise
    assessment = _score(pairs)
    logger.info("event=assessment_calculated status=completed criteria=%d", len(pairs))
    return assessment


class _Points(NamedTuple):
    weight: Decimal
    answer: Decimal
    maximum: Decimal


def _points(criterion: Criterion, result: CriterionResult, weight: Decimal) -> _Points:
    if result.answer is None or criterion.type == "flag":
        return _Points(_ZERO, _ZERO, _ONE)
    if criterion.type == "scale":
        return _Points(
            weight,
            Decimal(str(result.answer)),
            max(Decimal(str(step.value)) for step in criterion.scale or ()),
        )
    return _Points(weight, _ONE if result.answer else _ZERO, _ONE)


def _totals(rows: Collection[_Points]) -> tuple[Decimal, Decimal, Decimal | None]:
    weight = sum((row.weight for row in rows), _ZERO)
    if not weight:
        return _ZERO, _ZERO, None

    # Combine contributions as ratios, so even an exact half-cent from a
    # repeating scale fraction reaches ROUND_HALF_UP on the correct side.
    numerator, denominator = _ZERO, _ONE
    for row in rows:
        numerator = numerator * row.maximum + denominator * row.weight * row.answer
        denominator *= row.maximum
    total = (100 * numerator / (denominator * weight)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return numerator / denominator, weight, total


def _score(pairs: list[tuple[Criterion, CriterionResult]]) -> dict[str, Any]:
    weighted = []
    sections = defaultdict(list)
    gross_error = None
    for criterion, result in pairs:
        weight = Decimal(str(criterion.weight))
        points = _points(criterion, result, weight)
        weighted.append((result, weight, points))
        section = criterion.section if criterion.section and criterion.section.strip() else None
        sections[section].append(points)
        if criterion.type == "flag" and criterion.id == "gross_errors":
            gross_error = result.answer

    rows = [points for _, _, points in weighted]
    # Bound the digit span of products/sums and leave room to distinguish
    # a quotient just below a rounding boundary. Ignore the caller's context.
    span = sum(
        max(value.adjusted() + 1, 0) + max(-value.as_tuple().exponent, 0)
        for row in rows
        for value in row
    )
    with localcontext(Context(prec=max(28, 2 * span + 12))):
        _, applicable_weight, total = _totals(rows)
        criterion_scores = []
        for result, weight, row in weighted:
            numerator = row.weight * row.answer
            criterion_scores.append(
                {
                    **result.model_dump(),
                    "weight": weight,
                    "base_points": numerator / row.maximum,
                    "effective_weight": 100 * row.weight / applicable_weight
                    if applicable_weight
                    else _ZERO,
                    "effective_points": 100 * numerator / (row.maximum * applicable_weight)
                    if applicable_weight
                    else _ZERO,
                    "review_required": bool(result.review_reasons),
                }
            )
        section_scores = []
        for name, section_rows in sections.items():
            base, weight, section_total = _totals(section_rows)
            section_scores.append(
                {
                    "name": name,
                    "base_points": base,
                    "applicable_weight": weight,
                    "total": section_total,
                }
            )

    return {
        "total": total,
        "no_applicable_reason": "нет применимых критериев с весом" if total is None else None,
        "gross_error": gross_error,
        "criteria": criterion_scores,
        "sections": section_scores,
    }
