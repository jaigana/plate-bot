from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import TransactionType
from app.domain.errors import InsufficientFundsError, ValidationError
from app.infrastructure.db.models import PlatformLedger, Transaction, User


class BalanceBook:
    """Writes wallet changes and their immutable transaction audit record together."""

    @staticmethod
    def _record(
        session: AsyncSession,
        user: User,
        *,
        amount: int,
        transaction_type: TransactionType,
        available_before: int,
        frozen_before: int,
        reference_type: str,
        reference_id: int | None,
    ) -> None:
        session.add(
            Transaction(
                user_id=user.id,
                type=transaction_type.value,
                amount=amount,
                balance_before=available_before,
                balance_after=user.balance_available,
                frozen_before=frozen_before,
                frozen_after=user.balance_frozen,
                reference_type=reference_type,
                reference_id=reference_id,
            )
        )

    @classmethod
    def credit(
        cls,
        session: AsyncSession,
        user: User,
        amount: int,
        transaction_type: TransactionType,
        reference_type: str,
        reference_id: int | None,
    ) -> None:
        if amount <= 0:
            raise ValidationError("Сумма должна быть положительной.")
        available_before, frozen_before = user.balance_available, user.balance_frozen
        user.balance_available += amount
        cls._record(
            session,
            user,
            amount=amount,
            transaction_type=transaction_type,
            available_before=available_before,
            frozen_before=frozen_before,
            reference_type=reference_type,
            reference_id=reference_id,
        )

    @classmethod
    def debit(
        cls,
        session: AsyncSession,
        user: User,
        amount: int,
        transaction_type: TransactionType,
        reference_type: str,
        reference_id: int | None,
    ) -> None:
        if amount <= 0:
            raise ValidationError("Сумма должна быть положительной.")
        if user.balance_available < amount:
            raise InsufficientFundsError("Недостаточно доступных ⭐.")
        available_before, frozen_before = user.balance_available, user.balance_frozen
        user.balance_available -= amount
        cls._record(
            session,
            user,
            amount=-amount,
            transaction_type=transaction_type,
            available_before=available_before,
            frozen_before=frozen_before,
            reference_type=reference_type,
            reference_id=reference_id,
        )

    @classmethod
    def freeze(
        cls, session: AsyncSession, user: User, amount: int, reference_type: str, reference_id: int
    ) -> None:
        if amount <= 0 or user.balance_available < amount:
            raise InsufficientFundsError("Недостаточно доступных ⭐ для ставки.")
        available_before, frozen_before = user.balance_available, user.balance_frozen
        user.balance_available -= amount
        user.balance_frozen += amount
        cls._record(
            session,
            user,
            amount=-amount,
            transaction_type=TransactionType.AUCTION_FREEZE,
            available_before=available_before,
            frozen_before=frozen_before,
            reference_type=reference_type,
            reference_id=reference_id,
        )

    @classmethod
    def unfreeze(
        cls, session: AsyncSession, user: User, amount: int, reference_type: str, reference_id: int
    ) -> None:
        if amount <= 0 or user.balance_frozen < amount:
            raise InsufficientFundsError("Нарушена целостность замороженного баланса.")
        available_before, frozen_before = user.balance_available, user.balance_frozen
        user.balance_frozen -= amount
        user.balance_available += amount
        cls._record(
            session,
            user,
            amount=amount,
            transaction_type=TransactionType.AUCTION_UNFREEZE,
            available_before=available_before,
            frozen_before=frozen_before,
            reference_type=reference_type,
            reference_id=reference_id,
        )

    @classmethod
    def settle_frozen(
        cls, session: AsyncSession, user: User, amount: int, reference_type: str, reference_id: int
    ) -> None:
        if amount <= 0 or user.balance_frozen < amount:
            raise InsufficientFundsError("Нарушена целостность расчёта аукциона.")
        available_before, frozen_before = user.balance_available, user.balance_frozen
        user.balance_frozen -= amount
        cls._record(
            session,
            user,
            amount=-amount,
            transaction_type=TransactionType.AUCTION_SETTLEMENT,
            available_before=available_before,
            frozen_before=frozen_before,
            reference_type=reference_type,
            reference_id=reference_id,
        )

    @staticmethod
    def state_revenue(
        session: AsyncSession, amount: int, reference_type: str, reference_id: int | None
    ) -> None:
        if amount < 0:
            raise ValidationError("Доход государства не может быть отрицательным.")
        session.add(
            PlatformLedger(
                type="STATE_REVENUE",
                amount=amount,
                reference_type=reference_type,
                reference_id=reference_id,
            )
        )
