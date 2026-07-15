from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.config.settings import Settings


def webhook_endpoint(settings: Settings) -> str:
    """Build the public Telegram endpoint without accepting a path traversal-like URL."""
    assert settings.webhook_url is not None
    return f"{settings.webhook_url.rstrip('/')}{settings.webhook_path}"


async def serve_webhook(dispatcher: Dispatcher, bot: Bot, settings: Settings) -> None:
    """Run a graceful aiogram aiohttp webhook server until the process is cancelled."""
    if settings.webhook_url is None:
        raise ValueError("WEBHOOK_URL is required in webhook mode")
    application = web.Application()
    application.router.add_get("/healthz", _healthcheck)
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=(
            settings.webhook_secret.get_secret_value() if settings.webhook_secret is not None else None
        ),
    ).register(application, path=settings.webhook_path)
    setup_application(application, dispatcher, bot=bot)

    runner = web.AppRunner(application, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
    try:
        await bot.set_webhook(
            url=webhook_endpoint(settings),
            secret_token=(
                settings.webhook_secret.get_secret_value()
                if settings.webhook_secret is not None
                else None
            ),
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        await site.start()
        await asyncio.Future[None]()
    finally:
        await runner.cleanup()


async def _healthcheck(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
