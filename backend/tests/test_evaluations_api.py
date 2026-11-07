"""Synthetic provider with the real API, SQLite migrations and N-02 scorer."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.install import install_data_api
from app.config import Settings
from app.db.migrate import migrate
from app.db.session import session_factory
from app.main import create_app
from app.models.evaluations import AppealDecision, Evaluation
from app.services.evaluation_worker import ProviderFailure, evaluation_worker

API = "/api/v1"
SECRET = "SYNTHETIC_PRIVATE_MARKER"
CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/openapi.json").read_text(encoding="utf-8")
)


def check_schema(name, instance):
    Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]}
    ).validate(instance)


def provider_result(checklist):
    return {
        "provider": "test-provider",
        "model_requested": "synthetic",
        "model_reported": None,
        "duration_ms": 25,
        "usage": {
            "request_count": 2,
            "input_tokens": 20,
            "output_tokens": None,
            "total_tokens": None,
            "cached_input_tokens": 4,
            "reasoning_output_tokens": None,
        },
        "estimated_cost_usd": 0.001,
        "review_reasons": ["Manual review"],
        "criterion_results": [
            {
                "criterion_id": criterion["id"],
                "applicability": "applicable",
                "answer": 1 if criterion["type"] == "scale" else True,
                "explanation": "Synthetic explanation",
                "evidence": [{"kind": "message", "message_id": "m0001"}],
                "review_reasons": [],
            }
            for criterion in checklist["criteria"]
        ],
    }


class FakeProvider:
    def __init__(self, mode="success", gate=None):
        self.mode = mode
        self.gate = gate
        self.calls = []

    async def evaluate(self, transcript_snapshot, checklist_snapshot, prompt_version):
        self.calls.append((transcript_snapshot, checklist_snapshot, prompt_version))
        if self.gate is not None and not await asyncio.to_thread(self.gate.wait, 10):
            raise RuntimeError("Synthetic gate timed out")
        result = provider_result(checklist_snapshot)
        if self.mode == "failure":
            raise RuntimeError(SECRET)
        if self.mode == "known_usage_failure":
            raise ProviderFailure(result)
        if self.mode == "bad_evidence":
            result["criterion_results"][0]["evidence"][0]["message_id"] = "m9999"
        if self.mode == "mutates_snapshot":
            transcript_snapshot["messages"][0]["id"] = "m9999"
            result["criterion_results"][0]["evidence"][0]["message_id"] = "m9999"
        return result


@pytest.fixture
def app_for(tmp_path):
    url = f"sqlite:///{tmp_path}/test.sqlite3"

    def make_app(provider=None, *, run=True, concurrency=2):
        settings = Settings(_env_file=None, CQA_DATABASE_URL=url)
        app = create_app(settings)
        install_data_api(app)
        core_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            async with core_lifespan(application):
                migrate(engine=application.state.engine)
                sessions = session_factory(application.state.engine)
                application.state.session_factory = sessions
                if run:
                    async with evaluation_worker(
                        application,
                        sessions,
                        provider,
                        concurrency=concurrency,
                        prompt_version="test-v1",
                    ):
                        yield
                else:
                    application.state.evaluation_provider = provider
                    yield

        app.router.lifespan_context = lifespan
        return app

    return make_app


def upload(client, transcript=SECRET):
    response = client.post(
        f"{API}/conversations/import",
        json={
            "records": [
                {
                    "conversation_id": "synthetic",
                    "channel": "agent chat",
                    "language": "RU",
                    "audio_url": None,
                    "transcript": [
                        {"timestamp": "00:01", "speaker": "operator", "text": transcript}
                    ],
                }
            ]
        },
    )
    assert response.status_code == 200
    return response.json()["conversation_ids"][0]


def data(client):
    conversation_id = upload(client)
    response = client.post(
        f"{API}/checklists",
        json={
            "name": "Synthetic",
            "criteria": [
                {
                    "id": "scale",
                    "name": "Scale",
                    "instruction": "Synthetic scoring",
                    "type": "scale",
                    "weight": 3,
                    "scale": [
                        {"value": 1, "description": "Low"},
                        {"value": 7, "description": "High"},
                    ],
                },
                {
                    "id": "binary",
                    "name": "Binary",
                    "instruction": "Synthetic scoring",
                    "type": "binary",
                    "weight": 1,
                },
            ],
        },
    )
    assert response.status_code == 201
    return conversation_id, response.json()


def enqueue(client, conversation_id, checklist):
    response = client.post(
        f"{API}/evaluations",
        json={"conversation_id": conversation_id, "checklist_version_id": checklist["id"]},
    )
    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "queued"
    check_schema("Evaluation", result)
    return result


def wait_for(client, evaluation_id, *states):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        result = client.get(f"{API}/evaluations/{evaluation_id}").json()
        if result["status"] in states:
            return result
        sleep(0.03)
    pytest.fail("Evaluation did not reach the expected status")


def test_queued_restart_pins_versions_and_preserves_decimal(app_for, caplog):
    caplog.set_level("INFO", logger="cqa")
    with TestClient(app_for(object(), run=False)) as client:
        conversation_id, checklist = data(client)
        pending = enqueue(client, conversation_id, checklist)
        assert upload(client, "Synthetic updated transcript") == conversation_id
        changed = client.post(
            f"{API}/checklists/{checklist['checklist_id']}/versions",
            json={"expected_version_number": 1, "criteria": checklist["criteria"][1:]},
        )
        assert changed.status_code == 201

    provider = FakeProvider()
    app = app_for(provider)
    with TestClient(app) as client:
        result = wait_for(client, pending["id"], "completed", "failed")
        assert result["status"] == "completed"
        check_schema("Evaluation", result)
        assert result["original"] == result["final"]
        assert result["original"]["total"] == 35.71
        assert result["provenance"]["usage"]["request_count"] == 2
        assert result["provenance"]["estimated_cost_usd"] == 0.001
        assert result["review_required"] is True
        transcript, selected, prompt_version = provider.calls[0]
        assert transcript["id"] == pending["transcript_version_id"]
        assert transcript["messages"][0]["text"] == SECRET
        assert selected["id"] == checklist["id"]
        assert prompt_version == "test-v1"
        view = client.get(f"{API}/evaluations/{pending['id']}")
        exact = json.loads(view.text, parse_float=Decimal)["original"]["criteria"][0]["base_points"]
        assert isinstance(exact, Decimal) and len(exact.as_tuple().digits) > 17
        with app.state.session_factory() as db:
            saved = db.get(Evaluation, pending["id"])
            assert saved.original["criteria"][0]["base_points"] == exact

        listed = client.get(
            f"{API}/evaluations", params={"conversation_id": conversation_id, "status": "completed"}
        ).json()
        check_schema("EvaluationPage", listed)
        assert listed["items"] == [result]
        assert client.get(f"{API}/evaluations?status=queued").json()["total"] == 0
        latest = client.get(f"{API}/conversations/{conversation_id}").json()["latest_evaluation"]
        assert latest["id"] == pending["id"] and latest["original_total"] == 35.71
        with pytest.raises(IntegrityError), app.state.session_factory.begin() as db:
            db.execute(
                text("UPDATE evaluations SET original = '{}' WHERE id = :id"), {"id": pending["id"]}
            )
    assert SECRET not in caplog.text
    assert "event=evaluation_queued" in caplog.text
    assert "event=evaluation_completed status=completed" in caplog.text


def test_recovery_marks_running_failed_and_keeps_usage(app_for, caplog):
    app = app_for(object(), run=False)
    with TestClient(app) as client:
        conversation, checklist = data(client)
        interrupted = enqueue(client, conversation, checklist)
        queued = enqueue(client, conversation, checklist)
        metadata = provider_result(checklist)
        provenance = {
            key: metadata[key]
            for key in (
                "provider",
                "model_requested",
                "model_reported",
                "duration_ms",
                "usage",
                "estimated_cost_usd",
            )
        }
        provenance["prompt_version"] = "previous"
        with app.state.session_factory.begin() as db:
            row = db.get(Evaluation, interrupted["id"])
            row.status = "running"
            row.provenance = provenance
    provider = FakeProvider()
    with TestClient(app_for(provider)) as client:
        failed = wait_for(client, interrupted["id"], "failed")
        check_schema("Evaluation", failed)
        assert failed["error"]["code"] == "INTERRUPTED"
        assert failed["provenance"] == provenance
        assert failed["original"] is None
        wait_for(client, queued["id"], "completed")
        assert len(provider.calls) == 1
    assert "event=evaluations_interrupted error=INTERRUPTED records=1" in caplog.text


def test_concurrency_and_database_writes_during_provider_calls(app_for):
    gate = Event()
    provider = FakeProvider(gate=gate)
    with TestClient(app_for(provider, concurrency=2)) as client:
        try:
            conversation, checklist = data(client)
            jobs = [enqueue(client, conversation, checklist) for _ in range(3)]
            deadline = monotonic() + 10
            while monotonic() < deadline and len(provider.calls) < 2:
                sleep(0.03)
            assert len(provider.calls) == 2
            assert client.get(f"{API}/evaluations?status=running").json()["total"] == 2
            assert client.get(f"{API}/evaluations?status=queued").json()["total"] == 1
            assert upload(client, "Synthetic concurrent write") == conversation
        finally:
            gate.set()
        for job in jobs:
            wait_for(client, job["id"], "completed")
        assert len(provider.calls) == 3


@pytest.mark.parametrize(
    ("mode", "code", "has_provenance"),
    [
        ("failure", "AI_PROVIDER_ERROR", False),
        ("known_usage_failure", "AI_PROVIDER_ERROR", True),
        ("bad_evidence", "AI_INVALID_RESULT", True),
        ("mutates_snapshot", "AI_INVALID_RESULT", True),
    ],
)
def test_failure_persists_safe_error_and_available_usage(
    app_for, mode, code, has_provenance, caplog
):
    with TestClient(app_for(FakeProvider(mode))) as client:
        conversation, checklist = data(client)
        job = enqueue(client, conversation, checklist)
        failed = wait_for(client, job["id"], "failed")
        check_schema("Evaluation", failed)
        assert failed["error"]["code"] == code
        assert failed["original"] is failed["final"] is None
        assert (failed["provenance"] is not None) == has_provenance
        if has_provenance:
            assert failed["provenance"]["usage"]["request_count"] == 2
        assert SECRET not in json.dumps(failed) + caplog.text
    statuses = [r for r in caplog.records if r.name == "cqa.worker"]
    assert [r.getMessage() for r in statuses] == [
        "event=evaluation_queued",
        "event=evaluation_started status=running",
        f"event=evaluation_failed status=failed error={code}",
    ]
    assert [r.levelname for r in statuses] == ["INFO", "INFO", "ERROR"]
    assert all(record.exc_info is None for record in caplog.records)


def test_missing_provider_rejects_new_launches_and_fails_persisted_queue(app_for):
    with TestClient(app_for(object(), run=False)) as client:
        conversation, checklist = data(client)
        pending = enqueue(client, conversation, checklist)
    with TestClient(app_for()) as client:
        rejected = client.post(
            f"{API}/evaluations",
            json={"conversation_id": conversation, "checklist_version_id": checklist["id"]},
        )
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "AI_NOT_CONFIGURED"
        assert wait_for(client, pending["id"], "failed")["error"]["code"] == "AI_NOT_CONFIGURED"
        assert client.get(f"{API}/evaluations").json()["total"] == 1


def open_appeal(client, evaluation_id, criterion_id):
    return client.post(
        f"{API}/evaluations/{evaluation_id}/appeals",
        json={"criterion_id": criterion_id, "comment": SECRET},
    )


def decide(client, appeal_id, **payload):
    return client.post(f"{API}/appeals/{appeal_id}/decisions", json=payload)


def test_appeal_override_confirm_and_history(app_for, caplog):
    caplog.set_level("INFO", logger="cqa")
    provider = FakeProvider()
    app = app_for(provider)
    with TestClient(app) as client:
        conversation, checklist = data(client)
        job = enqueue(client, conversation, checklist)
        completed = wait_for(client, job["id"], "completed")
        opened = open_appeal(client, job["id"], "scale")
        assert opened.status_code == 201
        appeal = opened.json()
        check_schema("Appeal", appeal)
        assert open_appeal(client, job["id"], "scale").status_code == 409
        assert open_appeal(client, job["id"], "missing").status_code == 404
        override = {
            "action": "override",
            "reason": SECRET,
            "expected_revision": 0,
            "applicability": "applicable",
            "answer": 7,
            "evidence": [{"kind": "message", "message_id": "m0001"}],
        }
        assert decide(client, appeal["id"], **dict(override, reason=" ")).status_code == 422
        assert decide(client, appeal["id"], **dict(override, answer=True)).status_code == 422
        assert (
            decide(
                client,
                appeal["id"],
                **dict(override, evidence=[{"kind": "message", "message_id": "m9999"}]),
            ).status_code
            == 422
        )
        assert client.get(f"{API}/appeals/{appeal['id']}").json()["status"] == "open"
        changed = decide(client, appeal["id"], **override)
        assert changed.status_code == 200
        result = changed.json()
        check_schema("DecisionResponse", result)
        assert result["evaluation"]["original"] == completed["original"]
        assert result["evaluation"]["final"]["total"] == 100
        assert result["evaluation"]["revision"] == 1
        assert result["appeal"]["original"]["answer"] == 1
        assert result["appeal"]["decision"]["result"]["answer"] == 7
        assert decide(client, appeal["id"], **override).status_code == 409

        second = open_appeal(client, job["id"], "scale").json()
        confirmation = dict(action="confirm", reason="Synthetic confirmation", expected_revision=1)
        confirmed = decide(client, second["id"], **confirmation).json()
        assert confirmed["evaluation"]["revision"] == 2
        assert confirmed["appeal"]["decision"]["result"] == result["appeal"]["decision"]["result"]
        assert confirmed["evaluation"]["final"] == result["evaluation"]["final"]
        listed = client.get(f"{API}/appeals?evaluation_id={job['id']}&status=resolved").json()
        check_schema("AppealPage", listed)
        assert listed["total"] == 2
        with pytest.raises(IntegrityError), app.state.session_factory.begin() as db:
            db.execute(text("UPDATE appeal_decisions SET reason = 'changed'"))
        assert len(provider.calls) == 1
    assert SECRET not in caplog.text
    assert "event=appeal_conflict error=APPEAL_ALREADY_OPEN" in caplog.text
    assert caplog.text.count("event=appeal_decision_saved") == 2


def test_concurrent_decisions_use_same_revision_once(app_for):
    app = app_for(FakeProvider())
    with TestClient(app) as client:
        conversation, checklist = data(client)
        job = enqueue(client, conversation, checklist)
        wait_for(client, job["id"], "completed")
        ids = [
            open_appeal(client, job["id"], criterion).json()["id"]
            for criterion in ("scale", "binary")
        ]
        barrier = Barrier(2)

        def concurrent_decide(appeal_id):
            barrier.wait(timeout=5)
            return decide(
                client, appeal_id, action="confirm", reason="Synthetic", expected_revision=0
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(concurrent_decide, ids))
        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
        with app.state.session_factory() as db:
            assert len(db.scalars(select(AppealDecision)).all()) == 1
            assert db.get(Evaluation, job["id"]).revision == 1


def test_decision_commit_failure_rolls_back_without_success_log(app_for, caplog):
    app = app_for(FakeProvider())
    with TestClient(app) as client:
        conversation, checklist = data(client)
        job = enqueue(client, conversation, checklist)
        wait_for(client, job["id"], "completed")
        appeal = open_appeal(client, job["id"], "scale").json()
        original = client.get(f"{API}/evaluations/{job['id']}").json()
        sessions = app.state.session_factory

        def reject_decision_commit(session):
            if session.info.get("cqa_saved_events") == [("cqa.appeals", "appeal_decision_saved")]:
                raise OperationalError(SECRET, {}, RuntimeError(SECRET))

        caplog.clear()
        event.listen(sessions, "before_commit", reject_decision_commit)
        try:
            response = decide(
                client, appeal["id"], action="confirm", reason=SECRET, expected_revision=0
            )
        finally:
            event.remove(sessions, "before_commit", reject_decision_commit)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert client.get(f"{API}/appeals/{appeal['id']}").json() == appeal
        assert client.get(f"{API}/evaluations/{job['id']}").json() == original
        assert SECRET not in response.text + caplog.text
        assert "event=appeal_decision_saved" not in caplog.text
        errors = [record for record in caplog.records if record.name == "cqa.errors"]
        assert len(errors) == 1 and errors[0].levelname == "ERROR"
