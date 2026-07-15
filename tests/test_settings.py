import pytest
from app.config.settings import Settings
from app.presentation.webhook import webhook_endpoint
from pydantic import ValidationError as PydanticValidationError


def _settings(**overrides: object) -> Settings:
    return Settings(
        bot_token="123:abc",
        database_url="postgresql://user:password@localhost:5432/plates",
        redis_url="redis://localhost:6379/0",
        owner_telegram_id=1,
        **overrides,
    )


def test_empty_webhook_environment_values_select_polling_mode() -> None:
    settings = _settings(webhook_url="", webhook_secret="")

    assert settings.webhook_url is None
    assert settings.webhook_secret is None
    assert settings.database_schema == "cpm2"
    assert settings.async_database_url == "postgresql+asyncpg://user:password@localhost:5432/plates"


def test_webhook_endpoint_uses_public_https_origin_and_path() -> None:
    settings = _settings(
        webhook_url="https://bot.example.com/base/",
        webhook_path="/telegram",
        webhook_secret="A_valid-webhook_secret",
    )

    assert webhook_endpoint(settings) == "https://bot.example.com/base/telegram"


def test_webhook_requires_https_and_a_safe_secret() -> None:
    with pytest.raises(PydanticValidationError):
        _settings(webhook_url="http://bot.example.com")
    with pytest.raises(PydanticValidationError):
        _settings(webhook_url="https://bot.example.com", webhook_secret="contains space")
    with pytest.raises(PydanticValidationError):
        _settings(database_schema="cpm2-prod")
    with pytest.raises(PydanticValidationError):
        _settings(database_schema="CPM2")
