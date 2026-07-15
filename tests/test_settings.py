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


def test_railway_port_is_used_when_webhook_port_is_not_set() -> None:
    assert _settings(PORT=9090).webhook_port == 9090


def test_railway_public_domain_is_normalized_to_https() -> None:
    assert _settings(webhook_url="plates-production.up.railway.app").webhook_url == (
        "https://plates-production.up.railway.app"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (123456789, frozenset({123456789})),
        ("123456789", frozenset({123456789})),
        ("123456789, 987654321", frozenset({123456789, 987654321})),
        ([123456789, 987654321], frozenset({123456789, 987654321})),
    ],
)
def test_admin_ids_support_single_ids_csv_and_collections(
    value: object, expected: frozenset[int]
) -> None:
    assert _settings(admin_telegram_ids=value).admin_telegram_ids == expected


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
    with pytest.raises(PydanticValidationError):
        _settings(admin_telegram_ids=0)
