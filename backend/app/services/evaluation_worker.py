"""Single-process durable queue with independently committed state transitions."""

import asyncio
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

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
_REVIEW_REASONS = TypeAdapter(list[Text])
_COST = TypeAdapter(Number | None)


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


def _field(result, name):
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def _provenance(result, prompt_version):
    metadata = ProviderMetadata.model_validate(result)
    try:
        cost = _COST.validate_python(_field(result, "estimated_cost_usd"))
        valid_cost = True
    except (ValidationError, OverflowError):
        cost = None
        valid_cost = False
    return (
        dict(**metadata.model_dump(), prompt_version=prompt_version, estimated_cost_usd=cost),
        valid_cost,
    )


async def _save_running(stop, sessions, evaluation_id, **values):
    # Keep the pending write in this slot through transient DB outages. At
    # shutdown leave the run for recovery if the database is still unavailable.
    delay = 0.25
    while True:
        try:
            return await asyncio.to_thread(
                evaluations.update_running, sessions, evaluation_id, **values
            )
        except OperationalError:
            if delay == 0.25:
                logger.warning("event=evaluation_write_retry error=DATABASE_UNAVAILABLE")
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return False
            except TimeoutError:
                delay = min(2, delay * 2)


async def _fail(stop, sessions, evaluation_id, code, **values):
    try:
        saved = await _save_running(
            stop,
            sessions,
            evaluation_id,
            status="failed",
            completed_at=now(),
            error=evaluations.failure(code),
            **values,
        )
    except Exception as exc:
        log_internal_error("EVALUATION_FAILURE_NOT_SAVED", exc)
        return
    if saved:
        logger.error("event=evaluation_failed status=failed error=%s", code)


async def _evaluate(stop, sessions, evaluation_id, provider, prompt_version):
    if provider is None:
        await _fail(stop, sessions, evaluation_id, "AI_NOT_CONFIGURED")
        return
    transcript, checklist, pinned_id = await asyncio.to_thread(_load, sessions, evaluation_id)
    try:
        result = await provider.evaluate(deepcopy(transcript), deepcopy(checklist), prompt_version)
    except ProviderFailure as exc:
        try:
            provenance, _ = _provenance(exc.metadata, prompt_version)
        except (ValidationError, OverflowError):
            provenance = None
        values = {"provenance": provenance} if provenance is not None else {}
        await _fail(stop, sessions, evaluation_id, "AI_PROVIDER_ERROR", **values)
        return
    except Exception as exc:
        log_internal_error("AI_PROVIDER_ERROR", exc)
        await _fail(stop, sessions, evaluation_id, "AI_PROVIDER_ERROR")
        return

    try:
        provenance, valid_cost = _provenance(result, prompt_version)
    except (ValidationError, OverflowError):
        await _fail(stop, sessions, evaluation_id, "AI_INVALID_RESULT")
        return
    try:
        saved = await _save_running(stop, sessions, evaluation_id, provenance=provenance)
    except Exception as exc:
        log_internal_error("EVALUATION_PROVENANCE_NOT_SAVED", exc)
        await _fail(
            stop, sessions, evaluation_id, "EVALUATION_INTERNAL_ERROR", provenance=provenance
        )
        return
    if not saved:
        return

    rows = _field(result, "criterion_results")
    if isinstance(rows, list):
        # N-01 may expose its own Pydantic classes. N-02 validates their data
        # against its own schema, including rows copied with unvalidated edits.
        rows = [
            row.model_dump(warnings=False) if isinstance(row, BaseModel) else row for row in rows
        ]
    try:
        reasons = _REVIEW_REASONS.validate_python(_field(result, "review_reasons"))
        if not valid_cost or provenance["usage"]["request_count"] < 1:
            raise EvaluationValidationError("INVALID_PROVIDER_PROVENANCE")
        assessment = await asyncio.to_thread(
            build_assessment,
            criteria=checklist["criteria"],
            criterion_results=rows,
            transcript_snapshot=transcript,
            transcript_version_id=pinned_id,
        )
    except (EvaluationValidationError, ValidationError):
        await _fail(stop, sessions, evaluation_id, "AI_INVALID_RESULT")
        return
    saved = await _save_running(
        stop,
        sessions,
        evaluation_id,
        status="completed",
        completed_at=now(),
        original=assessment,
        final=assessment,
        provider_review_reasons=reasons,
    )
    if saved:
        logger.info("event=evaluation_completed status=completed")


async def _slot(stop, sessions, provider, prompt_version):
    claim_failed = False
    while not stop.is_set():
        evaluation_id = None
        try:
            evaluation_id = await asyncio.to_thread(evaluations.claim, sessions)
            claim_failed = False
            if evaluation_id is not None:
                logger.info("event=evaluation_started status=running")
                await _evaluate(stop, sessions, evaluation_id, provider, prompt_version)
                continue
        except Exception as exc:
            if evaluation_id is None:
                if not claim_failed:
                    log_internal_error("EVALUATION_QUEUE_UNAVAILABLE", exc)
                claim_failed = True
            else:
                log_internal_error("EVALUATION_INTERNAL_ERROR", exc)
                await _fail(stop, sessions, evaluation_id, "EVALUATION_INTERNAL_ERROR")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1 if claim_failed else 0.25)
        except TimeoutError:
            pass


@asynccontextmanager
async def evaluation_worker(
    app, sessions, provider: EvaluationProvider | None, *, concurrency: int, prompt_version: str
):
    """Start once per API lifespan, after migration and before accepting traffic."""
    if concurrency < 1 or not prompt_version.strip():
        raise ValueError("Invalid evaluation worker settings")
    engine = sessions.kw["bind"]
    if engine.url.database in (None, "", ":memory:") or isinstance(engine.pool, StaticPool):
        raise ValueError("Evaluation worker requires file-backed SQLite")
    try:
        interrupted = await asyncio.to_thread(evaluations.recover, sessions)
    except Exception as exc:
        log_internal_error("EVALUATION_RECOVERY_FAILED", exc)
        raise RuntimeError("EVALUATION_RECOVERY_FAILED") from None
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
