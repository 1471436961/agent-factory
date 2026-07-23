"""Settings validation tests."""

import pytest
from pydantic import ValidationError

from agent_factory.application.security import FactoryRole
from agent_factory.settings import Settings


def test_settings_defaults_are_safe_for_local_development() -> None:
    settings = Settings.model_validate({})

    assert settings.environment == "development"
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.auth_token is None
    assert settings.auth_subject == "local-owner"
    assert settings.auth_roles == frozenset({FactoryRole.ADMIN})
    assert settings.default_page_size <= settings.max_page_size
    assert (settings.migrations_dir / "001_initial.sql").is_file()
    assert (settings.migrations_dir / "002_persistence_contracts.sql").is_file()
    assert (settings.migrations_dir / "003_skill_governance.sql").is_file()
    assert (
        settings.migrations_dir / "004_instance_configuration_checksum.sql"
    ).is_file()
    assert (settings.migrations_dir / "005_task_outcome_integrity.sql").is_file()
    assert settings.sqlite_busy_timeout_ms == 5_000
    assert settings.max_inline_knowledge_bytes == 262_144


def test_settings_read_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_FACTORY_DEFAULT_PAGE_SIZE", "7")
    monkeypatch.setenv("AGENT_FACTORY_MAX_PAGE_SIZE", "11")
    monkeypatch.setenv(
        "AGENT_FACTORY_AUTH_TOKEN",
        "environment-token-that-is-at-least-32-characters",
    )
    monkeypatch.setenv("AGENT_FACTORY_AUTH_SUBJECT", "environment-owner")
    monkeypatch.setenv("AGENT_FACTORY_AUTH_ROLES", '["operator", "auditor"]')

    settings = Settings()

    assert settings.default_page_size == 7
    assert settings.max_page_size == 11
    assert settings.auth_token is not None
    assert settings.auth_token.get_secret_value().startswith("environment-token")
    assert settings.auth_subject == "environment-owner"
    assert settings.auth_roles == frozenset({FactoryRole.OPERATOR, FactoryRole.AUDITOR})


def test_settings_reject_default_page_size_above_maximum() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        Settings.model_validate(
            {
                "default_page_size": 101,
                "max_page_size": 100,
            }
        )


def test_settings_reject_short_auth_token() -> None:
    with pytest.raises(ValidationError) as captured:
        Settings.model_validate({"auth_token": "too-short"})

    assert captured.value.errors()[0]["type"] == "too_short"
