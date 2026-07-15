from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from app.application.dto import BackupDTO
from app.domain.errors import NotFoundError
from app.infrastructure.db.models import Backup, Notification, User
from app.infrastructure.db.session import UnitOfWork
from app.infrastructure.repositories.marketplace import SettingsRepository


class NotificationService:
    def __init__(self, uow: UnitOfWork, bot: Bot) -> None:
        self._uow = uow
        self._bot = bot
        self._settings = SettingsRepository()

    @staticmethod
    def _text(notification: Notification) -> str:
        payload = notification.payload
        messages = {
            "AUCTION_BID_PLACED": f"🔔 Новая ставка: ⭐{payload.get('amount', '')}.",
            "AUCTION_WON": f"🏆 Вы выиграли аукцион {payload.get('plate_number', '')}.",
            "AUCTION_OUTBID": "⚠ Вашу ставку перебили.",
            "AUCTION_FINISHED": "🔨 Аукцион завершён.",
            "SALE_COMPLETED": f"💰 Ваш номер продан за ⭐{payload.get('price', '')}.",
            "PLATE_PURCHASED": f"✅ Номер {payload.get('plate_number', '')} теперь ваш.",
            "BALANCE_TOPUP": f"⭐ Баланс пополнен на ⭐{payload.get('amount', '')}.",
            "ACCOUNT_INACTIVE_WARNING": "⚠ Ваш аккаунт скоро будет признан неактивным.",
            "AUCTION_CANCELLED": "⚠ Аукцион отменён; ваша ставка разморожена.",
        }
        return messages.get(notification.type, "Системное уведомление площадки.")

    async def deliver_pending(self, limit: int = 100) -> int:
        async with self._uow.transaction() as session:
            notifications = await self._settings.pending_notifications(session, limit)
            work = [(item.id, item.user_id, self._text(item)) for item in notifications]
        delivered = 0
        for notification_id, user_id, text in work:
            try:
                user_id = int(user_id)
                async with self._uow.transaction() as session:
                    notification = await session.get(
                        Notification, notification_id, with_for_update=True
                    )
                    if notification is None or notification.is_sent:
                        continue
                    user = await session.get(User, user_id)
                    if user is None:
                        continue
                    telegram_id = user.telegram_id
                await self._bot.send_message(telegram_id, text)
            except TelegramRetryAfter:
                continue
            except TelegramForbiddenError:
                # A blocked bot cannot deliver; keep an auditable unsent notification for administrators.
                continue
            else:
                async with self._uow.transaction() as session:
                    notification = await session.get(
                        Notification, notification_id, with_for_update=True
                    )
                    if notification is not None and not notification.is_sent:
                        notification.is_sent = True
                        notification.sent_at = datetime.now(UTC)
                        delivered += 1
        return delivered


class BackupService:
    def __init__(self, uow: UnitOfWork, database_url: str, backup_dir: Path) -> None:
        self._uow = uow
        self._database_url = database_url
        self._backup_dir = backup_dir

    def _pg_dump_args(self, output: Path) -> tuple[list[str], dict[str, str]]:
        parsed = urlparse(self._database_url.replace("postgresql+asyncpg", "postgresql"))
        if not parsed.hostname or not parsed.path:
            raise ValueError("DATABASE_URL must point to PostgreSQL")
        args = [
            "pg_dump",
            "--format=plain",
            "--no-owner",
            "--no-privileges",
            "--host",
            parsed.hostname,
            "--port",
            str(parsed.port or 5432),
            "--username",
            unquote(parsed.username or ""),
            "--file",
            str(output),
            parsed.path.lstrip("/"),
        ]
        environment = dict(os.environ)
        if parsed.password:
            environment["PGPASSWORD"] = unquote(parsed.password)
        return args, environment

    async def create(self, created_by: int | None = None) -> BackupDTO:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y_%m_%d")
        output = self._backup_dir / f"backup_{stamp}.sql"
        if output.exists():
            output = (
                self._backup_dir / f"backup_{datetime.now(UTC).strftime('%Y_%m_%d_%H%M%S')}.sql"
            )
        args, environment = self._pg_dump_args(output)
        process = await asyncio.create_subprocess_exec(
            *args,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump failed: {stderr.decode('utf-8', 'replace')[:500]}")
        size = output.stat().st_size
        async with self._uow.transaction() as session:
            record = Backup(
                file_name=output.name,
                storage_path=str(output.resolve()),
                size_bytes=size,
                created_by=created_by,
            )
            session.add(record)
            await session.flush()
            created = record.created_at
        return BackupDTO(output.name, str(output.resolve()), size, created)

    async def latest(self) -> Backup:
        async with self._uow.transaction() as session:
            backup = await session.scalar(
                select(Backup).order_by(Backup.created_at.desc()).limit(1)
            )
            if backup is None:
                raise NotFoundError("Резервные копии ещё не создавались.")
            return backup
