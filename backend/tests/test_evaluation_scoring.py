import json
import logging
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.evaluation.evidence import EvaluationValidationError
from app.evaluation.scoring import build_assessment

ROOT = Path(__file__).resolve().parents[2]


def example(name):
    path = ROOT / "data" / "fixtures" / "synthetic" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def criterion_result(criterion_id, answer):
    return {
        "criterion_id": criterion_id,
        "applicability": "not_applicable" if answer is None else "applicable",
        "answer": answer,
        "explanation": "Synthetic assessment",
        "evidence": [{"kind": "range", "start_message_id": "m0001", "end_message_id": "m0004"}],
        "review_reasons": [],
    }


@pytest.fixture
def request_data():
    snapshot = example("transcript.snapshot")
    return {
        "criteria": example("checklist-version.response")["criteria"],
        "criterion_results": example("provider.response")["criterion_results"],
        "transcript_snapshot": snapshot,
        "transcript_version_id": snapshot["id"],
    }


@pytest.mark.parametrize(
    ("changes", "expected", "gross"),
    [
        ({}, "100.00", False),
        ({"contact": False}, "90.00", False),
        ({"management": 7}, "95.50", False),
        ({"identification": None}, "100.00", False),
        ({"identification": None, "contact": False}, "87.50", False),
        ({"gross_errors": True}, "100.00", True),
        (
            {
                "contact": False,
                "needs": False,
                "identification": False,
                "consultation": 0,
                "empathy": False,
                "management": 0,
                "closing": False,
            },
            "0.00",
            False,
        ),
    ],
)
def test_scoring_examples_from_spec(request_data, changes, expected, gross):
    criteria = example("checklist.initial")["criteria"]
    answers = {
        "contact": True,
        "needs": True,
        "identification": True,
        "consultation": 20,
        "empathy": True,
        "management": 10,
        "closing": True,
        "gross_errors": False,
    }
    answers.update(changes)
    request_data["criteria"] = criteria
    request_data["criterion_results"] = [
        criterion_result(criterion_id, answer) for criterion_id, answer in answers.items()
    ]
    assessment = build_assessment(**request_data)
    assert str(assessment["total"]) == expected
    assert assessment["gross_error"] is gross
    assert assessment["no_applicable_reason"] is None

    rows = {row["criterion_id"]: row for row in assessment["criteria"]}
    if answers["management"] == 7:
        assert rows["management"]["base_points"] == Decimal("10.5")
    if answers["identification"] is None:
        assert rows["identification"]["weight"] == 20
        assert rows["identification"]["effective_weight"] == 0
        assert rows["contact"]["effective_weight"] == Decimal("12.5")
    assert rows["gross_errors"]["effective_points"] == 0


def test_zero_applicable_weight(request_data):
    request_data["criteria"] = request_data["criteria"][2:]
    request_data["criterion_results"] = request_data["criterion_results"][2:]
    assessment = build_assessment(**request_data)
    assert assessment["total"] is None
    assert assessment["no_applicable_reason"] == "нет применимых критериев с весом"
    assert all(section["total"] is None for section in assessment["sections"])
    assert all(
        row["effective_points"] == row["effective_weight"] == 0 for row in assessment["criteria"]
    )


def test_custom_fixture_matches_assessment_contract(request_data):
    expected = example("evaluation.completed")["original"]
    assert build_assessment(**request_data) == expected
    contract = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    Draft202012Validator(
        {"$ref": "#/components/schemas/Assessment", "components": contract["components"]}
    ).validate(build_assessment(**request_data))


def test_custom_flags_are_preserved_without_becoming_gross_error(request_data):
    request_data["criterion_results"][-1]["answer"] = True
    assessment = build_assessment(**request_data)
    assert assessment["criteria"][-1]["answer"] is True
    assert assessment["criteria"][-1]["base_points"] == 0
    assert assessment["gross_error"] is None
    assert assessment["total"] == 70


@pytest.mark.parametrize(("denominator_weight", "expected"), [(99.97, "0.01"), (100, "0.00")])
def test_half_up_on_repeating_fraction_and_unnamed_section(
    request_data, denominator_weight, expected
):
    binary, scale = request_data["criteria"][:2]
    binary.update(weight=denominator_weight, section=None)
    scale.update(
        weight=0.03,
        section=None,
        scale=[{"value": 1, "description": "Partial"}, {"value": 6, "description": "Full"}],
    )
    request_data["criteria"] = [binary, scale]
    request_data["criterion_results"] = [
        criterion_result(scale["id"], 1),
        criterion_result(binary["id"], False),
    ]
    with localcontext(prec=2):
        assessment = build_assessment(**request_data)
    assert str(assessment["total"]) == expected
    assert assessment["sections"] == [
        {
            "name": None,
            "base_points": Decimal("0.005"),
            "applicable_weight": Decimal(str(denominator_weight)) + Decimal("0.03"),
            "total": Decimal(expected),
        }
    ]
    assert assessment["criteria"][0]["criterion_id"] == binary["id"]


@pytest.mark.parametrize(
    ("index", "applicability", "answer", "code"),
    [
        (0, "not_applicable", None, "NA_NOT_ALLOWED"),
        (0, "applicable", 0, "INVALID_CRITERION_ANSWER"),
        (0, "applicable", 1, "INVALID_CRITERION_ANSWER"),
        (0, "applicable", None, "INVALID_CRITERION_ANSWER"),
        (1, "applicable", True, "INVALID_CRITERION_ANSWER"),
        (1, "applicable", False, "INVALID_CRITERION_ANSWER"),
        (1, "applicable", None, "INVALID_CRITERION_ANSWER"),
        (1, "applicable", "5", "INVALID_ASSESSMENT_INPUT"),
        (1, "applicable", float("inf"), "INVALID_ASSESSMENT_INPUT"),
        (1, "applicable", 6, "INVALID_SCALE_STEP"),
        (2, "not_applicable", False, "INVALID_CRITERION_ANSWER"),
        (2, "not_applicable", 0, "INVALID_CRITERION_ANSWER"),
        (3, "applicable", 0, "INVALID_CRITERION_ANSWER"),
    ],
)
def test_invalid_answers(request_data, index, applicability, answer, code):
    request_data["criterion_results"][index].update(applicability=applicability, answer=answer)
    with pytest.raises(EvaluationValidationError, match=f"^{code}$"):
        build_assessment(**request_data)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("missing", "CRITERION_COVERAGE_MISMATCH"),
        ("unknown", "CRITERION_COVERAGE_MISMATCH"),
        ("duplicate", "DUPLICATE_CRITERION_RESULT"),
    ],
)
def test_requires_exact_criterion_coverage(request_data, change, code):
    rows = request_data["criterion_results"]
    if change == "missing":
        rows.pop()
    elif change == "unknown":
        rows[-1]["criterion_id"] = "unrelated"
    else:
        rows[-1] = rows[0].copy()
    with pytest.raises(EvaluationValidationError, match=f"^{code}$"):
        build_assessment(**request_data)


@pytest.mark.parametrize(
    ("evidence", "code"),
    [
        ([{"kind": "message", "message_id": "m9999"}], "UNKNOWN_EVIDENCE_MESSAGE"),
        (
            [{"kind": "range", "start_message_id": "m0002", "end_message_id": "m9999"}],
            "UNKNOWN_EVIDENCE_MESSAGE",
        ),
        (
            [{"kind": "range", "start_message_id": "m0004", "end_message_id": "m0001"}],
            "REVERSED_EVIDENCE_RANGE",
        ),
        ([{"kind": "message", "message_id": "m0001"}], "MISSING_OPERATOR_EVIDENCE"),
        (
            [{"kind": "message", "message_id": "m0002", "quote": "invented"}],
            "INVALID_ASSESSMENT_INPUT",
        ),
        ([], "INVALID_ASSESSMENT_INPUT"),
    ],
)
def test_invalid_evidence(request_data, evidence, code):
    request_data["criterion_results"][0]["evidence"] = evidence
    with pytest.raises(EvaluationValidationError, match=f"^{code}$"):
        build_assessment(**request_data)


def test_mismatched_snapshot_rejected_even_when_message_ids_match(request_data):
    request_data["transcript_snapshot"]["id"] = "00000000-0000-4000-8000-000000000099"
    with pytest.raises(EvaluationValidationError, match="^TRANSCRIPT_VERSION_MISMATCH$"):
        build_assessment(**request_data)


def test_range_order_uses_positions_and_accepts_single_message(request_data):
    snapshot = request_data["transcript_snapshot"]
    snapshot["messages"].reverse()
    request_data["criterion_results"][0]["evidence"] = [
        {"kind": "range", "start_message_id": "m0002", "end_message_id": "m0002"}
    ]
    assert build_assessment(**request_data)["total"] == 70


def test_safe_logging_at_assessment_boundary(request_data, caplog):
    marker = "SYNTHETIC_PRIVATE_MARKER"
    request_data["transcript_snapshot"]["messages"][0]["text"] = marker
    request_data["criterion_results"][0]["explanation"] = marker
    caplog.set_level(logging.INFO, logger="cqa.evaluation")
    build_assessment(**request_data)
    request_data["criterion_results"][0]["evidence"][0]["quote"] = marker
    with pytest.raises(EvaluationValidationError, match="^INVALID_ASSESSMENT_INPUT$"):
        build_assessment(**request_data)
    assert caplog.record_tuples == [
        ("cqa.evaluation", logging.INFO, "event=assessment_calculated status=completed criteria=4"),
        (
            "cqa.evaluation",
            logging.WARNING,
            "event=assessment_rejected error=INVALID_ASSESSMENT_INPUT",
        ),
    ]
    assert all(record.exc_info is None for record in caplog.records)
