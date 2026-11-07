import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.schemas.data import ImportRequest
from app.schemas.urls import valid_http_url
from app.services.errors import ServiceError

logger = logging.getLogger("cqa.import")


def dataset_url(configured_url, group_id):
    if not valid_http_url(configured_url, https_only=True, allow_fragment=False):
        raise ServiceError(503, "DATASET_UNAVAILABLE", "Источник данных не настроен")
    parts = urlsplit(configured_url)
    # Preserve the raw encoding of signed query parameters; replace only group_id.
    query = [
        part
        for part in parts.query.split("&")
        if part and parse_qsl(part, keep_blank_values=True)[0][0] != "group_id"
    ]
    query.append(urlencode({"group_id": group_id}))
    return urlunsplit(parts._replace(query="&".join(query)))


def fetch_records(configured_url, group_id, transport=None):
    logger.info("event=dataset_fetch_started")
    try:
        url = dataset_url(configured_url, group_id)
        # Disable httpx/httpcore request loggers at application integration: URLs are signed.
        with httpx.Client(timeout=30, follow_redirects=False, transport=transport) as client:
            response = client.get(url)
            response.raise_for_status()
            body = response.json()
        data = body["data"]
        if body.get("isSuccess") is not True or body.get("error") not in (
            None,
            "",
            False,
        ):
            raise ValueError("upstream failure")
        payload = ImportRequest.model_validate({"records": data["records"]})
        if (
            data["dataset_group_id"] != group_id
            or type(data["total_records"]) is not int
            or data["total_records"] != len(payload.records)
        ):
            raise ValueError("invalid dataset metadata")
        return payload
    except ServiceError:
        logger.warning("event=dataset_fetch_rejected error=DATASET_UNAVAILABLE")
        raise
    except httpx.InvalidURL:
        logger.warning("event=dataset_fetch_rejected error=DATASET_UNAVAILABLE")
        raise ServiceError(503, "DATASET_UNAVAILABLE", "Источник данных не настроен") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.error("event=dataset_fetch_failed error=DATASET_ERROR")
        raise ServiceError(
            502, "DATASET_ERROR", "Не удалось получить корректный набор диалогов"
        ) from None
