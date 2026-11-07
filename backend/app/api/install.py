"""Integration entry point owned by Chingiz; main/config stay with the integrator."""

import logging

from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.routes.data import router
from app.diagnostics import log_internal_error
from app.services.errors import ServiceError


def install_data_api(app, session_factory, *, dataset_url="", error_response=None):
    if error_response is None:
        # Import when wiring the app: main may import this installer before defining helpers.
        from app.main import error_response

    respond = error_response
    app.state.session_factory = session_factory
    app.state.dataset_url = dataset_url
    # These clients otherwise log complete signed URLs at INFO/DEBUG, even on success.
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return respond(exc.status, exc.code, exc.message, request=request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never return Pydantic input/ctx/message: input may contain signed audio URLs.
        return respond(
            422,
            "VALIDATION_ERROR",
            "Некорректные входные данные",
            [
                {
                    # Unknown field names are untrusted input, too.
                    "location": list(e["loc"][:-1] if e["type"] == "extra_forbidden" else e["loc"]),
                    "type": e["type"],
                }
                for e in exc.errors()
            ],
            request=request,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        logging.getLogger("cqa.data").warning("event=data_write_conflict error=STATE_CONFLICT")
        return respond(409, "STATE_CONFLICT", "Конфликт сохранения данных", request=request)

    @app.exception_handler(OperationalError)
    async def database_error(request, exc):
        log_internal_error("DATABASE_UNAVAILABLE", exc)
        return respond(
            503,
            "DATABASE_UNAVAILABLE",
            "База данных временно недоступна",
            request=request,
        )
