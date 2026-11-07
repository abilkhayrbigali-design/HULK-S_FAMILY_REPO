import logging
import traceback
from unittest.mock import patch

import pytest
from fastapi import BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException

from app.config import PROJECT_ROOT, ConfigurationUnavailable, Settings
from app.main import create_app


def test_health_checks_database_without_external_credentials(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        with client.app.state.engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_file_database_uses_wal_and_persists_across_lifespans(tmp_path):
    database = tmp_path / "nested" / "cqa.sqlite3"
    settings = Settings(_env_file=None, CQA_DATABASE_URL=f"sqlite:///{database}")
    for attempt in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200
            with client.app.state.engine.begin() as connection:
                assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
                if attempt == 0:
                    connection.execute(text("CREATE TABLE bootstrap_probe (value INTEGER)"))
                    connection.execute(text("INSERT INTO bootstrap_probe VALUES (42)"))
                else:
                    assert (
                        connection.execute(text("SELECT value FROM bootstrap_probe")).scalar() == 42
                    )


def test_unavailable_database_is_not_healthy(settings, caplog):
    with TestClient(create_app(settings)) as client:
        with patch.object(
            client.app.state.engine,
            "connect",
            side_effect=OperationalError("synthetic-secret", {}, Exception("synthetic-secret")),
        ):
            with caplog.at_level(logging.INFO, logger="cqa.requests"):
                response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "synthetic-secret" not in response.text + caplog.text


@pytest.mark.parametrize("key", ["", "   "])
def test_empty_api_key_fails_explicitly(key):
    settings = Settings(_env_file=None, OPENAI_API_KEY=key)
    with pytest.raises(ConfigurationUnavailable) as error:
        settings.require_llm()
    assert error.value.code == "AI_NOT_CONFIGURED"


def test_configuration_error_is_http_503(settings):
    app = create_app(settings)

    @app.post("/test/require-ai", dependencies=[Depends(settings.require_llm)])
    def guarded():
        pytest.fail("An unconfigured AI action must never execute")

    with TestClient(app) as client:
        response = client.post("/test/require-ai")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"


def test_unconfigured_dataset_fails_without_network(settings):
    with pytest.raises(ConfigurationUnavailable, match="Dataset API URL is not configured"):
        settings.require_dataset()


def test_secrets_are_redacted_and_relative_database_is_stable(monkeypatch, tmp_path):
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="synthetic-key",
        CQA_DATASET_API_URL="https://example.invalid/api?sig=synthetic-signature",
    )
    assert "synthetic-key" not in repr(settings)
    assert "synthetic-signature" not in repr(settings)
    before = settings.sqlalchemy_url
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).sqlalchemy_url == before
    assert before.database == str(PROJECT_ROOT / "data/private/cqa.sqlite3")


def test_dotenv_loading_and_environment_override(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=synthetic-dotenv\nCQA_LLM_MAX_RETRIES=1\n")
    monkeypatch.setenv("CQA_LLM_MAX_RETRIES", "2")
    settings = Settings(_env_file=dotenv)
    assert settings.openai_api_key.get_secret_value() == "synthetic-dotenv"
    assert settings.llm_max_retries == 2


@pytest.mark.parametrize(
    "values",
    [
        {"CQA_LLM_MAX_RETRIES": -1},
        {"CQA_LLM_TIMEOUT_SECONDS": 0},
        {"CQA_EVALUATION_CONCURRENCY": 0},
        {"CQA_DATABASE_URL": "postgresql://localhost/db"},
        {"CQA_DATABASE_URL": "sqlite://user:synthetic-signature@/tmp/cqa.sqlite3"},
        {"CQA_DATABASE_URL": "sqlite://:1234/tmp/cqa.sqlite3"},
        {"CQA_ALLOWED_ORIGINS": "*"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost:5173/path"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost:bad"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost:70000"},
        {"CQA_ALLOWED_ORIGINS": "https://[synthetic-signature]"},
        {"CQA_ALLOWED_ORIGINS": "http://local\nhost:5173"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost\x00:5173"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost\x1b:5173"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost\x7f:5173"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost:5173?"},
        {"CQA_ALLOWED_ORIGINS": "http://localhost:5173#"},
        {"CQA_DATASET_API_URL": "http://example.invalid?sig=synthetic-signature"},
        {"CQA_DATASET_API_URL": "https://[synthetic-signature]"},
        {"CQA_DATASET_API_URL": "https://example.invalid:bad/api"},
        {"CQA_DATASET_API_URL": "https://example.invalid:70000/api"},
        {"CQA_DATASET_API_URL": "https://example.invalid\n/api"},
        {"CQA_DATASET_API_URL": "\x00https://example.invalid/api"},
        {"CQA_DATASET_API_URL": "https://example.invalid/\x1bapi"},
        {"CQA_DATASET_API_URL": "https://example.invalid/\x7fapi"},
        {"CQA_DATASET_API_URL": "https://example.invalid/api#sig=synthetic-signature"},
        {"CQA_DATASET_API_URL": "https://example.invalid/api#"},
    ],
)
def test_invalid_configuration_is_rejected_without_echoing_secret(values):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **values)
    assert "synthetic-signature" not in str(error.value)


def test_valid_http_settings_preserve_signed_url_and_support_ipv6():
    signed_url = "https://[::1]:8443/api?sig=synthetic%2Bsignature&group_id=DEV"
    settings = Settings(
        _env_file=None,
        CQA_DATASET_API_URL=signed_url,
        CQA_ALLOWED_ORIGINS=" http://localhost:5173, https://[::1]:8443 ",
    )
    assert settings.dataset_api_url.get_secret_value() == signed_url
    assert settings.cors_origins == ["http://localhost:5173", "https://[::1]:8443"]


def test_errors_and_request_logs_never_echo_private_payloads(settings, caplog):
    app = create_app(settings)

    class Input(BaseModel):
        value: int

    @app.post("/test/validate")
    def validate(payload: Input):
        return payload

    @app.get("/test/fail")
    def fail():
        raise RuntimeError("https://example.invalid?sig=synthetic-signature")

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="cqa.requests"):
        invalid = client.post("/test/validate", json={"value": "synthetic-private-transcript"})
        failed = client.get("/test/fail?sig=synthetic-signature")
        missing = client.get("/not-found?sig=synthetic-signature")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"] == [{"loc": ["body", "value"], "type": "int_parsing"}]
    assert failed.status_code == 500
    assert missing.status_code == 404
    combined = invalid.text + failed.text + missing.text + caplog.text
    assert "synthetic-private-transcript" not in combined
    assert "synthetic-signature" not in combined
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("failure_phase", ["background", "stream"])
def test_errors_after_response_start_do_not_expose_private_exceptions(
    settings, caplog, failure_phase
):
    app = create_app(settings)
    private_details = "https://example.invalid?sig=synthetic-signature"

    def fail():
        raise RuntimeError(private_details)

    @app.get("/test/background-failure")
    def background_failure(tasks: BackgroundTasks):
        tasks.add_task(fail)
        return {"status": "accepted"}

    @app.get("/test/stream-failure")
    def stream_failure():
        def body():
            yield b"partial response"
            fail()

        return StreamingResponse(body())

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="cqa.requests"):
        with pytest.raises(RuntimeError) as error:
            client.get(f"/test/{failure_phase}-failure")
    rendered = "".join(traceback.format_exception(error.value)) + caplog.text
    assert "synthetic-signature" not in rendered
    assert "INTERNAL_ERROR" in caplog.text
    records = [record for record in caplog.records if record.name.startswith("cqa.")]
    assert {record.name for record in records} == {"cqa.requests", "cqa.errors"}
    assert all(record.levelno == logging.ERROR for record in records)


def test_method_not_allowed_preserves_allow_header_and_safe_body(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/health", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.headers["content-type"] == "application/json"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.json() == {
        "error": {
            "code": "METHOD_NOT_ALLOWED",
            "message": "Method not allowed.",
            "details": [],
        }
    }


def test_http_error_preserves_retry_after_without_private_headers_or_detail(settings, caplog):
    app = create_app(settings)

    @app.get("/test/rate-limited")
    def rate_limited():
        raise HTTPException(
            status_code=429,
            detail="synthetic-private-transcript",
            headers={
                "rEtRy-AfTeR": "30",
                "Content-Type": "text/plain",
                "Location": "https://example.invalid?sig=synthetic-signature",
                "X-Private-Detail": "synthetic-private-transcript",
            },
        )

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="cqa.requests"):
        response = client.get("/test/rate-limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.headers["content-type"] == "application/json"
    assert "location" not in response.headers
    assert "x-private-detail" not in response.headers
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Request could not be completed.",
        "details": [],
    }
    rendered = response.text + str(response.headers) + caplog.text
    assert "synthetic-signature" not in rendered
    assert "synthetic-private-transcript" not in rendered


def test_cors_only_allows_configured_origins(settings):
    with TestClient(create_app(settings)) as client:
        allowed = client.options(
            "/health",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
        denied = client.options(
            "/health",
            headers={"Origin": "https://example.invalid", "Access-Control-Request-Method": "GET"},
        )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize(
    ("path", "status", "code", "level"),
    [
        ("/unknown", 404, "NOT_FOUND", logging.WARNING),
        ("/health", 405, "METHOD_NOT_ALLOWED", logging.WARNING),
        ("/test/require-ai", 503, "AI_NOT_CONFIGURED", logging.ERROR),
        ("/test/validate", 422, "VALIDATION_ERROR", logging.WARNING),
        ("/test/custom-error", 409, "CONFLICT", logging.WARNING),
    ],
)
def test_handled_errors_log_their_safe_code_and_severity(
    settings, caplog, path, status, code, level
):
    app = create_app(settings)

    class Input(BaseModel):
        value: int

    @app.post("/test/validate")
    def validate(payload: Input):
        return payload

    @app.post("/test/require-ai", dependencies=[Depends(settings.require_llm)])
    def require_ai():
        pytest.fail("An unconfigured AI action must never execute")

    @app.post("/test/custom-error")
    def custom_error():
        raise HTTPException(409, detail="synthetic-private-transcript")

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="cqa.requests"):
        response = client.post(
            path + "?sig=synthetic-signature", json={"value": "synthetic-private-transcript"}
        )
    assert response.status_code == status
    records = [record for record in caplog.records if record.name == "cqa.requests"]
    assert len(records) == 1
    assert records[0].levelno == level
    assert f"status={status}" in records[0].getMessage()
    assert f"error={code}" in records[0].getMessage()
    assert "synthetic-signature" not in caplog.text
    assert "synthetic-private-transcript" not in caplog.text


def test_cors_preflight_errors_are_logged(settings, caplog):
    with (
        TestClient(create_app(settings)) as client,
        caplog.at_level(logging.INFO, logger="cqa.requests"),
    ):
        response = client.options(
            "/health?sig=synthetic-signature",
            headers={
                "Origin": "https://example.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code == 400
    records = [record for record in caplog.records if record.name == "cqa.requests"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "error=HTTP_400" in records[0].getMessage()
    assert "synthetic-signature" not in caplog.text


def test_database_errors_have_safe_diagnostics_even_at_error_log_level(caplog):
    settings = Settings(
        _env_file=None, CQA_DATABASE_URL="sqlite:///:memory:", CQA_LOG_LEVEL="ERROR"
    )
    with TestClient(create_app(settings)) as client, caplog.at_level(logging.ERROR):
        with patch.object(
            client.app.state.engine,
            "connect",
            side_effect=OperationalError(
                "synthetic-private-transcript", {}, Exception("?sig=synthetic-signature")
            ),
        ):
            response = client.get("/health")
    assert response.status_code == 503
    requests = [record for record in caplog.records if record.name == "cqa.requests"]
    errors = [record for record in caplog.records if record.name == "cqa.errors"]
    assert len(requests) == len(errors) == 1
    assert "error=DATABASE_UNAVAILABLE" in requests[0].getMessage()
    assert "error=DATABASE_UNAVAILABLE" in errors[0].getMessage()
    assert "backend/app/main.py:" in errors[0].getMessage()
    assert errors[0].exc_info is None
    assert errors[0].stack_info is None
    assert "synthetic-private-transcript" not in caplog.text
    assert "synthetic-signature" not in caplog.text


@pytest.mark.parametrize("grouped", [False, True])
def test_internal_errors_log_code_locations_without_exception_text(settings, caplog, grouped):
    app = create_app(settings)

    @app.get("/test/fail-with-diagnostics")
    def fail():
        try:
            raise ValueError("synthetic-private-transcript")
        except ValueError as error:
            if grouped:
                raise ExceptionGroup("synthetic-secret-group", [error]) from None
            raise RuntimeError("https://example.invalid?sig=synthetic-signature") from error

    with TestClient(app) as client, caplog.at_level(logging.ERROR):
        response = client.get(
            "/test/fail-with-diagnostics?sig=synthetic-signature",
            headers={"Origin": "http://localhost:5173"},
        )
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    requests = [record for record in caplog.records if record.name == "cqa.requests"]
    errors = [record for record in caplog.records if record.name == "cqa.errors"]
    assert len(requests) == len(errors) == 1
    assert "error=INTERNAL_ERROR" in requests[0].getMessage()
    assert "backend/tests/test_bootstrap.py:" in errors[0].getMessage()
    assert errors[0].exc_info is None
    assert errors[0].stack_info is None
    for secret in ("synthetic-signature", "synthetic-private-transcript", "synthetic-secret-group"):
        assert secret not in caplog.text
