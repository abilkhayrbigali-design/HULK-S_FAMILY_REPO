"""Local Ollama adapter for the existing evaluation worker."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Never
from urllib.parse import urlsplit

import httpx
from pydantic import Field, StringConstraints, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.evaluation.evidence import EvaluationValidationError
from app.evaluation.provider_protocol import (
    INSTRUCTIONS,
    MODEL_SCHEMA,
    Attempts,
    ProviderResult,
    snapshot_payload,
    validated_output,
)
from app.schemas.urls import valid_http_url
from app.services.evaluation_worker import ProviderFailure

logger = logging.getLogger("cqa.evaluation")
PROMPT_VERSION = "cqa-ollama-operator-v1"


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    base_url: str = Field(
        default="http://127.0.0.1:11434", validation_alias="OLLAMA_BASE_URL", repr=False
    )
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        default="qwen3.5:9b-q4_K_M", validation_alias="OLLAMA_MODEL"
    )
    timeout_seconds: float = Field(
        default=120, gt=0, le=600, validation_alias="OLLAMA_TIMEOUT_SECONDS"
    )
    max_retries: int = Field(default=1, ge=0, le=3, validation_alias="OLLAMA_MAX_RETRIES")

    @field_validator("base_url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        if (
            not valid_http_url(value, allow_fragment=False)
            or "?" in value
            or urlsplit(value).path not in ("", "/")
        ):
            raise ValueError("Ollama requires an HTTP(S) origin")
        return value


def _count(body, key):
    value = body.get(key)
    return value if type(value) is int and value >= 0 else None


def _usage(body):
    prompt = _count(body, "prompt_eval_count")
    generated = _count(body, "eval_count")
    return {
        "input_tokens": prompt,
        "output_tokens": generated,
        "total_tokens": prompt + generated
        if prompt is not None and generated is not None
        else None,
        "cached_input_tokens": _count(body, "prompt_eval_cached_count"),
    }


def _body(response):
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class OllamaProvider:
    def __init__(self, client: httpx.AsyncClient, settings: OllamaSettings):
        self._client = client
        self._settings = settings

    async def evaluate(
        self, transcript_snapshot, checklist_snapshot, prompt_version
    ) -> ProviderResult:
        attempts = Attempts("ollama", self._settings.model)
        if prompt_version != PROMPT_VERSION:
            self._fail("OLLAMA_UNKNOWN_PROMPT_VERSION", attempts)
        try:
            content = snapshot_payload(transcript_snapshot, checklist_snapshot)
        except (KeyError, TypeError, ValueError):
            self._fail("OLLAMA_INVALID_SNAPSHOT", attempts)
        request = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": content},
            ],
            "format": MODEL_SCHEMA,
            "stream": False,
            "think": False,
            "truncate": False,
            "shift": False,
            "options": {"num_ctx": 16384},
        }
        logger.info("event=provider_started provider=ollama")
        for attempt in range(1, self._settings.max_retries + 2):
            try:
                async with asyncio.timeout(self._settings.timeout_seconds):
                    response = await self._client.post("/api/chat", json=request)
            except (httpx.TimeoutException, TimeoutError):
                attempts.record()
                code = "OLLAMA_TIMEOUT"
            except httpx.TransportError:
                attempts.record()
                code = "OLLAMA_UNAVAILABLE"
            else:
                body = _body(response)
                attempts.record(_usage(body), body.get("model"))
                if response.status_code in (408, 429) or 500 <= response.status_code < 600:
                    code = "OLLAMA_UPSTREAM_ERROR"
                elif response.status_code != 200:
                    self._fail("OLLAMA_REQUEST_REJECTED", attempts)
                else:
                    return self._result(body, transcript_snapshot, checklist_snapshot, attempts)
            if attempt > self._settings.max_retries:
                break
            logger.warning(
                "event=provider_retry provider=ollama attempt=%d error=%s", attempt, code
            )
            await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        self._fail(code, attempts)

    def _result(self, body, transcript, checklist, attempts):
        if body.get("done_reason") == "length" or body.get("done") is False:
            self._fail("OLLAMA_TRUNCATED", attempts)
        message = body.get("message")
        if (
            "error" in body
            or body.get("done") is not True
            or body.get("done_reason") != "stop"
            or not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)
        ):
            self._fail("OLLAMA_INVALID_RESPONSE", attempts)
        try:
            rows, reasons = validated_output(message["content"], transcript, checklist)
        except (EvaluationValidationError, ValidationError, OverflowError):
            self._fail("OLLAMA_INVALID_RESULT", attempts)
        metadata = attempts.metadata()
        logger.info(
            "event=provider_completed provider=ollama attempts=%d duration_ms=%d",
            attempts.count,
            metadata.duration_ms,
        )
        return ProviderResult(
            **metadata.model_dump(), criterion_results=rows, review_reasons=reasons
        )

    @staticmethod
    def _fail(code: str, attempts: Attempts) -> Never:
        metadata = attempts.metadata()
        logger.error(
            "event=provider_failed provider=ollama attempts=%d duration_ms=%d error=%s",
            attempts.count,
            metadata.duration_ms,
            code,
        )
        raise ProviderFailure(metadata if attempts.count else None) from None


@asynccontextmanager
async def ollama_provider(
    settings: OllamaSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[OllamaProvider]:
    """Keep the HTTP client open until the consuming worker stops."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        yield OllamaProvider(client, settings)
