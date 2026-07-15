from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.application.dto import Page, StatisticsDTO
from app.application.services.balance import BalanceBook
from app.domain.enums import (
    AuctionStatus,
    OwnershipOperation,
    PlateStatus,
    SaleStatus,
    TransactionType,
    UserStatus,
)
from app.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.domain.policies import MarketplacePolicy
from app.infrastructure.db.models import (
    Auction,
    AuditLog,
    Banner,
    BlacklistedSeries,
    BotCard,
    Notification,
    OwnershipHistory,
    Plate,
    PlatformLedger,
    Sale,
    User,
    UserBlock,
)
from app.infrastructure.db.session import UnitOfWork
from app.infrastructure.repositories.marketplace import (
    PlateRepository,
    SettingsRepository,
    UserRepository,
)

if TYPE_CHECKING:
    from app.application.services.auction import AuctionService


def utcnow() -> datetime:
    return datetime.now(UTC)


class AdminService:
    def __init__(self, uow: UnitOfWork, auctions: AuctionService | None = None) -> None:
        self._uow = uow
        self._auction_service = auctions
        self._users = UserRepository()
        self._plates = PlateRepository()
        self._settings = SettingsRepository()

    async def _admin(self, session: object, telegram_id: int) -> User:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
        if user is None or not user.is_admin:
            raise AuthorizationError("Недостаточно прав администратора.")
        return user

    async def ensure_admin(self, telegram_id: int) -> User:
        async with self._uow.transaction() as session:
            return await self._admin(session, telegram_id)

    @staticmethod
    def _audit(
        session: object,
        admin_id: int,
        action: str,
        entity: str,
        entity_id: int | None,
        payload: dict[str, object],
    ) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        session.add(
            AuditLog(
                admin_id=admin_id,
                action_type=action,
                entity_type=entity,
                entity_id=entity_id,
                payload=payload,
            )
        )

    async def statistics(self, telegram_id: int) -> StatisticsDTO:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
            plates = int(await session.scalar(select(func.count()).select_from(Plate)) or 0)
            sales = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Sale)
                    .where(Sale.status == SaleStatus.ACTIVE.value)
                )
                or 0
            )
            auctions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Auction)
                    .where(
                        Auction.status.in_(
                            [AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]
                        )
                    )
                )
                or 0
            )
            state_revenue = int(
                await session.scalar(select(func.coalesce(func.sum(PlatformLedger.amount), 0))) or 0
            )
            return StatisticsDTO(users, plates, sales, auctions, state_revenue)

    async def users(self, telegram_id: int, page: Page) -> list[User]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            return await self._users.list_users(session, page.offset, page.limit)

    async def user(self, telegram_id: int, user_id: int) -> User:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            target = await self._users.get(session, user_id)
            if target is None:
                raise NotFoundError("Пользователь не найден.")
            return target

    async def adjust_balance(
        self, telegram_id: int, target_id: int, delta: int, reason: str
    ) -> None:
        if not delta or not reason.strip():
            raise ValidationError("Укажите ненулевую сумму и причину изменения.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            targets = await self._users.lock_many(session, [admin.id, target_id])
            target = targets.get(target_id)
            if target is None:
                raise NotFoundError("Пользователь не найден.")
            if delta > 0:
                BalanceBook.credit(
                    session, target, delta, TransactionType.ADMIN_ADJUSTMENT, "admin", admin.id
                )
            else:
                BalanceBook.debit(
                    session, target, -delta, TransactionType.ADMIN_ADJUSTMENT, "admin", admin.id
                )
            self._audit(
                session,
                admin.id,
                "ADJUST_BALANCE",
                "user",
                target.id,
                {"delta": delta, "reason": reason},
            )

    async def set_blocked(
        self, telegram_id: int, target_id: int, blocked: bool, reason: str
    ) -> None:
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            target = await self._users.get(session, target_id, lock=True)
            if target is None:
                raise NotFoundError("Пользователь не найден.")
            if target.is_admin:
                raise ConflictError("Администратора нельзя блокировать через эту панель.")
            target.is_blocked = blocked
            target.status = UserStatus.BLOCKED.value if blocked else UserStatus.ACTIVE.value
            if blocked:
                if not reason.strip():
                    raise ValidationError("Для блокировки укажите причину.")
                session.add(UserBlock(user_id=target.id, admin_id=admin.id, reason=reason))
            else:
                block = await session.scalar(
                    select(UserBlock)
                    .where(UserBlock.user_id == target.id, UserBlock.lifted_at.is_(None))
                    .order_by(UserBlock.id.desc())
                    .with_for_update()
                )
                if block is not None:
                    block.lifted_at = utcnow()
            self._audit(
                session,
                admin.id,
                "BLOCK_USER" if blocked else "UNBLOCK_USER",
                "user",
                target.id,
                {"reason": reason},
            )

    async def set_setting(self, telegram_id: int, key: str, value: int) -> None:
        allowed = set(MarketplacePolicy.__dataclass_fields__)
        if key not in allowed:
            raise ValidationError("Неизвестная настройка платформы.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            current = await self._settings.policy(session)
            values = {
                name: getattr(current, name) for name in MarketplacePolicy.__dataclass_fields__
            }
            values[key] = value
            candidate = MarketplacePolicy(**values)
            if not (
                0 <= candidate.commission_percent <= 100
                and candidate.state_plate_price >= 1
                and candidate.auction_min_increment >= 1
                and 1 <= candidate.auction_max_duration_hours <= 24
                and 0 <= candidate.auction_sniping_minutes <= 60
                and 1 <= candidate.reserve_duration_minutes <= 60
                and candidate.inactive_days >= 1
                and candidate.max_sale_price >= 1
            ):
                raise ValidationError("Значение нарушает допустимые границы настройки.")
            await self._settings.set(session, key, value, admin.id)
            self._audit(
                session,
                admin.id,
                "SET_PLATFORM_SETTING",
                "setting",
                None,
                {"key": key, "value": value},
            )

    async def blacklist_series(
        self, telegram_id: int, country_code: str, series: str, add: bool
    ) -> None:
        code, normalized = country_code.upper(), series.strip().upper()
        if (
            code != "KZ"
            or not 2 <= len(normalized) <= 3
            or not normalized.isascii()
            or not normalized.isalpha()
        ):
            raise ValidationError("Допустима латинская серия КЗ из 2–3 букв.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            record = await session.scalar(
                select(BlacklistedSeries)
                .where(
                    BlacklistedSeries.country_code == code, BlacklistedSeries.series == normalized
                )
                .with_for_update()
            )
            if add and record is None:
                session.add(
                    BlacklistedSeries(country_code=code, series=normalized, created_by=admin.id)
                )
            if not add and record is not None:
                await session.delete(record)
            self._audit(
                session,
                admin.id,
                "ADD_BLACKLIST" if add else "REMOVE_BLACKLIST",
                "blacklist",
                None,
                {"series": normalized},
            )

    async def edit_card(
        self,
        telegram_id: int,
        card_id: str,
        title: str,
        description: str,
        image_file_id: str | None,
        banner_id: int | None,
        enabled: bool,
    ) -> BotCard:
        if not card_id or len(card_id) > 64 or not title.strip() or not description.strip():
            raise ValidationError("У карточки должны быть идентификатор, название и описание.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            card = await session.get(BotCard, card_id, with_for_update=True)
            if banner_id is not None and await session.get(Banner, banner_id) is None:
                raise NotFoundError("Баннер не найден.")
            if card is None:
                card = BotCard(
                    card_id=card_id,
                    title=title,
                    description=description,
                    image_file_id=image_file_id,
                    banner_id=banner_id,
                    enabled=enabled,
                    updated_by=admin.id,
                )
                session.add(card)
            else:
                card.title, card.description = title, description
                if image_file_id is not None:
                    card.image_file_id = image_file_id
                card.banner_id = banner_id
                card.enabled, card.updated_by = enabled, admin.id
            self._audit(session, admin.id, "EDIT_CARD", "bot_card", None, {"card_id": card_id})
            await session.flush()
            return card

    async def set_card_image(
        self, telegram_id: int, card_id: str, image_file_id: str | None
    ) -> BotCard:
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            card = await session.get(BotCard, card_id, with_for_update=True)
            if card is None:
                raise NotFoundError("Карточка не найдена.")
            card.image_file_id = image_file_id
            card.updated_by = admin.id
            self._audit(session, admin.id, "SET_CARD_IMAGE", "bot_card", None, {"card_id": card_id})
            await session.flush()
            return card

    async def cards(self, telegram_id: int, page: Page) -> list[BotCard]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            rows = await session.scalars(
                select(BotCard).order_by(BotCard.card_id).offset(page.offset).limit(page.limit)
            )
            return list(rows)

    async def create_banner(
        self,
        telegram_id: int,
        title: str,
        description: str,
        priority: int,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> Banner:
        if not title.strip() or not description.strip() or not -10_000 <= priority <= 10_000:
            raise ValidationError("Укажите название, описание и приоритет баннера.")
        if start_at is not None and end_at is not None and end_at <= start_at:
            raise ValidationError("Время окончания баннера должно быть позже времени начала.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            banner = Banner(
                title=title.strip(),
                description=description.strip(),
                priority=priority,
                start_at=start_at,
                end_at=end_at,
            )
            session.add(banner)
            await session.flush()
            self._audit(session, admin.id, "CREATE_BANNER", "banner", banner.id, {})
            return banner

    async def set_banner_image(
        self, telegram_id: int, banner_id: int, image_file_id: str | None
    ) -> Banner:
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            banner = await session.get(Banner, banner_id, with_for_update=True)
            if banner is None:
                raise NotFoundError("Баннер не найден.")
            banner.image_file_id = image_file_id
            self._audit(session, admin.id, "SET_BANNER_IMAGE", "banner", banner.id, {})
            return banner

    async def banners(self, telegram_id: int, page: Page) -> list[Banner]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            rows = await session.scalars(
                select(Banner)
                .order_by(Banner.priority.desc(), Banner.id.desc())
                .offset(page.offset)
                .limit(page.limit)
            )
            return list(rows)

    async def plates(self, telegram_id: int, page: Page) -> list[Plate]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            return await self._plates.list_all(session, page.offset, page.limit)

    async def plate(self, telegram_id: int, plate_id: int) -> Plate:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            plate = await self._plates.get(session, plate_id)
            if plate is None:
                raise NotFoundError("Номер не найден.")
            return plate

    async def auctions(self, telegram_id: int, page: Page) -> list[Auction]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            rows = await session.scalars(
                select(Auction).order_by(Auction.id.desc()).offset(page.offset).limit(page.limit)
            )
            return list(rows)

    async def auction(self, telegram_id: int, auction_id: int) -> Auction:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            auction = await session.get(Auction, auction_id)
            if auction is None:
                raise NotFoundError("Аукцион не найден.")
            return auction

    async def finance(self, telegram_id: int, page: Page) -> tuple[int, list[PlatformLedger]]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            total = int(
                await session.scalar(select(func.coalesce(func.sum(PlatformLedger.amount), 0))) or 0
            )
            rows = await session.scalars(
                select(PlatformLedger)
                .order_by(PlatformLedger.id.desc())
                .offset(page.offset)
                .limit(page.limit)
            )
            return total, list(rows)

    async def settings_values(self, telegram_id: int) -> dict[str, int]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            policy = await self._settings.policy(session)
            return {name: getattr(policy, name) for name in MarketplacePolicy.__dataclass_fields__}

    async def blacklist(self, telegram_id: int) -> list[BlacklistedSeries]:
        async with self._uow.transaction() as session:
            await self._admin(session, telegram_id)
            rows = await session.scalars(
                select(BlacklistedSeries)
                .where(BlacklistedSeries.country_code == "KZ")
                .order_by(BlacklistedSeries.series)
            )
            return list(rows)

    async def remove_sale(self, telegram_id: int, plate_id: int, reason: str) -> None:
        if not reason.strip():
            raise ValidationError("Укажите причину снятия с продажи.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            plate = await self._plates.get(session, plate_id, lock=True)
            if plate is None:
                raise NotFoundError("Номер не найден.")
            sale = await self._plates.active_sale(session, plate.id, lock=True)
            if sale is None:
                raise NotFoundError("Активная продажа не найдена.")
            if sale.status == SaleStatus.RESERVED.value:
                raise ConflictError("Нельзя снять продажу, пока ожидается оплата по счёту.")
            sale.status = SaleStatus.CANCELLED.value
            plate.status = PlateStatus.OWNED.value
            self._audit(session, admin.id, "REMOVE_SALE", "plate", plate.id, {"reason": reason})

    async def force_cancel_auction(self, telegram_id: int, auction_id: int, reason: str) -> None:
        if not reason.strip():
            raise ValidationError("Укажите причину отмены аукциона.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            auction = await session.get(Auction, auction_id, with_for_update=True)
            if auction is None:
                raise NotFoundError("Аукцион не найден.")
            if auction.status not in {AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value}:
                raise ConflictError("Аукцион уже закрыт.")
            plate = await self._plates.get(session, auction.plate_id, lock=True)
            if plate is None:
                raise NotFoundError("Номер аукциона не найден.")
            await self._clear_listing(session, plate)
            plate.status = PlateStatus.OWNED.value
            self._audit(
                session, admin.id, "CANCEL_AUCTION", "auction", auction.id, {"reason": reason}
            )

    async def force_finish_auction(self, telegram_id: int, auction_id: int) -> bool:
        if self._auction_service is None:
            raise RuntimeError("Сервис аукционов не подключён.")
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            admin_id = admin.id
        completed = await self._auction_service.finish(auction_id, force=True)
        if completed:
            async with self._uow.transaction() as session:
                await self._admin(session, telegram_id)
                self._audit(session, admin_id, "FORCE_FINISH_AUCTION", "auction", auction_id, {})
        return completed

    async def force_return_to_state(self, telegram_id: int, plate_id: int, reason: str) -> None:
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            plate = await self._plates.get(session, plate_id, lock=True)
            if plate is None:
                raise NotFoundError("Номер не найден.")
            await self._clear_listing(session, plate)
            old_owner = plate.owner_id
            plate.owner_id = None
            plate.status = PlateStatus.STATE_SALE.value
            plate.reserved_by = None
            plate.reserved_until = None
            session.add(
                OwnershipHistory(
                    plate_id=plate.id,
                    old_owner_id=old_owner,
                    new_owner_id=None,
                    operation_type=OwnershipOperation.CONFISCATION.value,
                    price=None,
                )
            )
            self._audit(session, admin.id, "RETURN_TO_STATE", "plate", plate.id, {"reason": reason})

    async def force_transfer(
        self, telegram_id: int, plate_id: int, target_id: int, reason: str
    ) -> None:
        async with self._uow.transaction() as session:
            admin = await self._admin(session, telegram_id)
            plate = await self._plates.get(session, plate_id, lock=True)
            target = await self._users.get(session, target_id, lock=True)
            if plate is None or target is None:
                raise NotFoundError("Номер или пользователь не найден.")
            if target.is_blocked:
                raise ConflictError("Нельзя передать номер заблокированному пользователю.")
            await self._clear_listing(session, plate)
            old_owner = plate.owner_id
            plate.owner_id = target.id
            plate.status = PlateStatus.OWNED.value
            plate.reserved_by = None
            plate.reserved_until = None
            session.add(
                OwnershipHistory(
                    plate_id=plate.id,
                    old_owner_id=old_owner,
                    new_owner_id=target.id,
                    operation_type=OwnershipOperation.ADMIN_TRANSFER.value,
                    price=None,
                )
            )
            self._audit(
                session,
                admin.id,
                "FORCE_TRANSFER",
                "plate",
                plate.id,
                {"target_id": target.id, "reason": reason},
            )

    async def _clear_listing(self, session: object, plate: Plate) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        sale = await self._plates.active_sale(session, plate.id, lock=True)
        if sale is not None:
            if sale.status == SaleStatus.RESERVED.value:
                raise ConflictError("Нельзя изменить номер, пока ожидается оплата по счёту.")
            sale.status = SaleStatus.CANCELLED.value
        auction = await session.scalar(
            select(Auction)
            .where(
                Auction.plate_id == plate.id,
                Auction.status.in_([AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]),
            )
            .with_for_update()
        )
        if auction is not None:
            if auction.highest_bidder_id is not None:
                bidder = await self._users.get(session, auction.highest_bidder_id, lock=True)
                if bidder is not None:
                    BalanceBook.unfreeze(
                        session, bidder, auction.current_price, "auction", auction.id
                    )
                    session.add(
                        Notification(
                            user_id=bidder.id,
                            type="AUCTION_CANCELLED",
                            payload={"auction_id": auction.id},
                        )
                    )
            auction.status = AuctionStatus.CANCELLED.value
            auction.is_cancelled = True

    async def confiscate_inactive(self) -> int:
        """Daily policy job: preserve balances, move all assets to State, retain provenance."""
        async with self._uow.transaction() as session:
            policy = await self._settings.policy(session)
            cutoff = utcnow() - timedelta(days=policy.inactive_days)
            ids = list(
                await session.scalars(
                    select(User.id).where(
                        User.last_activity < cutoff,
                        User.is_admin.is_(False),
                        User.is_blocked.is_(False),
                        User.status == UserStatus.ACTIVE.value,
                    )
                )
            )
        processed = 0
        for user_id in ids:
            async with self._uow.transaction() as session:
                user = await self._users.get(session, user_id, lock=True)
                if user is None or user.status != UserStatus.ACTIVE.value:
                    continue
                policy = await self._settings.policy(session)
                if user.last_activity >= utcnow() - timedelta(days=policy.inactive_days):
                    continue
                plates = list(
                    await session.scalars(
                        select(Plate).where(Plate.owner_id == user.id).with_for_update()
                    )
                )
                for plate in plates:
                    await self._clear_listing(session, plate)
                    plate.owner_id = None
                    plate.status = PlateStatus.STATE_SALE.value
                    plate.reserved_by = None
                    plate.reserved_until = None
                    session.add(
                        OwnershipHistory(
                            plate_id=plate.id,
                            old_owner_id=user.id,
                            new_owner_id=None,
                            operation_type=OwnershipOperation.CONFISCATION.value,
                            price=None,
                        )
                    )
                user.status = UserStatus.INACTIVE.value
                processed += 1
        return processed

    async def queue_inactivity_warnings(self) -> int:
        """Create one auditable warning shortly before automatic confiscation."""
        async with self._uow.transaction() as session:
            policy = await self._settings.policy(session)
            now = utcnow()
            warning_after = now - timedelta(days=max(policy.inactive_days - 30, 0))
            inactive_after = now - timedelta(days=policy.inactive_days)
            warned = select(Notification.user_id).where(
                Notification.type == "ACCOUNT_INACTIVE_WARNING"
            )
            users = list(
                await session.scalars(
                    select(User).where(
                        User.status == UserStatus.ACTIVE.value,
                        User.is_admin.is_(False),
                        User.is_blocked.is_(False),
                        User.last_activity <= warning_after,
                        User.last_activity > inactive_after,
                        User.id.not_in(warned),
                    )
                )
            )
            for user in users:
                session.add(
                    Notification(
                        user_id=user.id,
                        type="ACCOUNT_INACTIVE_WARNING",
                        payload={"inactive_days": policy.inactive_days},
                    )
                )
            return len(users)
