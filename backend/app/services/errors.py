class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def require(value):
    if value is None:
        raise ServiceError(404, "NOT_FOUND", "Ресурс не найден")
    return value
