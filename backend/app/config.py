"""Server-only configuration. Relative storage paths resolve from the repository root."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationUnavailable(Exception):
    """A safe, public configuration error; never includes a setting's value."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    dataset_api_url: SecretStr = Field(default=SecretStr(""), alias="CQA_DATASET_API_URL")
    default_group_id: str = Field(default="DEV", alias="CQA_DEFAULT_GROUP_ID", min_length=1)
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-6-astra", alias="OPENAI_MODEL", min_length=1)
    llm_timeout_seconds: float = Field(default=90, alias="CQA_LLM_TIMEOUT_SECONDS", gt=0, le=600)
    llm_max_retries: int = Field(default=2, alias="CQA_LLM_MAX_RETRIES", ge=0, le=5)
    evaluation_concurrency: int = Field(default=2, alias="CQA_EVALUATION_CONCURRENCY", ge=1, le=8)
    database_url: str = Field(
        default="sqlite:///./data/private/cqa.sqlite3", alias="CQA_DATABASE_URL", repr=False
    )
    allowed_origins: str = Field(default="http://localhost:5173", alias="CQA_ALLOWED_ORIGINS")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="CQA_LOG_LEVEL"
    )

    @field_validator("dataset_api_url")
    @classmethod
    def validate_dataset_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw:
            parsed = urlsplit(raw)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("CQA_DATASET_API_URL must be an HTTPS URL without credentials")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            parsed = make_url(value)
        except Exception:
            raise ValueError("CQA_DATABASE_URL must be a SQLite URL") from None
        if parsed.drivername != "sqlite" or parsed.host or not parsed.database or parsed.query:
            raise ValueError("CQA_DATABASE_URL must be a local sqlite:///path URL")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, value: str) -> str:
        origins = [part.strip() for part in value.split(",") if part.strip()]
        if not origins:
            raise ValueError("CQA_ALLOWED_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CQA_ALLOWED_ORIGINS must contain comma-separated HTTP origins")
        return ",".join(origins)

    @property
    def cors_origins(self) -> list[str]:
        return self.allowed_origins.split(",")

    @property
    def sqlalchemy_url(self) -> URL:
        url = make_url(self.database_url)
        if url.database == ":memory:":
            return url
        path = Path(url.database or "")
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return url.set(database=str(path.resolve()))

    def require_llm(self) -> None:
        if not self.openai_api_key.get_secret_value().strip():
            raise ConfigurationUnavailable("AI_NOT_CONFIGURED", "AI API key is not configured.")

    def require_dataset(self) -> None:
        if not self.dataset_api_url.get_secret_value().strip():
            raise ConfigurationUnavailable(
                "DATASET_NOT_CONFIGURED", "Dataset API URL is not configured."
            )
