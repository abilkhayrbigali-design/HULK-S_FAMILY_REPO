import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data/fixtures/synthetic"
CONTRACT = json.loads((ROOT / "contracts/openapi.json").read_text())
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def validator(name):
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{name}",
        "components": CONTRACT["components"],
    }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_contract_schemas_are_valid():
    validate(CONTRACT)
    for schema in CONTRACT["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)
    standalone = json.loads((ROOT / "contracts/provider-result.schema.json").read_text())
    Draft202012Validator.check_schema(standalone)
    Draft202012Validator(standalone).validate(fixture("provider.response.json"))


@pytest.mark.parametrize("example", MANIFEST["fixtures"], ids=lambda entry: entry["file"])
def test_synthetic_wire_examples_match_contract(example):
    validator(example["schema"]).validate(fixture(example["file"]))


def test_all_json_examples_are_registered_and_explicitly_synthetic():
    assert MANIFEST["synthetic"] is True
    assert set(path.name for path in FIXTURES.glob("*.json")) == {
        "manifest.json",
        *(item["file"] for item in MANIFEST["fixtures"]),
    }
    assert {item["language"] for item in fixture("import.request.json")["records"]} == {
        "RU",
        "KZ",
        "EN",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"weight": True},
        {"weight": -1},
        {"type": "flag", "weight": 10},
        {"type": "binary", "scale": [{"value": 0, "description": "Synthetic"}]},
        {"allow_na": True, "na_rule": None},
        {"allow_na": False, "na_rule": "Unpermitted"},
        {
            "type": "scale",
            "scale": [
                {"value": 0, "description": "Synthetic"},
                {"value": 0, "description": "Synthetic"},
            ],
        },
    ],
)
def test_invalid_criterion_structures_are_rejected(mutation):
    criterion = deepcopy(fixture("checklist.initial.json")["criteria"][0])
    criterion.update(mutation)
    assert not validator("Criterion").is_valid(criterion)


def test_failed_evaluation_cannot_contain_successful_assessment():
    failed = fixture("evaluation.failed.json")
    failed["original"] = fixture("evaluation.completed.json")["original"]
    assert not validator("Evaluation").is_valid(failed)


def test_na_cannot_contain_false_instead_of_null():
    criterion = fixture("provider.response.json")["criterion_results"][2]
    criterion["answer"] = False
    assert not validator("ProviderCriterionResult").is_valid(criterion)


def test_synthetic_history_and_message_references_are_consistent():
    before = fixture("evaluation.completed.json")
    after = fixture("decision.response.json")["evaluation"]
    assert after["original"] == before["original"]
    assert after["revision"] == before["revision"] + 1
    assert before["final"]["total"] == 70
    assert after["final"]["total"] == 100
    message_ids = {message["id"] for message in fixture("transcript.snapshot.json")["messages"]}
    assert {item["criterion_id"] for item in before["original"]["criteria"]} == {
        criterion["id"] for criterion in fixture("checklist-version.response.json")["criteria"]
    }
    for criterion in before["original"]["criteria"]:
        for evidence in criterion["evidence"]:
            ids = [value for key, value in evidence.items() if key.endswith("message_id")]
            assert set(ids) <= message_ids


def test_runtime_health_and_error_responses_match_target_contract(settings):
    with TestClient(create_app(settings)) as client:
        validator("Health").validate(client.get("/health").json())
        validator("Error").validate(client.get("/unknown").json())
        runtime = client.get("/openapi.json").json()
    assert set(runtime["paths"]) == {"/health"}
    assert "post" in CONTRACT["paths"]["/api/v1/evaluations"]
