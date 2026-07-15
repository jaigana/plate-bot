from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.application.dto import TelegramUserData
from app.bootstrap import Container


class UserRegistrationMiddleware(BaseMiddleware):
    """Synchronises Telegram identity before a handler reaches a use case."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        container: Container = data["container"]
        from_user = getattr(event, "from_user", None)
        if from_user is not None and not from_user.is_bot:
            await container.marketplace.register_user(
                TelegramUserData(
                    telegram_id=from_user.id,
                    username=from_user.username,
                    full_name=from_user.full_name,
                ),
                container.settings.admin_telegram_ids,
            )
        return await handler(event, data)
