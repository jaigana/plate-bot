from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from uuid import uuid4

from aiogram import Bot

from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class StoredImage:
    reference: str
    object_key: str | None


class ImageStorage:
    """Stores CMS images in S3 when configured, otherwise retains Telegram's file ID.

    A public S3 base URL is required because Telegram fetches remote media itself when an
    image is rendered in a card. This keeps S3 optional for small deployments while
    allowing durable, provider-neutral asset storage in production.
    """

    def __init__(self, settings: Settings) -> None:
        self._endpoint_url = settings.s3_endpoint_url
        self._bucket = settings.s3_bucket
        self._access_key = (
            settings.s3_access_key.get_secret_value() if settings.s3_access_key else None
        )
        self._secret_key = (
            settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None
        )
        self._public_base_url = (
            settings.s3_public_base_url.rstrip("/") if settings.s3_public_base_url else None
        )

    @property
    def enabled(self) -> bool:
        return all(
            (
                self._endpoint_url,
                self._bucket,
                self._access_key,
                self._secret_key,
                self._public_base_url,
            )
        )

    async def persist_telegram_photo(self, bot: Bot, file_id: str, prefix: str) -> StoredImage:
        if not self.enabled:
            return StoredImage(reference=file_id, object_key=None)
        file = await bot.get_file(file_id)
        if not file.file_path:
            raise RuntimeError("Telegram did not return a file path for the image.")
        buffer = BytesIO()
        await bot.download_file(file.file_path, destination=buffer)
        payload = buffer.getvalue()
        if not payload:
            raise RuntimeError("The uploaded image is empty.")
        key = f"{prefix.strip('/')}/{uuid4().hex}.jpg"
        await asyncio.to_thread(self._upload, key, payload)
        assert self._public_base_url is not None
        return StoredImage(reference=f"{self._public_base_url}/{key}", object_key=key)

    def _upload(self, key: str, payload: bytes) -> None:
        if not self.enabled:
            return
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=payload,
            ContentType="image/jpeg",
            CacheControl="public, max-age=31536000, immutable",
        )
