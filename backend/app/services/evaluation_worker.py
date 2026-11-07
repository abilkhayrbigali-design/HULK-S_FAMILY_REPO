"""Single-process durable queue with independently committed state transitions."""

import asyncio
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from app.diagnostics import log_internal_error
from app.evaluation.evidence import EvaluationValidationError
from app.evaluation.scoring import build_assessment
from app.models.data import now
from app.models.evaluations import Evaluation
from app.schemas.data import Number, Text
from app.services import evaluations
from app.services.errors import require

logger = logging.getLogger("cqa.worker")
Counter = Annotated[StrictInt, Field(ge=0)]


class EvaluationProvider(Protocol):
    async def evaluate(self, transcript_snapshot, checklist_snapshot, prompt_version): ...


class Usage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_count: Counter
    input_tokens: Counter | None
    output_tokens: Counter | None
    total_tokens: Counter | None
    cached_input_tokens: Counter | None
    reasoning_output_tokens: Counter | None


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: Text
    model_requested: Text
    model_reported: Text | None
    duration_ms: Counter
    usage: Usage
    review_reasons: list[Text] = Field(default_factory=list)
    estimated_cost_usd: Number | None = None


class ProviderFailure(Exception):
    """Optional provider failure envelope with already known cumulative usage."""

    def __init__(self, metadata=None):
        self.metadata = metadata
        super().__init__("AI_PROVIDER_ERROR")


def _load(sessions, evaluation_id):
    with sessions() as db:
        row = require(db.get(Evaluation, evaluation_id))
        transcript, checklist = evaluations.snapshots(db, row)
        return transcript, checklist, row.transcript_version_id


def _provenance(metadata, prompt_version):
    return dict(
        **metadata.model_dump(exclude={"review_reasons"}),
        prompt_version=prompt_version,
    )


async def _fail(sessions, evaluation_id, code, **values):
    saved = await asyncio.to_thread(
        evaluations.update_running,
        sessions,
        evaluation_id,
        status="failed",
        completed_at=now(),
        error=evaluations.failure(code),
        **values,
    )
    if saved:
        logger.error("event=evaluation_failed status=failed error=%s", code)


async def _evaluate(sessions, evaluation_id, provider, prompt_version):
    if provider is None:
        await _fail(sessions, evaluation_id, "AI_NOT_CONFIGURED")
        return
    transcript, checklist, pinned_id = await asyncio.to_thread(_load, sessions, evaluation_id)
    try:
        result = await provider.evaluate(deepcopy(transcript), deepcopy(checklist), prompt_version)
    except ProviderFailure as exc:
        try:
            metadata = (
                ProviderMetadata.model_validate(exc.metadata) if exc.metadata is not None else None
            )
        except (ValidationError, OverflowError):
            metadata = None
        values = {"provenance": _provenance(metadata, prompt_version)} if metadata else {}
        await _fail(sessions, evaluation_id, "AI_PROVIDER_ERROR", **values)
        return
    except Exception as exc:
        log_internal_error("AI_PROVIDER_ERROR", exc)
        await _fail(sessions, evaluation_id, "AI_PROVIDER_ERROR")
        return

    try:
        metadata = ProviderMetadata.model_validate(result)
        provenance = _provenance(metadata, prompt_version)
    except (ValidationError, OverflowError):
        await _fail(sessions, evaluation_id, "AI_INVALID_RESULT")
        return
    saved = await asyncio.to_thread(
        evaluations.update_running, sessions, evaluation_id, provenance=provenance
    )
    if not saved:
        return

    rows = (
        result.get("criterion_results")
        if isinstance(result, dict)
        else getattr(result, "criterion_results", None)
    )
    try:
        if metadata.usage.request_count < 1:
            raise EvaluationValidationError("INVALID_REQUEST_COUNT")
        assessment = await asyncio.to_thread(
            build_assessment,
            criteria=checklist["criteria"],
            criterion_results=rows,
            transcript_snapshot=transcript,
            transcript_version_id=pinned_id,
        )
    except EvaluationValidationError:
        await _fail(sessions, evaluation_id, "AI_INVALID_RESULT")
        return
    saved = await asyncio.to_thread(
        evaluations.update_running,
        sessions,
        evaluation_id,
        status="completed",
        completed_at=now(),
        original=assessment,
        final=assessment,
        provider_review_reasons=metadata.review_reasons,
    )
    if saved:
        logger.info("event=evaluation_completed status=completed")


async def _slot(stop, sessions, provider, prompt_version):
    while not stop.is_set():
        evaluation_id = None
        try:
            evaluation_id = await asyncio.to_thread(evaluations.claim, sessions)
            if evaluation_id is not None:
                logger.info("event=evaluation_started status=running")
                await _evaluate(sessions, evaluation_id, provider, prompt_version)
                continue
        except Exception as exc:
            log_internal_error("EVALUATION_INTERNAL_ERROR", exc)
            if evaluation_id is not None:
                try:
                    await _fail(sessions, evaluation_id, "EVALUATION_INTERNAL_ERROR")
                except Exception as save_error:
                    log_internal_error("EVALUATION_FAILURE_NOT_SAVED", save_error)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except TimeoutError:
            pass


@asynccontextmanager
async def evaluation_worker(
    app, sessions, provider: EvaluationProvider | None, *, concurrency: int, prompt_version: str
):
    """Start once per API lifespan, after migration and before accepting traffic."""
    if concurrency < 1 or not prompt_version.strip():
        raise ValueError("Invalid evaluation worker settings")
    interrupted = await asyncio.to_thread(evaluations.recover, sessions)
    if interrupted:
        logger.error("event=evaluations_interrupted error=INTERRUPTED records=%d", interrupted)
    stop = asyncio.Event()
    app.state.session_factory = sessions
    app.state.evaluation_provider = provider
    tasks = [
        asyncio.create_task(_slot(stop, sessions, provider, prompt_version))
        for _ in range(concurrency)
    ]
    try:
        yield
    finally:
        app.state.evaluation_provider = None
        stop.set()
        await asyncio.gather(*tasks)
