"""Integration entry point owned by Chingiz; main/config stay with the integrator."""

import logging

from app.api.routes.data import router
from app.services.errors import ServiceError
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError


def install_data_api(app, session_factory, *, dataset_url=""):
    app.state.session_factory = session_factory
    app.state.dataset_url = dataset_url
    # These clients otherwise log complete signed URLs at INFO/DEBUG, even on success.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []}
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never return Pydantic input/ctx/message: input may contain signed audio URLs.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Некорректные входные данные",
                    "details": [
                        {"location": list(e["loc"]), "type": e["type"]}
                        for e in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "STATE_CONFLICT",
                    "message": "Конфликт сохранения данных",
                    "details": [],
                }
            },
        )

    @app.exception_handler(OperationalError)
    async def database_error(request, exc):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "База данных временно недоступна",
                    "details": [],
                }
            },
        )
