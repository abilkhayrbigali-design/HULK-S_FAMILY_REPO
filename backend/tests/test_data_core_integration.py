"""Integration with A-01's shared diagnostics and request middleware."""

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app import main as core
from app.api.install import install_data_api
from app.db.migrate import migrate
from app.db.session import make_engine, session_factory
from app.services.seed import seed


def test_data_api_with_shared_errors_and_request_middleware(tmp_path, caplog):
    url = f"sqlite:///{tmp_path}/integration.sqlite3"
    migrate(url)
    engine = make_engine(url)
    factory = session_factory(engine)
    with factory.begin() as db:
        seed(db)
    app = core.create_app(core.Settings(_env_file=None, CQA_DATABASE_URL=url))
    install_data_api(app, factory, error_response=core.error_response)
    caplog.set_level("INFO", logger="cqa")
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            caplog.clear()
            response = client.get("/api/v1/checklists/missing")
            assert response.status_code == 404
            requests = [r for r in caplog.records if r.name == "cqa.requests"]
            assert len(requests) == 1
            assert "error=NOT_FOUND" in requests[0].getMessage()
            checklist = client.get("/api/v1/checklists").json()["items"][0]
            version = client.get(
                f"/api/v1/checklist-versions/{checklist['current_version_id']}"
            ).json()

            def fail_commit(connection):
                raise OperationalError(
                    "synthetic-private-sql", {}, Exception("synthetic-private-db")
                )

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
            assert all(r.levelname == "ERROR" for r in requests + errors)
            assert "event=checklist_version_saved" not in caplog.text
            assert "synthetic-private" not in response.text + caplog.text
            assert client.get("/api/v1/checklists").json()["total"] == 1
    finally:
        engine.dispose()
