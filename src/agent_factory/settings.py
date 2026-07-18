"""Environment-backed process configuration."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent / "infrastructure" / "sqlite" / "sql"
)


class Settings(BaseSettings):
    """Validated configuration loaded from ``AGENT_FACTORY_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_FACTORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/agent_factory.db"
    api_prefix: str = "/api/v1"
    max_request_bytes: int = Field(default=1_048_576, ge=1_024)
    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)
    idempotency_ttl_seconds: int = Field(default=86_400, ge=60)
    audit_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    data_dir: Path = Field(default=Path("./data"))
    migrations_dir: Path = Field(default=DEFAULT_MIGRATIONS_DIR)

    @model_validator(mode="after")
    def validate_page_sizes(self) -> Self:
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size must not exceed max_page_size")
        return self
