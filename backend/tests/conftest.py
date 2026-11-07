import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Tests must not depend on or print an operator's environment secrets."""
    for field in Settings.model_fields.values():
        monkeypatch.delenv(field.alias, raising=False)


@pytest.fixture
def settings():
    return Settings(_env_file=None, CQA_DATABASE_URL="sqlite:///:memory:")
