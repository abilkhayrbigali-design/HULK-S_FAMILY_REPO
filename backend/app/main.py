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

from app.config import ConfigurationUnavailable, Settings

logger = logging.getLogger("cqa.requests")


class Health(BaseModel):
    status: Literal["ok"] = "ok"


def error_response(
    status: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
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

    @application.middleware("http")
    async def safe_request_log(request: Request, call_next):
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Do not log an exception/traceback: client exceptions may contain signed URLs.
            response = error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
        logger.info(
            "request status=%d duration_ms=%d",
            response.status_code,
            int((perf_counter() - started) * 1000),
        )
        return response

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
        return error_response(exc.status_code, code, message)

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
