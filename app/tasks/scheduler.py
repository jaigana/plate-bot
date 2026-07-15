from collections.abc import Awaitable, Callable

import structlog
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.application.services.maintenance import NotificationService
from app.bootstrap import Container

logger = structlog.get_logger(__name__)


def _guard(name: str, task: Callable[[], Awaitable[object]]) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        try:
            result = await task()
            logger.info("scheduled_task_completed", task=name, result=str(result))
        except Exception:
            logger.exception("scheduled_task_failed", task=name)

    return run


def build_scheduler(container: Container, bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    notifications = NotificationService(container.uow, bot)

    async def backup() -> str:
        item = await container.backups.create()
        await bot.send_document(
            container.settings.owner_telegram_id, document=item.storage_path, caption=item.file_name
        )
        return item.file_name

    scheduler.add_job(
        _guard("expire_reservations", container.marketplace.expire_pending_payments),
        "interval",
        minutes=1,
        id="expire_reservations",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("finish_auctions", container.auctions.finish_due),
        "interval",
        minutes=1,
        id="finish_auctions",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("anti_sniping", container.auctions.apply_pending_anti_sniping),
        "interval",
        minutes=1,
        id="anti_sniping",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("notifications", notifications.deliver_pending),
        "interval",
        hours=1,
        id="notifications",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("inactivity_warnings", container.admin.queue_inactivity_warnings),
        "interval",
        hours=1,
        id="inactivity_warnings",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("inactive_accounts", container.admin.confiscate_inactive),
        CronTrigger(hour=2, minute=5),
        id="inactive_accounts",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _guard("backup", backup),
        CronTrigger(hour=3, minute=0),
        id="backup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
