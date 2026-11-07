"""Responses API adapter. The worker owns persistence and scoring."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from os import environ
from typing import Never

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
)
from openai.types.responses import Response
from pydantic import ValidationError

from app.config import ConfigurationUnavailable, Settings
from app.evaluation.evidence import EvaluationValidationError
from app.evaluation.provider_protocol import (
    INSTRUCTIONS,
    MODEL_SCHEMA,
    Attempts,
    ProviderResult,
    snapshot_payload,
    validated_output,
)
from app.services.evaluation_worker import ProviderFailure

logger = logging.getLogger("cqa.evaluation")
PROMPT_VERSION = "cqa-operator-v1"
_REPAIR = (
    "\nA previous response failed validation. Recheck criterion coverage, answer types, "
    "allowed scale steps and N/A, and the exact IDs and order of evidence."
)
_MAX_BACKOFF = 8.0


_FORMAT = {
    "type": "json_schema",
    "name": "cqa_criterion_results",
    "strict": True,
    "schema": MODEL_SCHEMA,
}


def _token_counts(response):
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "cached_input_tokens": getattr(
            getattr(usage, "input_tokens_details", None), "cached_tokens", None
        ),
        "reasoning_output_tokens": getattr(
            getattr(usage, "output_tokens_details", None), "reasoning_tokens", None
        ),
    }


def _retry_after(error):
    """Honor a valid server delay, including the SDK's millisecond header."""
    headers = error.response.headers
    for name, unit in (("retry-after-ms", 1000), ("retry-after", 1)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            seconds = float(raw) / unit
        except ValueError:
            if name != "retry-after":
                continue
            try:
                date = parsedate_to_datetime(raw)
                seconds = (date - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                continue
        if isfinite(seconds) and seconds >= 0:
            return seconds
    return 0.0


def _refused(response: Response) -> bool:
    return any(
        part.type == "refusal"
        for item in response.output
        if item.type == "message"
        for part in item.content
    )


class OpenAIProvider:
    def __init__(self, client: AsyncOpenAI, settings: Settings):
        self._client = client
        self._model = settings.openai_model.strip()
        self._timeout = settings.llm_timeout_seconds
        self._max_attempts = 1 + settings.llm_max_retries

    async def evaluate(
        self, transcript_snapshot, checklist_snapshot, prompt_version
    ) -> ProviderResult:
        attempts = Attempts("openai", self._model)
        if prompt_version != PROMPT_VERSION:
            self._fail("AI_UNKNOWN_PROMPT_VERSION", attempts)
        try:
            content = snapshot_payload(transcript_snapshot, checklist_snapshot)
        except (KeyError, TypeError, ValueError):
            self._fail("AI_INVALID_SNAPSHOT", attempts)
        logger.info("event=provider_started")
        repair = False
        for attempt in range(1, self._max_attempts + 1):
            delay = min(_MAX_BACKOFF, 0.5 * 2 ** (attempt - 1))
            try:
                # Bound the entire request in addition to the SDK's transport timeouts.
                async with asyncio.timeout(self._timeout):
                    response = await self._client.responses.create(
                        model=self._model,
                        instructions=INSTRUCTIONS + (_REPAIR if repair else ""),
                        input=[{"role": "user", "content": content}],
                        text={"format": _FORMAT},
                        store=False,
                        truncation="disabled",
                    )
            except (APITimeoutError, TimeoutError):
                attempts.record()
                code = "AI_TIMEOUT"
            except APIConnectionError:
                attempts.record()
                code = "AI_CONNECTION_ERROR"
            except APIStatusError as error:
                attempts.record()
                status = error.status_code
                if status == 408:
                    code = "AI_TIMEOUT"
                elif status == 429 and error.code != "insufficient_quota":
                    code = "AI_RATE_LIMIT"
                elif 500 <= status < 600:
                    code = "AI_UPSTREAM_ERROR"
                else:
                    self._fail("AI_REQUEST_REJECTED", attempts)
                if error.response.headers.get("x-should-retry") == "false":
                    self._fail(code, attempts)
                delay = max(delay, _retry_after(error))
            except (APIError, ValueError):
                attempts.record()
                self._fail("AI_SDK_ERROR", attempts)
            else:
                attempts.record(_token_counts(response), getattr(response, "model", None))
                try:
                    if _refused(response):
                        self._fail("AI_REFUSAL", attempts)
                    if response.status != "completed":
                        self._fail("AI_RESPONSE_INCOMPLETE", attempts)
                    rows, review_reasons = validated_output(
                        response.output_text, transcript_snapshot, checklist_snapshot
                    )
                except (
                    ValidationError,
                    EvaluationValidationError,
                    OverflowError,
                    AttributeError,
                    TypeError,
                ):
                    code = "AI_INVALID_RESULT"
                    repair = True
                else:
                    metadata = attempts.metadata()
                    logger.info(
                        "event=provider_completed attempts=%d duration_ms=%d",
                        attempt,
                        metadata.duration_ms,
                    )
                    return ProviderResult(
                        **metadata.model_dump(),
                        criterion_results=rows,
                        review_reasons=review_reasons,
                    )

            if attempt == self._max_attempts or delay > _MAX_BACKOFF:
                break
            logger.warning("event=provider_retry attempt=%d error=%s", attempt, code)
            await asyncio.sleep(delay)
        self._fail(code, attempts)

    @staticmethod
    def _fail(code: str, attempts: Attempts) -> Never:
        metadata = attempts.metadata()
        logger.error(
            "event=provider_failed attempts=%d duration_ms=%d error=%s",
            attempts.count,
            metadata.duration_ms,
            code,
        )
        raise ProviderFailure(metadata if attempts.count else None) from None


@asynccontextmanager
async def openai_provider(settings: Settings) -> AsyncIterator[OpenAIProvider]:
    """Own one SDK client for the lifetime of the consuming worker."""
    settings.require_llm()
    if not settings.openai_model.strip():
        raise ConfigurationUnavailable("AI_MODEL_NOT_CONFIGURED", "AI model is not configured.")
    # The SDK merges this environment variable after its generated auth headers.
    if environ.get("OPENAI_CUSTOM_HEADERS", "").strip():
        raise ConfigurationUnavailable(
            "AI_SDK_CONFIG_CONFLICT",
            "Custom SDK headers are incompatible with the evaluation provider.",
        )
    # SDK debug records may contain full request bodies or exception chains.
    for name in ("openai", "httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    async with AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value().strip(),
        base_url="https://api.openai.com/v1",
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
        http_client=DefaultAsyncHttpxClient(follow_redirects=False),
    ) as client:
        yield OpenAIProvider(client, settings)
