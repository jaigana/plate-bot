from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    database_url: str
    redis_url: str
    admin_telegram_ids: frozenset[int] = Field(default_factory=frozenset)
    owner_telegram_id: int
    log_level: str = "INFO"
    webhook_url: str | None = None
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr | None = None
    webhook_host: str = "0.0.0.0"
    webhook_port: int = Field(default=8080, ge=1, le=65535)
    scheduler_enabled: bool = True
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_public_base_url: str | None = None

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: str | list[int] | set[int]) -> frozenset[int]:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return frozenset(value)

    @field_validator("webhook_url", "webhook_secret", mode="before")
    @classmethod
    def empty_webhook_values_are_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_webhook(self) -> Settings:
        if self.webhook_url is not None and not self.webhook_url.startswith("https://"):
            raise ValueError("WEBHOOK_URL must use HTTPS")
        if not self.webhook_path.startswith("/"):
            raise ValueError("WEBHOOK_PATH must start with '/'")
        if self.webhook_secret is not None:
            secret = self.webhook_secret.get_secret_value()
            if not 1 <= len(secret) <= 256 or not all(
                char.isascii() and (char.isalnum() or char in "_-") for char in secret
            ):
                raise ValueError("WEBHOOK_SECRET contains unsupported characters")
        return self

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgres://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
