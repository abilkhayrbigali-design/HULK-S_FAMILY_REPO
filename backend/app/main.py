"""Application factory and resource lifecycle; business routes are added by C-01/C-02."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import ConfigurationUnavailable, Settings

logger = logging.getLogger("cqa.requests")


class Health(BaseModel):
    status: Literal["ok"] = "ok"


def error_response(
    status: int,
    code: str,
    message: str,
    details: list | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
        headers=headers,
    )


class SafeRequestLogMiddleware:
    """Cover the full response lifecycle without exposing upstream exception details."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        response_started = False
        status = 500
        error_code = None

        async def track_response(message: Message) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except Exception:
            error_code = "INTERNAL_ERROR"
            if response_started:
                # A second response would violate HTTP; let the server close an incomplete stream.
                raise RuntimeError("Response processing failed.") from None
            response = error_response(500, error_code, "An internal error occurred.")
            await response(scope, receive, track_response)
        finally:
            logger.info(
                "request status=%d duration_ms=%d error=%s",
                status,
                int((perf_counter() - started) * 1000),
                error_code or "none",
            )


def build_engine(settings: Settings) -> Engine:
    """C-01 reuses app.state.engine; no tables or seed are created here."""
    url = settings.sqlalchemy_url
    if url.database != ":memory:":
        Path(url.database or "").parent.mkdir(parents=True, exist_ok=True)
    options = {"poolclass": StaticPool} if url.database == ":memory:" else {}
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 5}, **options)

    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, _record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.engine = build_engine(configured)
        try:
            yield
        finally:
            application.state.engine.dispose()

    application = FastAPI(title="CQA API", version="0.1.0", lifespan=lifespan)
    application.state.settings = configured
    logging.basicConfig(level=configured.log_level, format="%(levelname)s %(name)s %(message)s")
    logger.setLevel(configured.log_level)
    # Network libraries must not emit signed upstream URLs or request payloads at DEBUG/INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    application.add_middleware(SafeRequestLogMiddleware)

    @application.exception_handler(ConfigurationUnavailable)
    async def configuration_error(_request: Request, exc: ConfigurationUnavailable):
        return error_response(503, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # Pydantic's input/msg/ctx may include private text or secrets. Keep only location and code.
        details = [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)

    @application.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        messages = {
            404: ("NOT_FOUND", "Resource not found."),
            405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
            409: ("CONFLICT", "Resource version or state conflict."),
        }
        code, message = messages.get(
            exc.status_code, ("HTTP_ERROR", "Request could not be completed.")
        )
        # Preserve protocol metadata without forwarding private URLs or custom error details.
        headers = {
            name: value
            for name, value in (exc.headers or {}).items()
            if name.lower() in {"allow", "retry-after"}
        }
        return error_response(exc.status_code, code, message, headers=headers)

    @application.get("/health", response_model=Health)
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return error_response(503, "DATABASE_UNAVAILABLE", "Database is unavailable.")
        return Health()

    # Keep CORS outermost so handled failures also carry the permitted origin.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    return application


app = create_app()
