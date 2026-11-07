"""Real SQLite + HTTP integration; all conversations are authored synthetic fixtures."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import httpx
import pytest
from app.api.install import install_data_api
from app.db.migrate import migrate
from app.db.session import make_engine, session_factory
from app.services.datasets import dataset_url
from app.services.seed import seed
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def setup(tmp_path):
    url = f"sqlite:///{tmp_path}/test.sqlite3"
    migrate(url)
    migrate(url)
    engine = make_engine(url)
    factory = session_factory(engine)
    with factory.begin() as db:
        checklist_id = seed(db)
    with factory.begin() as db:
        assert seed(db) == checklist_id
    app = FastAPI()
    install_data_api(app, factory)
    with TestClient(app) as client:
        yield client, factory, app, url
    engine.dispose()


@pytest.fixture
def record():
    return dict(
        conversation_id="synthetic-001",
        channel="agent chat",
        language="RU",
        audio_url=None,
        transcript=[
            dict(timestamp="00:01", speaker="client", text="Как найти справку?"),
            dict(
                timestamp="00:03",
                speaker="operator",
                text=" Здравствуйте! Меня зовут Алия. ",
            ),
        ],
    )


def upload(client, *records):
    return client.post("/api/v1/conversations/import", json={"records": list(records)})


def test_seed_versions_and_conflict(setup):
    client, factory, _, _ = setup
    listing = client.get("/api/v1/checklists").json()
    assert listing["total"] == 1
    checklist_id = listing["items"][0]["id"]
    original = client.get(f"/api/v1/checklists/{checklist_id}").json()[
        "current_version"
    ]
    assert len(original["criteria"]) == 8
    assert sum(c["weight"] for c in original["criteria"]) == 100
    scales = {
        c["id"]: [s["value"] for s in c["scale"]]
        for c in original["criteria"]
        if c["scale"]
    }
    assert scales == {"consultation": [0, 5, 10, 15, 20], "management": [0, 4, 7, 10]}
    criteria = deepcopy(original["criteria"])
    criteria[0]["weight"] = 12
    criteria.append(
        dict(
            id="custom",
            name="Новый",
            instruction="Проверь новый критерий",
            type="binary",
            weight=3,
        )
    )
    payload = dict(expected_version_number=1, criteria=criteria)
    result = client.post(f"/api/v1/checklists/{checklist_id}/versions", json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["version_number"] == 2
    assert (
        client.post(
            f"/api/v1/checklists/{checklist_id}/versions", json=payload
        ).status_code
        == 409
    )
    assert client.get(f"/api/v1/checklist-versions/{original['id']}").json() == original
    with factory.begin() as db:
        seed(db)
    assert (
        client.get(f"/api/v1/checklists/{checklist_id}").json()[
            "current_version_number"
        ]
        == 2
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"weight": True},
        {"weight": -1},
        {"weight": "10"},
        {"weight": 0},
        {"allow_na": True},
        {"na_rule": "unexpected"},
        {"instruction": " "},
        {"scale": []},
        {"type": "flag", "weight": 1},
        {
            "type": "scale",
            "scale": [
                {"value": True, "description": "bool"},
                {"value": 10, "description": "max"},
            ],
        },
        {
            "type": "scale",
            "scale": [
                {"value": 0, "description": "min"},
                {"value": 0, "description": "duplicate"},
            ],
        },
    ],
)
def test_invalid_criteria(setup, patch):
    client = setup[0]
    criterion = dict(id="x", name="X", instruction="Проверь", type="binary", weight=10)
    criterion.update(patch)
    assert (
        client.post(
            "/api/v1/checklists", json={"name": "Test", "criteria": [criterion]}
        ).status_code
        == 422
    )


def test_import_idempotency_history_and_filters(setup, record):
    client = setup[0]
    first = upload(client, record)
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1
    id_ = first.json()["conversation_ids"][0]
    original = client.get(f"/api/v1/conversations/{id_}").json()
    assert original["messages"][1]["text"].startswith(" ")
    assert original["messages"][1]["id"] == "m0002"
    assert upload(client, record).json()["unchanged"] == 1
    changed = deepcopy(record)
    changed["language"] = "KZ"
    changed["transcript"][1]["text"] = "Сәлеметсіз бе!"
    assert upload(client, changed).json()["new_versions"] == 1
    current = client.get(f"/api/v1/conversations/{id_}").json()
    assert current["transcript_version_id"] != original["transcript_version_id"]
    previous = client.get(
        f"/api/v1/conversations/{id_}",
        params={"transcript_version_id": original["transcript_version_id"]},
    ).json()
    assert previous["messages"] == original["messages"]
    assert previous["language"] == "RU"
    assert client.get("/api/v1/conversations?language=RU").json()["total"] == 0
    assert (
        client.get("/api/v1/conversations?language=KZ&group_id=UPLOAD").json()["total"]
        == 1
    )
    other = deepcopy(record)
    other["conversation_id"] = "synthetic-002"
    other_id = upload(client, other).json()["conversation_ids"][0]
    assert (
        client.get(
            f"/api/v1/conversations/{other_id}",
            params={"transcript_version_id": original["transcript_version_id"]},
        ).status_code
        == 404
    )
    # Re-importing historical content reuses its snapshot, without duplicating it.
    assert upload(client, record).json()["unchanged"] == 1
    assert (
        client.get(f"/api/v1/conversations/{id_}").json()["transcript_version_id"]
        == original["transcript_version_id"]
    )


def test_atomic_validation_and_safe_errors(setup, record):
    client = setup[0]
    bad = deepcopy(record)
    bad["conversation_id"] = "synthetic-bad"
    bad["transcript"][0]["speaker"] = "unknown"
    bad["audio_url"] = "https://example.invalid/?sig=synthetic-secret"
    result = upload(client, record, bad)
    assert result.status_code == 422
    assert "synthetic-secret" not in result.text
    assert client.get("/api/v1/conversations").json()["total"] == 0
    assert upload(client, record, record).status_code == 422
    bad = deepcopy(record)
    bad["transcript"] = []
    assert upload(client, bad).status_code == 422
    assert client.get("/api/v1/conversations?limit=101").status_code == 422
    assert client.get("/api/v1/conversations?offset=-1").status_code == 422


def test_database_fk_and_immutability(setup, record):
    client, factory, _, _ = setup
    upload(client, record)
    with factory() as db:
        assert db.scalar(text("PRAGMA foreign_keys")) == 1
        assert db.scalar(text("PRAGMA journal_mode")) == "wal"
    for sql in (
        "UPDATE messages SET text='changed'",
        "DELETE FROM transcript_versions",
        "UPDATE checklist_versions SET name='changed'",
        "INSERT INTO messages (transcript_version_id,id,position,timestamp,speaker,text) VALUES ('missing','m0001',1,'00:01','client','test')",
    ):
        with pytest.raises(IntegrityError), factory.begin() as db:
            db.execute(text(sql))


def test_restart_persistence(setup, record):
    client, _, _, url = setup
    id_ = upload(client, record).json()["conversation_ids"][0]
    engine = make_engine(url)
    app = FastAPI()
    install_data_api(app, session_factory(engine))
    with TestClient(app) as reopened:
        assert (
            reopened.get(f"/api/v1/conversations/{id_}").json()["messages"][0]["text"]
            == record["transcript"][0]["text"]
        )
    engine.dispose()


def test_concurrent_version_and_import(setup, record):
    client = setup[0]
    item = client.get("/api/v1/checklists").json()["items"][0]
    version = client.get(
        f"/api/v1/checklist-versions/{item['current_version_id']}"
    ).json()

    def save(_):
        return client.post(
            f"/api/v1/checklists/{item['id']}/versions",
            json=dict(expected_version_number=1, criteria=version["criteria"]),
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(save, range(2))) == [201, 409]
        imports = list(pool.map(lambda _: upload(client, record).json(), range(2)))
    assert sum(r["created"] for r in imports) == 1
    assert sum(r["unchanged"] for r in imports) == 1


def test_dataset_new_group_and_errors(setup, record, caplog):
    client, _, app, _ = setup
    url = "https://example.invalid/api?sig=synthetic%2Bsecret%2f&group_id=OLD&group_id=STALE&x="
    changed = dataset_url(url, "NEW GROUP")
    assert "sig=synthetic%2Bsecret%2f" in changed
    assert changed.count("group_id=") == 1
    app.state.dataset_url = url

    def respond(request):
        assert request.url.params["group_id"] == "NEW GROUP"
        return httpx.Response(
            200,
            json={
                "error": None,
                "isSuccess": True,
                "data": {
                    "dataset_group_id": "NEW GROUP",
                    "total_records": 1,
                    "records": [record],
                },
            },
        )

    app.state.dataset_transport = httpx.MockTransport(respond)
    result = client.post("/api/v1/datasets/import", json={"group_id": "NEW GROUP"})
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 1
    app.state.dataset_transport = httpx.MockTransport(lambda req: httpx.Response(500))
    result = client.post("/api/v1/datasets/import", json={"group_id": "NEW GROUP"})
    assert result.status_code == 502
    assert "synthetic" not in result.text
    assert "synthetic" not in caplog.text
    app.state.dataset_url = ""
    assert (
        client.post("/api/v1/datasets/import", json={"group_id": "DEV"}).status_code
        == 503
    )
