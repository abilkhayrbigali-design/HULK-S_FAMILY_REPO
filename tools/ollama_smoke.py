"""Run a live Ollama smoke on a new synthetic dialogue and temporary SQLite."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from app.config import Settings
from app.diagnostics import log_internal_error
from app.evaluation.ollama_provider import (
    PROMPT_VERSION,
    OllamaSettings,
    ollama_provider,
)
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "data/fixtures/synthetic"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@asynccontextmanager
async def _api(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://smoke"
        ) as client,
    ):
        yield client


async def _smoke(database, local):
    config = Settings(
        _env_file=None,
        CQA_DATABASE_URL=f"sqlite:///{database.as_posix()}",
        CQA_DATASET_API_URL="",
        OPENAI_API_KEY="",
        CQA_EVALUATION_CONCURRENCY=1,
    )
    async with (
        ollama_provider(local) as provider,
        _api(
            create_app(config, provider=provider, prompt_version=PROMPT_VERSION)
        ) as api,
    ):
        record = _fixture("import.request.json")["records"][0]
        imported = await api.post(
            "/api/v1/conversations/import", json={"records": [record]}
        )
        imported.raise_for_status()
        checklist = _fixture("checklist-version.response.json")
        created = await api.post(
            "/api/v1/checklists",
            json={"name": checklist["name"], "criteria": checklist["criteria"]},
        )
        created.raise_for_status()
        queued = await api.post(
            "/api/v1/evaluations",
            json={
                "conversation_id": imported.json()["conversation_ids"][0],
                "checklist_version_id": created.json()["id"],
            },
        )
        queued.raise_for_status()
        route = f"/api/v1/evaluations/{queued.json()['id']}"
        async with asyncio.timeout(
            local.timeout_seconds * (local.max_retries + 1) + 15
        ):
            while True:
                result = (await api.get(route)).raise_for_status().json()
                if result["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.25)
    async with _api(create_app(config)) as api:
        persisted = (await api.get(route)).raise_for_status().json()
    metadata = result["provenance"] or {}
    report = {
        "status": result["status"],
        "persisted": persisted == result,
        "provider": metadata.get("provider"),
        "model_requested": local.model,
        "model_reported": metadata.get("model_reported"),
        "duration_ms": metadata.get("duration_ms"),
        "usage": metadata.get("usage"),
        "estimated_cost_usd": metadata.get("estimated_cost_usd"),
    }
    print(json.dumps(report, ensure_ascii=False))
    return (
        result["status"] == "completed"
        and metadata.get("provider") == "ollama"
        and persisted == result
    )


def main():
    try:
        with TemporaryDirectory(prefix="cqa-ollama-smoke-") as directory:
            return (
                0
                if asyncio.run(
                    _smoke(Path(directory) / "smoke.sqlite3", OllamaSettings())
                )
                else 1
            )
    except Exception as exc:  # noqa: BLE001 -- CLI boundary, suppress private traceback.
        log_internal_error("OLLAMA_SMOKE_FAILED", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
