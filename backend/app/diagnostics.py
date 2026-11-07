"""Safe diagnostics: code locations only, never exception values or source lines."""

import logging
from pathlib import Path

from app.config import PROJECT_ROOT

logger = logging.getLogger("cqa.errors")


def log_internal_error(code: str, exception: BaseException) -> None:
    """Log a fixed application error code and bounded repository traceback locations.

    Do not use str/repr(exception), exc_info, source lines or frame locals here:
    HTTP/SQL exceptions and chained/grouped errors may contain private payloads.
    """
    locations = []
    pending = [exception]
    seen = set()
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        frame = current.__traceback__
        while frame is not None:
            filename = Path(frame.tb_frame.f_code.co_filename)
            if filename.is_relative_to(PROJECT_ROOT):
                relative = filename.relative_to(PROJECT_ROOT)
                if relative.parts[0] in {"backend", "contracts", "tools"} and not any(
                    part.startswith(".") for part in relative.parts
                ):
                    locations.append(f"{relative.as_posix()}:{frame.tb_lineno}")
            frame = frame.tb_next
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:16])
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)

    logger.error(
        "error=%s locations=%s", code, ",".join(dict.fromkeys(locations[-16:])) or "unavailable"
    )
