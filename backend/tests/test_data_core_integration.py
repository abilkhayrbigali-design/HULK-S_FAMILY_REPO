"""Integration with A-01's shared diagnostics and request middleware."""

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app import main as core


def test_data_api_with_shared_errors_and_request_middleware(settings, caplog):
    app = core.create_app(settings)
    caplog.set_level("INFO", logger="cqa")
    with TestClient(app) as client:
        engine = app.state.engine
        assert client.get("/health").status_code == 200
        caplog.clear()
        response = client.get("/api/v1/checklists/00000000-0000-4000-8000-000000000099")
        assert response.status_code == 404
        requests = [r for r in caplog.records if r.name == "cqa.requests"]
        assert len(requests) == 1
        assert "error=NOT_FOUND" in requests[0].getMessage()
        checklist = client.get("/api/v1/checklists").json()["items"][0]
        version = client.get(f"/api/v1/checklist-versions/{checklist['latest_version_id']}").json()

        operation = client.get("/openapi.json").json()["paths"]["/api/v1/checklists"]["post"]
        assert {"201", "409", "422", "503"} <= operation["responses"].keys()

        def fail_commit(connection):
            raise OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-db"))

        caplog.clear()
        event.listen(engine, "commit", fail_commit)
        try:
            response = client.post(
                "/api/v1/checklists",
                json={
                    "name": "Synthetic",
                    "criteria": version["criteria"],
                },
            )
        finally:
            event.remove(engine, "commit", fail_commit)
        assert response.status_code == 503
        requests = [r for r in caplog.records if r.name == "cqa.requests"]
        errors = [r for r in caplog.records if r.name == "cqa.errors"]
        assert len(requests) == len(errors) == 1
        assert "error=DATABASE_UNAVAILABLE" in requests[0].getMessage()
        assert requests[0].levelname == errors[0].levelname == "ERROR"
        assert "event=checklist_version_saved" not in caplog.text
        assert "synthetic-private" not in response.text + caplog.text
        assert client.get("/api/v1/checklists").json()["total"] == 1
