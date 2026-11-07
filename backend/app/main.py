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
from app.diagnostics import log_internal_error

logger = logging.getLogger("cqa.requests")
ERROR_CODE_STATE_KEY = "_cqa_error_code"


class Health(BaseModel):
    status: Literal["ok"] = "ok"


def error_response(
    status: int,
    code: str,
    message: str,
    details: list | None = None,
    *,
    headers: dict[str, str] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    """Build a public error; pass request to include its fixed code in the request log."""
    if request is not None:
        request.scope.setdefault("state", {})[ERROR_CODE_STATE_KEY] = code
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
        headers=headers,
    )


class SafeRequestLogMiddleware:
    """Log one safe summary for every HTTP request, including CORS preflight."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500

        async def track_response(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        finally:
            error_code = scope.get("state", {}).get(ERROR_CODE_STATE_KEY)
            if status >= 400 and error_code is None:
                error_code = f"HTTP_{status}"
            if status >= 500 or error_code == "INTERNAL_ERROR":
                level = logging.ERROR
            else:
                level = logging.WARNING if status >= 400 else logging.INFO
            logger.log(
                level,
                "request status=%d duration_ms=%d error=%s",
                status,
                int((perf_counter() - started) * 1000),
                error_code or "none",
            )


class SafeErrorMiddleware:
    """Cover the full response lifecycle without exposing upstream exception details."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except Exception as exc:
            scope.setdefault("state", {})[ERROR_CODE_STATE_KEY] = "INTERNAL_ERROR"
            log_internal_error("INTERNAL_ERROR", exc)
            if response_started:
                # A second response would violate HTTP; let the server close an incomplete stream.
                raise RuntimeError("Response processing failed.") from None
            response = error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
            await response(scope, receive, track_response)


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
    logging.getLogger("cqa").setLevel(configured.log_level)
    # Network libraries must not emit signed upstream URLs or request payloads at DEBUG/INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    application.add_middleware(SafeErrorMiddleware)

    @application.exception_handler(ConfigurationUnavailable)
    async def configuration_error(request: Request, exc: ConfigurationUnavailable):
        return error_response(503, exc.code, exc.message, request=request)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's input/msg/ctx may include private text or secrets. Keep only location and code.
        details = [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()]
        return error_response(
            422, "VALIDATION_ERROR", "Request validation failed.", details, request=request
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
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
        return error_response(exc.status_code, code, message, headers=headers, request=request)

    @application.get("/health", response_model=Health)
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            log_internal_error("DATABASE_UNAVAILABLE", exc)
            return error_response(
                503, "DATABASE_UNAVAILABLE", "Database is unavailable.", request=request
            )
        return Health()

    # CORS wraps safe errors; the request log also observes preflight handled by CORS itself.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.add_middleware(SafeRequestLogMiddleware)
    return application


app = create_app()
