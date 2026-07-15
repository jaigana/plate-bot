from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, delete, desc, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AuctionStatus, PaymentStatus, PlateStatus, SaleStatus, UserStatus
from app.domain.policies import MarketplacePolicy
from app.infrastructure.db.models import (
    Auction,
    Bid,
    BlacklistedSeries,
    BotCard,
    Notification,
    PaymentIntent,
    Plate,
    PlatformSetting,
    Sale,
    StateEmissionReservation,
    User,
)


class UserRepository:
    async def create_or_touch(
        self,
        session: AsyncSession,
        *,
        telegram_id: int,
        username: str | None,
        telegram_name: str,
        is_admin: bool,
        now: datetime,
    ) -> User:
        statement = insert(User).values(
            telegram_id=telegram_id,
            username=username,
            telegram_name=telegram_name,
            is_admin=is_admin,
            registered_at=now,
            last_activity=now,
            screen_stack=[],
        )
        statement = statement.on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={
                "username": statement.excluded.username,
                "telegram_name": statement.excluded.telegram_name,
                "last_activity": now,
                "is_admin": User.is_admin.op("OR")(is_admin),
            },
        ).returning(User.id)
        user_id = await session.scalar(statement)
        assert user_id is not None
        user = await self.get(session, user_id, lock=True)
        assert user is not None
        if user.status == UserStatus.INACTIVE.value and not user.is_blocked:
            user.status = UserStatus.ACTIVE.value
        if is_admin:
            user.status = UserStatus.ADMIN.value
        return user

    async def get_by_telegram_id(
        self, session: AsyncSession, telegram_id: int, *, lock: bool = False
    ) -> User | None:
        query = select(User).where(User.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def get(self, session: AsyncSession, user_id: int, *, lock: bool = False) -> User | None:
        query = select(User).where(User.id == user_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def lock_many(self, session: AsyncSession, user_ids: list[int]) -> dict[int, User]:
        rows = await session.scalars(
            select(User).where(User.id.in_(sorted(set(user_ids)))).with_for_update()
        )
        return {user.id: user for user in rows}

    async def list_users(self, session: AsyncSession, offset: int, limit: int) -> list[User]:
        rows = await session.scalars(
            select(User).order_by(desc(User.id)).offset(offset).limit(limit)
        )
        return list(rows)

    async def count(self, session: AsyncSession) -> int:
        return int(await session.scalar(select(func.count()).select_from(User)) or 0)

    async def set_app_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
        message_id: int,
        image_file_id: str | None,
    ) -> None:
        user = await self.get(session, user_id, lock=True)
        if user is not None:
            user.app_chat_id = chat_id
            user.app_message_id = message_id
            user.app_image_file_id = image_file_id


class PlateRepository:
    async def get(
        self, session: AsyncSession, plate_id: int, *, lock: bool = False
    ) -> Plate | None:
        query = select(Plate).where(Plate.id == plate_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def get_by_number(
        self, session: AsyncSession, country_code: str, plate_number: str, *, lock: bool = False
    ) -> Plate | None:
        query = select(Plate).where(
            Plate.country_code == country_code, Plate.plate_number == plate_number
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def search(
        self,
        session: AsyncSession,
        query_text: str,
        country_code: str | None,
        offset: int,
        limit: int,
    ) -> list[Plate]:
        query: Select[tuple[Plate]] = select(Plate).where(
            Plate.plate_number.ilike(f"%{query_text}%"),
            Plate.status.in_([PlateStatus.FIXED_SALE.value, PlateStatus.AUCTION.value]),
        )
        if country_code:
            query = query.where(Plate.country_code == country_code)
        rows = await session.scalars(
            query.order_by(desc(Plate.created_at)).offset(offset).limit(limit)
        )
        return list(rows)

    async def market(
        self, session: AsyncSession, country_code: str | None, sort: str, offset: int, limit: int
    ) -> list[Plate]:
        query: Select[tuple[Plate]] = select(Plate).where(
            Plate.status.in_([PlateStatus.FIXED_SALE.value, PlateStatus.AUCTION.value])
        )
        if country_code:
            query = query.where(Plate.country_code == country_code)
        if sort == "new":
            query = query.order_by(desc(Plate.created_at))
        elif sort == "cheap":
            query = (
                query.outerjoin(
                    Sale,
                    (Sale.plate_id == Plate.id) & (Sale.status == SaleStatus.ACTIVE.value),
                )
                .outerjoin(
                    Auction,
                    Auction.plate_id == Plate.id,
                )
                .order_by(func.coalesce(Sale.price, Auction.current_price), desc(Plate.created_at))
            )
        elif sort == "rare":
            query = query.order_by(func.length(Plate.plate_number), Plate.plate_number)
        else:
            query = query.order_by(desc(Plate.created_at))
        rows = await session.scalars(query.offset(offset).limit(limit))
        return list(rows)

    async def by_owner(
        self, session: AsyncSession, owner_id: int, offset: int, limit: int
    ) -> list[Plate]:
        rows = await session.scalars(
            select(Plate)
            .where(Plate.owner_id == owner_id)
            .order_by(desc(Plate.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(rows)

    async def list_all(self, session: AsyncSession, offset: int, limit: int) -> list[Plate]:
        rows = await session.scalars(
            select(Plate).order_by(desc(Plate.id)).offset(offset).limit(limit)
        )
        return list(rows)

    async def active_sale(
        self, session: AsyncSession, plate_id: int, *, lock: bool = False
    ) -> Sale | None:
        query = select(Sale).where(
            Sale.plate_id == plate_id,
            Sale.status.in_([SaleStatus.ACTIVE.value, SaleStatus.RESERVED.value]),
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def blacklist(self, session: AsyncSession, country_code: str) -> set[str]:
        values = await session.scalars(
            select(BlacklistedSeries.series).where(BlacklistedSeries.country_code == country_code)
        )
        return {value.upper() for value in values}


class AuctionRepository:
    async def get(
        self, session: AsyncSession, auction_id: int, *, lock: bool = False
    ) -> Auction | None:
        query = select(Auction).where(Auction.id == auction_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def list_active(self, session: AsyncSession, offset: int, limit: int) -> list[Auction]:
        rows = await session.scalars(
            select(Auction)
            .where(Auction.status.in_([AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]))
            .order_by(Auction.ends_at)
            .offset(offset)
            .limit(limit)
        )
        return list(rows)

    async def list_all(self, session: AsyncSession, offset: int, limit: int) -> list[Auction]:
        rows = await session.scalars(
            select(Auction).order_by(desc(Auction.id)).offset(offset).limit(limit)
        )
        return list(rows)

    async def bids(
        self, session: AsyncSession, auction_id: int, offset: int, limit: int
    ) -> list[Bid]:
        rows = await session.scalars(
            select(Bid)
            .where(Bid.auction_id == auction_id)
            .order_by(desc(Bid.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(rows)

    async def due_ids(self, session: AsyncSession, now: datetime) -> list[int]:
        values = await session.scalars(
            select(Auction.id).where(
                Auction.status.in_([AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]),
                Auction.ends_at <= now,
            )
        )
        return list(values)

    async def pending_sniping_bid_ids(self, session: AsyncSession) -> list[int]:
        values = await session.scalars(
            select(Bid.id)
            .join(Auction, Auction.id == Bid.auction_id)
            .where(
                Bid.anti_sniping_applied.is_(False),
                Auction.status.in_([AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value]),
            )
        )
        return list(values)


class PaymentRepository:
    async def get_by_payload(
        self, session: AsyncSession, payload: str, *, lock: bool = False
    ) -> PaymentIntent | None:
        query = select(PaymentIntent).where(PaymentIntent.payload == payload)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def get_by_charge_id(
        self,
        session: AsyncSession,
        charge_id: str,
        *,
        lock: bool = False,
    ) -> PaymentIntent | None:
        query = select(PaymentIntent).where(PaymentIntent.telegram_payment_charge_id == charge_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def pending_expired(self, session: AsyncSession, now: datetime) -> list[PaymentIntent]:
        rows = await session.scalars(
            select(PaymentIntent).where(
                PaymentIntent.status == PaymentStatus.PENDING.value, PaymentIntent.expires_at <= now
            )
        )
        return list(rows)

    async def state_reservation(
        self, session: AsyncSession, country_code: str, plate_number: str, *, lock: bool = False
    ) -> StateEmissionReservation | None:
        query = select(StateEmissionReservation).where(
            StateEmissionReservation.country_code == country_code,
            StateEmissionReservation.plate_number == plate_number,
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def remove_expired_state_reservations(self, session: AsyncSession, now: datetime) -> None:
        await session.execute(
            delete(StateEmissionReservation).where(StateEmissionReservation.expires_at <= now)
        )


class SettingsRepository:
    defaults = MarketplacePolicy()

    async def policy(self, session: AsyncSession) -> MarketplacePolicy:
        rows = await session.scalars(select(PlatformSetting))
        values = {item.key: item.value for item in rows}
        kwargs: dict[str, int] = {}
        for name in MarketplacePolicy.__dataclass_fields__:
            if name in values:
                kwargs[name] = int(values[name])
        return MarketplacePolicy(**kwargs)

    async def set(self, session: AsyncSession, key: str, value: int, admin_id: int) -> None:
        record = await session.get(PlatformSetting, key, with_for_update=True)
        if record is None:
            session.add(PlatformSetting(key=key, value=str(value), updated_by=admin_id))
        else:
            record.value = str(value)
            record.updated_by = admin_id

    async def cards(self, session: AsyncSession) -> list[BotCard]:
        rows = await session.scalars(select(BotCard).where(BotCard.enabled.is_(True)))
        return list(rows)

    async def get_card(self, session: AsyncSession, card_id: str) -> BotCard | None:
        return await session.get(BotCard, card_id)

    async def pending_notifications(self, session: AsyncSession, limit: int) -> list[Notification]:
        rows = await session.scalars(
            select(Notification)
            .where(Notification.is_sent.is_(False))
            .order_by(Notification.id)
            .limit(limit)
        )
        return list(rows)

    async def expired_reservations(self, session: AsyncSession, now: datetime) -> list[Plate]:
        rows = await session.scalars(
            select(Plate).where(Plate.reserved_until.is_not(None), Plate.reserved_until <= now)
        )
        return list(rows)

    async def update_activity(self, session: AsyncSession, user_id: int, now: datetime) -> None:
        await session.execute(update(User).where(User.id == user_id).values(last_activity=now))
