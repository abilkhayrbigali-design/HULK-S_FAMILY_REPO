import logging


def log_internal_error(code, exc):
    # Reuse the integrator's bounded diagnostics when A-01 is available.
    try:
        from app.diagnostics import log_internal_error as diagnose
    except ModuleNotFoundError as missing:
        if missing.name != "app.diagnostics":
            raise
        logging.getLogger("cqa.errors").error("error=%s", code)
    else:
        diagnose(code, exc)


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def require(value):
    if value is None:
        raise ServiceError(404, "NOT_FOUND", "Ресурс не найден")
    return value
