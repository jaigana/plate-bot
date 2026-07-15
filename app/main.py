import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.exceptions import RedisError

from app.bootstrap import build_container
from app.config.settings import get_settings
from app.presentation.handlers import router
from app.presentation.middleware import UserRegistrationMiddleware
from app.presentation.webhook import serve_webhook
from app.tasks.scheduler import build_scheduler
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)


async def build_storage(redis_url: str) -> RedisStorage | MemoryStorage:
    """Use Redis when available, without making basic bot replies depend on it."""
    storage = RedisStorage.from_url(redis_url)
    try:
        await storage.redis.ping()
    except RedisError as error:
        await storage.close()
        logger.warning("redis_unavailable_using_memory_storage: %s", error)
        return MemoryStorage()
    return storage


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    storage = await build_storage(settings.redis_url)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher["container"] = container
    # `Update` objects do not expose `from_user`.  Register on the observers that
    # carry the actual Telegram event, otherwise a first `/start` reaches its
    # handler before the user exists in PostgreSQL.
    dispatcher.message.outer_middleware(UserRegistrationMiddleware())
    dispatcher.callback_query.outer_middleware(UserRegistrationMiddleware())
    dispatcher.pre_checkout_query.outer_middleware(UserRegistrationMiddleware())
    dispatcher.include_router(router)
    scheduler = build_scheduler(container, bot) if settings.scheduler_enabled else None
    if scheduler is not None:
        scheduler.start()
    try:
        if settings.webhook_url:
            await serve_webhook(dispatcher, bot, settings)
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await storage.close()
        await bot.session.close()
        await container.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
