import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

from app.config import Settings
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


def test_runtime_paths_and_response_schemas_match_target_contract(settings):
    with TestClient(create_app(settings)) as client:
        validator("Health").validate(client.get("/health").json())
        validator("Error").validate(client.get("/unknown").json())
        runtime = client.get("/openapi.json").json()
    validate(runtime)
    assert set(runtime["paths"]) == set(CONTRACT["paths"])
    for path, operations in CONTRACT["paths"].items():
        assert runtime["paths"][path].keys() == operations.keys()
        for method, target in operations.items():
            operation = runtime["paths"][path][method]
            assert operation["operationId"] == target["operationId"]
            for status, response in target["responses"].items():
                target_ref = response["content"]["application/json"]["schema"]["$ref"]
                actual_ref = operation["responses"][status]["content"]["application/json"][
                    "schema"
                ]["$ref"]
                assert actual_ref == target_ref.replace("/schemas/", "/schemas/Wire")
    request_body = runtime["paths"]["/api/v1/checklists"]["post"]["requestBody"]
    assert (
        request_body["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ChecklistCreate"
    )
    assert set(runtime["components"]["schemas"]["ChecklistCreate"]["required"]) == {
        "name",
        "criteria",
    }


def test_production_factory_wire_smoke_and_persistence(tmp_path, caplog):
    path = tmp_path / "integration.sqlite3"
    settings = Settings(_env_file=None, CQA_DATABASE_URL=f"sqlite:///{path}")
    marker = "SYNTHETIC_PRIVATE_INTEGRATION"
    payload = fixture("import.request.json")
    payload["records"][0]["transcript"][0]["text"] = marker
    origin = {"Origin": "http://localhost:5173"}

    def verify(response, method, route, status):
        assert response.status_code == status
        declared = runtime["paths"][route][method]["responses"][str(status)]
        schema = declared["content"]["application/json"]["schema"]
        Draft202012Validator(
            {**schema, "components": runtime["components"]}, format_checker=FormatChecker()
        ).validate(response.json())
        return response.json()

    with TestClient(create_app(settings)) as client:
        runtime = client.get("/openapi.json").json()
        imported = verify(
            client.post("/api/v1/conversations/import", json=payload),
            "post",
            "/api/v1/conversations/import",
            200,
        )
        conversation_id = imported["conversation_ids"][0]
        listed = verify(client.get("/api/v1/conversations"), "get", "/api/v1/conversations", 200)
        assert listed["total"] == len(payload["records"])
        detail_url = f"/api/v1/conversations/{conversation_id}"
        detail = verify(client.get(detail_url), "get", "/api/v1/conversations/{id}", 200)
        assert detail["messages"][0]["text"] == marker
        assert detail["latest_evaluation"] is None

        seed_list = verify(client.get("/api/v1/checklists"), "get", "/api/v1/checklists", 200)
        assert len(seed_list["items"]) == 1
        original = verify(
            client.post("/api/v1/checklists", json=fixture("checklist.initial.json")),
            "post",
            "/api/v1/checklists",
            201,
        )
        url = f"/api/v1/checklists/{original['checklist_id']}"
        criteria = deepcopy(original["criteria"])
        criteria[0]["weight"] += 1
        new_payload = {"criteria": criteria, "expected_version_number": 1}
        newer = verify(
            client.post(f"{url}/versions", json=new_payload),
            "post",
            "/api/v1/checklists/{id}/versions",
            201,
        )
        verify(
            client.post(f"{url}/versions", json=new_payload),
            "post",
            "/api/v1/checklists/{id}/versions",
            409,
        )
        error_response = client.post(
            "/api/v1/evaluations",
            json={"conversation_id": conversation_id, "checklist_version_id": newer["id"]},
            headers=origin,
        )
        error = verify(error_response, "post", "/api/v1/evaluations", 503)
        assert error["error"]["code"] == "AI_NOT_CONFIGURED"
        assert error_response.headers["access-control-allow-origin"] == origin["Origin"]
        assert (
            verify(client.get("/api/v1/evaluations"), "get", "/api/v1/evaluations", 200)["items"]
            == []
        )
        assert verify(client.get("/api/v1/appeals"), "get", "/api/v1/appeals", 200)["items"] == []

    with TestClient(create_app(settings)) as client:
        assert client.get(detail_url).json() == detail
        checklist = verify(client.get(url), "get", "/api/v1/checklists/{id}", 200)
        assert checklist["current_version"] == newer
        assert checklist["versions"] == [original, newer]
        version = verify(
            client.get(f"/api/v1/checklist-versions/{original['id']}"),
            "get",
            "/api/v1/checklist-versions/{id}",
            200,
        )
        assert version == original
        listing = verify(client.get("/api/v1/checklists"), "get", "/api/v1/checklists", 200)
        assert listing["total"] == 2
        summary = next(item for item in listing["items"] if item["id"] == original["checklist_id"])
        assert summary["latest_version_id"] == newer["id"]
        assert summary["latest_version_number"] == 2
    assert marker not in caplog.text
    assert "error=AI_NOT_CONFIGURED" in caplog.text
