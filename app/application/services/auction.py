from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.application.dto import Page
from app.application.services.balance import BalanceBook
from app.domain.enums import (
    AuctionStatus,
    NotificationType,
    OwnershipOperation,
    PlateStatus,
    TransactionType,
)
from app.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import Auction, Bid, Notification, OwnershipHistory
from app.infrastructure.db.session import UnitOfWork
from app.infrastructure.repositories.marketplace import (
    AuctionRepository,
    PlateRepository,
    SettingsRepository,
    UserRepository,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class AuctionService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._auctions = AuctionRepository()
        self._plates = PlateRepository()
        self._users = UserRepository()
        self._settings = SettingsRepository()

    async def get(self, auction_id: int) -> Auction:
        async with self._uow.transaction() as session:
            auction = await self._auctions.get(session, auction_id)
            if auction is None:
                raise NotFoundError("Аукцион не найден.")
            return auction

    async def list_active(self, page: Page) -> list[Auction]:
        async with self._uow.transaction() as session:
            return await self._auctions.list_active(session, page.offset, page.limit)

    async def bid_history(self, auction_id: int, page: Page) -> list[Bid]:
        async with self._uow.transaction() as session:
            return await self._auctions.bids(session, auction_id, page.offset, page.limit)

    async def create(
        self, telegram_id: int, plate_id: int, start_price: int, duration_hours: int
    ) -> Auction:
        now = utcnow()
        async with self._uow.transaction() as session:
            seller = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            plate = await self._plates.get(session, plate_id, lock=True)
            if seller is None or plate is None:
                raise NotFoundError("Пользователь или номер не найден.")
            if plate.owner_id != seller.id:
                raise AuthorizationError("Создать аукцион может только владелец номера.")
            if seller.is_blocked:
                raise AuthorizationError("Заблокированный аккаунт не может создавать аукционы.")
            if plate.status != PlateStatus.OWNED.value:
                raise ConflictError("Номер нельзя выставить на аукцион в текущем статусе.")
            policy = await self._settings.policy(session)
            policy.validate_auction(start_price, duration_hours)
            auction = Auction(
                plate_id=plate.id,
                seller_id=seller.id,
                start_price=start_price,
                current_price=start_price,
                started_at=now,
                ends_at=now + timedelta(hours=duration_hours),
                status=AuctionStatus.ACTIVE.value,
            )
            session.add(auction)
            plate.status = PlateStatus.AUCTION.value
            await session.flush()
            return auction

    async def cancel(self, telegram_id: int, auction_id: int) -> None:
        async with self._uow.transaction() as session:
            seller = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            auction = await self._auctions.get(session, auction_id, lock=True)
            if seller is None or auction is None:
                raise NotFoundError("Пользователь или аукцион не найден.")
            if auction.seller_id != seller.id:
                raise AuthorizationError("Отменить аукцион может только продавец.")
            if auction.status not in {AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value}:
                raise ConflictError("Аукцион уже закрыт.")
            bid_count = await session.scalar(
                select(func.count()).select_from(Bid).where(Bid.auction_id == auction.id)
            )
            if bid_count:
                raise ConflictError("Аукцион со ставками отменить нельзя.")
            plate = await self._plates.get(session, auction.plate_id, lock=True)
            if plate is None:
                raise NotFoundError("Номер аукциона не найден.")
            auction.status = AuctionStatus.CANCELLED.value
            auction.is_cancelled = True
            plate.status = PlateStatus.OWNED.value

    async def place_bid(self, telegram_id: int, auction_id: int, amount: int) -> Auction:
        now = utcnow()
        async with self._uow.transaction() as session:
            auction = await self._auctions.get(session, auction_id, lock=True)
            if auction is None:
                raise NotFoundError("Аукцион не найден.")
            if (
                auction.status not in {AuctionStatus.ACTIVE.value, AuctionStatus.EXTENDED.value}
                or auction.ends_at <= now
            ):
                raise ConflictError("Аукцион уже завершён.")
            bidder = await self._users.get_by_telegram_id(session, telegram_id)
            if bidder is None:
                raise NotFoundError("Пользователь не найден.")
            if bidder.id == auction.seller_id:
                raise ValidationError("Продавец не может делать ставки в собственном аукционе.")
            if bidder.is_blocked:
                raise AuthorizationError("Ваш аккаунт заблокирован.")
            if auction.highest_bidder_id == bidder.id:
                raise ValidationError("Вы уже лидируете в аукционе.")
            policy = await self._settings.policy(session)
            minimum = (
                auction.start_price
                if auction.highest_bidder_id is None
                else auction.current_price + policy.auction_min_increment
            )
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < minimum:
                raise ValidationError(f"Минимальная ставка: ⭐{minimum}.")
            lock_ids = [bidder.id]
            if auction.highest_bidder_id is not None:
                lock_ids.append(auction.highest_bidder_id)
            locked = await self._users.lock_many(session, lock_ids)
            locked_bidder = locked.get(bidder.id)
            if locked_bidder is None:
                raise NotFoundError("Участник аукциона не найден.")
            old_bidder = (
                locked.get(auction.highest_bidder_id) if auction.highest_bidder_id else None
            )
            old_price = auction.current_price
            BalanceBook.freeze(session, locked_bidder, amount, "auction", auction.id)
            if old_bidder is not None:
                BalanceBook.unfreeze(session, old_bidder, old_price, "auction", auction.id)
                session.add(
                    Notification(
                        user_id=old_bidder.id,
                        type=NotificationType.AUCTION_OUTBID.value,
                        payload={"auction_id": auction.id, "plate_id": auction.plate_id},
                    )
                )
            bid = Bid(auction_id=auction.id, bidder_id=locked_bidder.id, amount=amount)
            session.add(bid)
            auction.current_price = amount
            auction.highest_bidder_id = locked_bidder.id
            if auction.ends_at - now <= timedelta(minutes=policy.auction_sniping_minutes):
                auction.ends_at += timedelta(minutes=policy.auction_sniping_minutes)
                auction.status = AuctionStatus.EXTENDED.value
                bid.anti_sniping_applied = True
            session.add(
                Notification(
                    user_id=auction.seller_id,
                    type=NotificationType.AUCTION_BID_PLACED.value,
                    payload={"event": "bid", "auction_id": auction.id, "amount": amount},
                )
            )
            await session.flush()
            return auction

    async def finish(self, auction_id: int, *, force: bool = False) -> bool:
        now = utcnow()
        async with self._uow.transaction() as session:
            auction = await self._auctions.get(session, auction_id, lock=True)
            if auction is None or auction.status not in {
                AuctionStatus.ACTIVE.value,
                AuctionStatus.EXTENDED.value,
            }:
                return False
            if not force and auction.ends_at > now:
                return False
            plate = await self._plates.get(session, auction.plate_id, lock=True)
            if plate is None:
                raise NotFoundError("Номер аукциона не найден.")
            lock_ids = [auction.seller_id]
            if auction.highest_bidder_id is not None:
                lock_ids.append(auction.highest_bidder_id)
            locked = await self._users.lock_many(session, lock_ids)
            seller = locked.get(auction.seller_id)
            if seller is None:
                raise NotFoundError("Продавец аукциона не найден.")
            auction.status = AuctionStatus.FINISHED.value
            auction.is_finished = True
            if auction.highest_bidder_id is None:
                plate.status = PlateStatus.OWNED.value
                session.add(
                    Notification(
                        user_id=seller.id,
                        type=NotificationType.AUCTION_FINISHED.value,
                        payload={"auction_id": auction.id, "result": "no_bids"},
                    )
                )
                return True
            winner = locked.get(auction.highest_bidder_id)
            if winner is None:
                raise NotFoundError("Победитель аукциона не найден.")
            BalanceBook.settle_frozen(session, winner, auction.current_price, "auction", auction.id)
            policy = await self._settings.policy(session)
            commission = policy.commission(auction.current_price)
            BalanceBook.credit(
                session,
                seller,
                auction.current_price - commission,
                TransactionType.SALE_CREDIT,
                "auction",
                auction.id,
            )
            BalanceBook.state_revenue(session, commission, "auction", auction.id)
            previous_owner = plate.owner_id
            plate.owner_id = winner.id
            plate.status = PlateStatus.OWNED.value
            session.add(
                OwnershipHistory(
                    plate_id=plate.id,
                    old_owner_id=previous_owner,
                    new_owner_id=winner.id,
                    operation_type=OwnershipOperation.AUCTION.value,
                    price=auction.current_price,
                )
            )
            session.add(
                Notification(
                    user_id=winner.id,
                    type=NotificationType.AUCTION_WON.value,
                    payload={
                        "auction_id": auction.id,
                        "plate_id": plate.id,
                        "plate_number": plate.plate_number,
                    },
                )
            )
            session.add(
                Notification(
                    user_id=seller.id,
                    type=NotificationType.AUCTION_FINISHED.value,
                    payload={
                        "auction_id": auction.id,
                        "result": "sold",
                        "price": auction.current_price,
                    },
                )
            )
            return True

    async def finish_due(self) -> int:
        async with self._uow.transaction() as session:
            ids = await self._auctions.due_ids(session, utcnow())
        return sum(1 for auction_id in ids if await self.finish(auction_id))

    async def apply_pending_anti_sniping(self) -> int:
        """Durably repair a bid made by an interrupted older deployment exactly once."""
        async with self._uow.transaction() as session:
            bid_ids = await self._auctions.pending_sniping_bid_ids(session)
        applied = 0
        for bid_id in bid_ids:
            async with self._uow.transaction() as session:
                bid = await session.scalar(select(Bid).where(Bid.id == bid_id).with_for_update())
                if bid is None or bid.anti_sniping_applied:
                    continue
                auction = await self._auctions.get(session, bid.auction_id, lock=True)
                if auction is None or auction.status not in {
                    AuctionStatus.ACTIVE.value,
                    AuctionStatus.EXTENDED.value,
                }:
                    bid.anti_sniping_applied = True
                    continue
                policy = await self._settings.policy(session)
                # A bid is eligible only if its original timestamp was within the configured final window.
                if auction.ends_at - bid.created_at <= timedelta(
                    minutes=policy.auction_sniping_minutes
                ):
                    auction.ends_at += timedelta(minutes=policy.auction_sniping_minutes)
                    auction.status = AuctionStatus.EXTENDED.value
                    applied += 1
                bid.anti_sniping_applied = True
        return applied
