from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.application.dto import InvoiceDTO, Page, PlateSearchResult, TelegramUserData
from app.application.services.balance import BalanceBook
from app.domain.countries import COUNTRIES
from app.domain.enums import (
    NotificationType,
    OwnershipOperation,
    PaymentKind,
    PaymentStatus,
    PlateStatus,
    SaleStatus,
    TransactionType,
)
from app.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PaymentError,
    ValidationError,
)
from app.domain.validators import normalize_plate_query, validate_plate
from app.infrastructure.db.models import (
    Admin,
    Notification,
    OwnershipHistory,
    PaymentIntent,
    Plate,
    Sale,
    StateEmissionReservation,
    User,
)
from app.infrastructure.db.session import UnitOfWork
from app.infrastructure.repositories.marketplace import (
    PaymentRepository,
    PlateRepository,
    SettingsRepository,
    UserRepository,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class MarketplaceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._users = UserRepository()
        self._plates = PlateRepository()
        self._payments = PaymentRepository()
        self._settings = SettingsRepository()

    @staticmethod
    def _payload() -> str:
        # Telegram invoice payload is capped at 128 bytes; our schema and callbacks cap it at 64.
        return f"p_{secrets.token_urlsafe(22)}"[:64]

    async def register_user(self, data: TelegramUserData, admin_ids: frozenset[int]) -> User:
        now = utcnow()
        async with self._uow.transaction() as session:
            user = await self._users.create_or_touch(
                session,
                telegram_id=data.telegram_id,
                username=data.username,
                telegram_name=data.full_name,
                is_admin=data.telegram_id in admin_ids,
                now=now,
            )
            if data.telegram_id in admin_ids:
                admin = await session.scalar(select(Admin).where(Admin.user_id == user.id))
                if admin is None:
                    session.add(Admin(user_id=user.id, granted_by=None))
            return user

    async def get_user(self, telegram_id: int) -> User:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id)
            if user is None:
                raise NotFoundError("Сначала откройте бота командой /start.")
            return user

    async def set_main_message(
        self,
        telegram_id: int,
        chat_id: int,
        message_id: int,
        image_file_id: str | None,
    ) -> None:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            user.app_chat_id = chat_id
            user.app_message_id = message_id
            user.app_image_file_id = image_file_id

    async def get_plate(self, plate_id: int) -> Plate:
        async with self._uow.transaction() as session:
            plate = await self._plates.get(session, plate_id)
            if plate is None:
                raise NotFoundError("Номер не найден.")
            return plate

    async def ownership_history(self, plate_id: int) -> list[OwnershipHistory]:
        async with self._uow.transaction() as session:
            if await self._plates.get(session, plate_id) is None:
                raise NotFoundError("Номер не найден.")
            rows = await session.scalars(
                select(OwnershipHistory)
                .where(OwnershipHistory.plate_id == plate_id)
                .order_by(OwnershipHistory.created_at.desc())
                .limit(50)
            )
            return list(rows)

    async def my_plates(self, telegram_id: int, page: Page) -> list[Plate]:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            return await self._plates.by_owner(session, user.id, page.offset, page.limit)

    async def search(
        self, country_code: str, raw_query: str, page: Page
    ) -> tuple[str, list[Plate]]:
        if not isinstance(raw_query, str) or len(raw_query) > 64:
            raise ValidationError("Поисковый запрос содержит слишком много символов.")
        async with self._uow.transaction() as session:
            code = country_code.upper()
            if code not in COUNTRIES or not COUNTRIES[code].active:
                raise ValidationError("Эта страна пока недоступна.")
            query = normalize_plate_query(code, raw_query)
            if len(query) > 15:
                raise ValidationError("Поисковый запрос не должен быть длиннее 15 символов.")
            return query, await self._plates.search(session, query, code, page.offset, page.limit)

    async def find_exact_or_offer_state(
        self, country_code: str, raw_number: str
    ) -> PlateSearchResult:
        code = country_code.upper()
        definition = COUNTRIES.get(code)
        if definition is None or not definition.active:
            raise ValidationError("Эта страна пока недоступна.")
        async with self._uow.transaction() as session:
            blacklist = await self._plates.blacklist(session, code)
            number = validate_plate(code, raw_number, blacklist)
            plate = await self._plates.get_by_number(session, code, number)
            return PlateSearchResult(
                plate_id=plate.id if plate else None,
                country_code=code,
                plate_number=number,
                can_buy_from_state=plate is None,
            )

    async def start_top_up(self, telegram_id: int, amount: int) -> InvoiceDTO:
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 99_999:
            raise ValidationError("Пополнение должно быть целым числом от ⭐1 до ⭐99999.")
        now = utcnow()
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            if user.is_blocked:
                raise AuthorizationError("Ваш аккаунт заблокирован.")
            intent = PaymentIntent(
                payload=self._payload(),
                user_id=user.id,
                kind=PaymentKind.TOP_UP.value,
                amount=amount,
                status=PaymentStatus.PENDING.value,
                expires_at=now + timedelta(hours=24),
            )
            session.add(intent)
            await session.flush()
            return InvoiceDTO(
                intent.payload, "Пополнение баланса", f"Зачисление ⭐{amount} на баланс", amount
            )

    async def start_state_emission(
        self, telegram_id: int, country_code: str, raw_number: str
    ) -> InvoiceDTO:
        now = utcnow()
        code = country_code.upper()
        definition = COUNTRIES.get(code)
        if definition is None or not definition.active:
            raise ValidationError("Эта страна пока недоступна.")
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if user is None:
                raise NotFoundError("Пользователь не найден.")
            if user.is_blocked:
                raise AuthorizationError("Ваш аккаунт заблокирован.")
            blacklist = await self._plates.blacklist(session, code)
            number = validate_plate(code, raw_number, blacklist)
            if await self._plates.get_by_number(session, code, number, lock=True):
                raise ConflictError("Этот номер уже существует в системе.")
            previous = await self._payments.state_reservation(session, code, number, lock=True)
            if previous is not None:
                if previous.expires_at > now:
                    raise ConflictError("Этот номер уже резервируется другим пользователем.")
                await session.delete(previous)
                await session.flush()
            policy = await self._settings.policy(session)
            intent = PaymentIntent(
                payload=self._payload(),
                user_id=user.id,
                kind=PaymentKind.STATE_EMISSION.value,
                amount=policy.state_plate_price,
                status=PaymentStatus.PENDING.value,
                country_code=code,
                requested_plate_number=number,
                expires_at=now + timedelta(minutes=policy.reserve_duration_minutes),
            )
            session.add(intent)
            await session.flush()
            try:
                async with session.begin_nested():
                    session.add(
                        StateEmissionReservation(
                            country_code=code,
                            plate_number=number,
                            user_id=user.id,
                            payment_intent_id=intent.id,
                            expires_at=intent.expires_at,
                        )
                    )
                    await session.flush()
            except IntegrityError as error:
                raise ConflictError("Этот номер уже резервируется другим пользователем.") from error
            return InvoiceDTO(
                intent.payload, "Покупка у Государства", f"Игровой номер {number}", intent.amount
            )

    async def start_state_purchase(self, telegram_id: int, plate_id: int) -> InvoiceDTO:
        """Reserve and sell a confiscated State-owned asset without re-emitting its number."""
        now = utcnow()
        async with self._uow.transaction() as session:
            buyer = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            plate = await self._plates.get(session, plate_id, lock=True)
            if buyer is None or plate is None:
                raise NotFoundError("Пользователь или номер не найден.")
            if buyer.is_blocked:
                raise AuthorizationError("Ваш аккаунт заблокирован.")
            if plate.status != PlateStatus.STATE_SALE.value or plate.owner_id is not None:
                raise ConflictError("Этот номер больше не продаётся Государством.")
            if plate.reserved_until is not None and plate.reserved_until > now:
                raise ConflictError("Номер временно резервируется другим покупателем.")
            policy = await self._settings.policy(session)
            intent = PaymentIntent(
                payload=self._payload(),
                user_id=buyer.id,
                kind=PaymentKind.STATE_PURCHASE.value,
                amount=policy.state_plate_price,
                status=PaymentStatus.PENDING.value,
                plate_id=plate.id,
                expires_at=now + timedelta(minutes=policy.reserve_duration_minutes),
            )
            session.add(intent)
            plate.reserved_by = buyer.id
            plate.reserved_until = intent.expires_at
            plate.status = PlateStatus.RESERVED.value
            await session.flush()
            return InvoiceDTO(
                intent.payload,
                "Покупка у Государства",
                f"Игровой номер {plate.plate_number}",
                intent.amount,
            )

    async def create_sale(self, telegram_id: int, plate_id: int, price: int) -> Sale:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            plate = await self._plates.get(session, plate_id, lock=True)
            if user is None or plate is None:
                raise NotFoundError("Пользователь или номер не найден.")
            if plate.owner_id != user.id:
                raise AuthorizationError("Вы не владелец этого номера.")
            if user.is_blocked:
                raise AuthorizationError("Заблокированный аккаунт не может создавать продажи.")
            if plate.status != PlateStatus.OWNED.value:
                raise ConflictError("Номер нельзя выставить на продажу в текущем статусе.")
            policy = await self._settings.policy(session)
            policy.validate_sale_price(price)
            sale = Sale(
                plate_id=plate.id,
                seller_id=user.id,
                price=price,
                commission=policy.commission(price),
                status=SaleStatus.ACTIVE.value,
            )
            session.add(sale)
            plate.status = PlateStatus.FIXED_SALE.value
            await session.flush()
            return sale

    async def cancel_sale(self, telegram_id: int, plate_id: int) -> None:
        async with self._uow.transaction() as session:
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            plate = await self._plates.get(session, plate_id, lock=True)
            if user is None or plate is None:
                raise NotFoundError("Пользователь или номер не найден.")
            sale = await self._plates.active_sale(session, plate_id, lock=True)
            if sale is None:
                raise NotFoundError("Активная продажа не найдена.")
            if sale.seller_id != user.id:
                raise AuthorizationError("Отменить продажу может только продавец.")
            if sale.status == SaleStatus.RESERVED.value:
                raise ConflictError("Номер уже зарезервирован для оплаты.")
            sale.status = SaleStatus.CANCELLED.value
            plate.status = PlateStatus.OWNED.value

    async def start_sale_purchase(self, telegram_id: int, plate_id: int) -> InvoiceDTO:
        now = utcnow()
        async with self._uow.transaction() as session:
            buyer = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            plate = await self._plates.get(session, plate_id, lock=True)
            if buyer is None or plate is None:
                raise NotFoundError("Пользователь или номер не найден.")
            if buyer.is_blocked:
                raise AuthorizationError("Ваш аккаунт заблокирован.")
            sale = await self._plates.active_sale(session, plate.id, lock=True)
            if sale is None or plate.status != PlateStatus.FIXED_SALE.value:
                raise ConflictError("Этот номер больше не продаётся.")
            if sale.seller_id == buyer.id:
                raise ValidationError("Нельзя купить собственный номер.")
            if plate.reserved_until and plate.reserved_until > now:
                raise ConflictError("Номер временно резервируется другим покупателем.")
            policy = await self._settings.policy(session)
            intent = PaymentIntent(
                payload=self._payload(),
                user_id=buyer.id,
                kind=PaymentKind.SALE_PURCHASE.value,
                amount=sale.price,
                status=PaymentStatus.PENDING.value,
                plate_id=plate.id,
                sale_id=sale.id,
                expires_at=now + timedelta(minutes=policy.reserve_duration_minutes),
            )
            session.add(intent)
            plate.reserved_by = buyer.id
            plate.reserved_until = intent.expires_at
            plate.status = PlateStatus.RESERVED.value
            sale.status = SaleStatus.RESERVED.value
            await session.flush()
            return InvoiceDTO(
                intent.payload, "Покупка игрового номера", f"Номер {plate.plate_number}", sale.price
            )

    async def validate_precheckout(self, telegram_id: int, payload: str, amount: int) -> None:
        now = utcnow()
        async with self._uow.transaction() as session:
            intent = await self._payments.get_by_payload(session, payload, lock=True)
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if (
                intent is None
                or user is None
                or intent.user_id != user.id
                or intent.status != PaymentStatus.PENDING.value
                or intent.amount != amount
                or intent.expires_at <= now
            ):
                raise PaymentError("Этот счёт недействителен или уже истёк.")

    async def complete_telegram_payment(
        self, telegram_id: int, payload: str, amount: int, charge_id: str
    ) -> None:
        now = utcnow()
        async with self._uow.transaction() as session:
            intent = await self._payments.get_by_payload(session, payload, lock=True)
            user = await self._users.get_by_telegram_id(session, telegram_id, lock=True)
            if (
                intent is None
                or user is None
                or intent.user_id != user.id
                or intent.amount != amount
            ):
                raise PaymentError("Не найден соответствующий счёт.")
            charged_intent = await self._payments.get_by_charge_id(session, charge_id, lock=True)
            if charged_intent is not None and charged_intent.payload != payload:
                raise PaymentError("Этот идентификатор платежа уже использован другим счётом.")
            if intent.status == PaymentStatus.PAID.value:
                if intent.telegram_payment_charge_id == charge_id:
                    return
                raise PaymentError("Счёт уже обработан с другим идентификатором платежа.")
            if intent.status != PaymentStatus.PENDING.value:
                raise PaymentError("Этот счёт больше нельзя оплатить.")
            if intent.kind == PaymentKind.TOP_UP.value:
                BalanceBook.credit(
                    session, user, amount, TransactionType.TOP_UP, "payment_intent", intent.id
                )
                session.add(
                    Notification(
                        user_id=user.id,
                        type=NotificationType.BALANCE_TOPUP.value,
                        payload={"amount": amount},
                    )
                )
            elif intent.kind == PaymentKind.STATE_EMISSION.value:
                await self._complete_state_emission(session, intent, user)
            elif intent.kind == PaymentKind.STATE_PURCHASE.value:
                await self._complete_state_purchase(session, intent, user)
            elif intent.kind == PaymentKind.SALE_PURCHASE.value:
                await self._complete_sale_purchase(session, intent, user)
            else:
                raise PaymentError("Неизвестный тип платежа.")
            intent.status = PaymentStatus.PAID.value
            intent.telegram_payment_charge_id = charge_id
            intent.paid_at = now

    async def _complete_state_emission(
        self, session: object, intent: PaymentIntent, user: User
    ) -> None:
        # Session's protocol is deliberately not leaked into handlers; SQLAlchemy is used only here.
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        if not intent.country_code or not intent.requested_plate_number:
            raise PaymentError("У счёта отсутствуют данные номера.")
        reservation = await self._payments.state_reservation(
            session, intent.country_code, intent.requested_plate_number, lock=True
        )
        if reservation is None or reservation.payment_intent_id != intent.id:
            raise PaymentError("Резерв номера истёк. Обратитесь в поддержку для возврата.")
        existing = await self._plates.get_by_number(
            session, intent.country_code, intent.requested_plate_number, lock=True
        )
        if existing is not None:
            raise PaymentError("Номер уже выпущен. Обратитесь в поддержку для возврата.")
        plate = Plate(
            plate_number=intent.requested_plate_number,
            country_code=intent.country_code,
            owner_id=user.id,
            status=PlateStatus.OWNED.value,
            created_by_state=True,
        )
        session.add(plate)
        await session.flush()
        session.add(
            OwnershipHistory(
                plate_id=plate.id,
                old_owner_id=None,
                new_owner_id=user.id,
                operation_type=OwnershipOperation.STATE_EMISSION.value,
                price=intent.amount,
            )
        )
        BalanceBook.state_revenue(session, intent.amount, "payment_intent", intent.id)
        session.add(
            Notification(
                user_id=user.id,
                type=NotificationType.PLATE_PURCHASED.value,
                payload={"plate_id": plate.id, "plate_number": plate.plate_number},
            )
        )
        await session.delete(reservation)

    async def _complete_state_purchase(
        self,
        session: object,
        intent: PaymentIntent,
        buyer: User,
    ) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        if intent.plate_id is None:
            raise PaymentError("У счёта отсутствуют данные номера.")
        plate = await self._plates.get(session, intent.plate_id, lock=True)
        if (
            plate is None
            or plate.status != PlateStatus.RESERVED.value
            or plate.owner_id is not None
            or plate.reserved_by != buyer.id
            or plate.reserved_until is None
        ):
            raise PaymentError("Резерв номера нарушен. Обратитесь в поддержку для возврата.")
        plate.owner_id = buyer.id
        plate.status = PlateStatus.OWNED.value
        plate.reserved_by = None
        plate.reserved_until = None
        session.add(
            OwnershipHistory(
                plate_id=plate.id,
                old_owner_id=None,
                new_owner_id=buyer.id,
                operation_type=OwnershipOperation.SALE.value,
                price=intent.amount,
            )
        )
        BalanceBook.state_revenue(session, intent.amount, "payment_intent", intent.id)
        session.add(
            Notification(
                user_id=buyer.id,
                type=NotificationType.PLATE_PURCHASED.value,
                payload={"plate_id": plate.id, "plate_number": plate.plate_number},
            )
        )

    async def _complete_sale_purchase(
        self, session: object, intent: PaymentIntent, buyer: User
    ) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        if intent.plate_id is None or intent.sale_id is None:
            raise PaymentError("У счёта отсутствуют данные продажи.")
        plate = await self._plates.get(session, intent.plate_id, lock=True)
        sale = await self._plates.active_sale(session, intent.plate_id, lock=True)
        if plate is None or sale is None or sale.id != intent.sale_id:
            raise PaymentError("Продажа больше недоступна. Обратитесь в поддержку для возврата.")
        if (
            sale.status != SaleStatus.RESERVED.value
            or plate.reserved_by != buyer.id
            or plate.status != PlateStatus.RESERVED.value
            or sale.price != intent.amount
        ):
            raise PaymentError("Резерв продажи нарушен. Обратитесь в поддержку для возврата.")
        locked = await self._users.lock_many(session, [buyer.id, sale.seller_id])
        seller = locked.get(sale.seller_id)
        if seller is None:
            raise PaymentError("Продавец не найден.")
        BalanceBook.credit(
            session,
            seller,
            sale.price - sale.commission,
            TransactionType.SALE_CREDIT,
            "sale",
            sale.id,
        )
        BalanceBook.state_revenue(session, sale.commission, "sale", sale.id)
        previous_owner = plate.owner_id
        plate.owner_id = buyer.id
        plate.status = PlateStatus.OWNED.value
        plate.reserved_by = None
        plate.reserved_until = None
        sale.status = SaleStatus.COMPLETED.value
        sale.buyer_id = buyer.id
        sale.completed_at = utcnow()
        session.add(
            OwnershipHistory(
                plate_id=plate.id,
                old_owner_id=previous_owner,
                new_owner_id=buyer.id,
                operation_type=OwnershipOperation.SALE.value,
                price=sale.price,
            )
        )
        session.add(
            Notification(
                user_id=seller.id,
                type=NotificationType.SALE_COMPLETED.value,
                payload={
                    "plate_id": plate.id,
                    "plate_number": plate.plate_number,
                    "price": sale.price,
                },
            )
        )
        session.add(
            Notification(
                user_id=buyer.id,
                type=NotificationType.PLATE_PURCHASED.value,
                payload={"plate_id": plate.id, "plate_number": plate.plate_number},
            )
        )

    async def expire_pending_payment(self, payload: str) -> bool:
        now = utcnow()
        async with self._uow.transaction() as session:
            intent = await self._payments.get_by_payload(session, payload, lock=True)
            if (
                intent is None
                or intent.status != PaymentStatus.PENDING.value
                or intent.expires_at > now
            ):
                return False
            intent.status = PaymentStatus.EXPIRED.value
            if (
                intent.kind == PaymentKind.STATE_EMISSION.value
                and intent.country_code
                and intent.requested_plate_number
            ):
                reservation = await self._payments.state_reservation(
                    session, intent.country_code, intent.requested_plate_number, lock=True
                )
                if reservation and reservation.payment_intent_id == intent.id:
                    await session.delete(reservation)
            elif (
                intent.kind
                in {
                    PaymentKind.SALE_PURCHASE.value,
                    PaymentKind.STATE_PURCHASE.value,
                }
                and intent.plate_id is not None
            ):
                plate = await self._plates.get(session, intent.plate_id, lock=True)
                sale = await self._plates.active_sale(session, intent.plate_id, lock=True)
                if intent.kind == PaymentKind.SALE_PURCHASE.value and (
                    plate is None
                    or sale is None
                    or sale.id != intent.sale_id
                    or sale.status != SaleStatus.RESERVED.value
                ):
                    return True
                if plate is not None:
                    plate.status = (
                        PlateStatus.FIXED_SALE.value
                        if intent.kind == PaymentKind.SALE_PURCHASE.value
                        else PlateStatus.STATE_SALE.value
                    )
                    plate.reserved_by = None
                    plate.reserved_until = None
                if sale is not None and intent.kind == PaymentKind.SALE_PURCHASE.value:
                    sale.status = SaleStatus.ACTIVE.value
            return True

    async def expire_pending_payments(self) -> int:
        async with self._uow.transaction() as session:
            intents = await self._payments.pending_expired(session, utcnow())
            payloads = [intent.payload for intent in intents]
        return sum(1 for payload in payloads if await self.expire_pending_payment(payload))
