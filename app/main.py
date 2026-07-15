import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.bootstrap import build_container
from app.config.settings import get_settings
from app.presentation.handlers import router
from app.presentation.middleware import UserRegistrationMiddleware
from app.presentation.webhook import serve_webhook
from app.tasks.scheduler import build_scheduler
from app.utils.logging import configure_logging


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher["container"] = container
    dispatcher.update.outer_middleware(UserRegistrationMiddleware())
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
