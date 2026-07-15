from typing import Any

import pytest
from app.application.services.balance import BalanceBook
from app.domain.enums import TransactionType
from app.domain.errors import InsufficientFundsError
from app.infrastructure.db.models import User


class RecordingSession:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def add(self, item: object) -> None:
        self.records.append(item)


def _user(available: int, frozen: int = 0) -> User:
    return User(id=1, telegram_id=1001, telegram_name="Tester", balance_available=available, balance_frozen=frozen)


def test_freeze_unfreeze_and_settlement_leave_an_audit_trail() -> None:
    session = RecordingSession()
    user = _user(100)

    BalanceBook.freeze(session, user, 40, "auction", 7)  # type: ignore[arg-type]
    assert (user.balance_available, user.balance_frozen) == (60, 40)

    BalanceBook.unfreeze(session, user, 15, "auction", 7)  # type: ignore[arg-type]
    BalanceBook.settle_frozen(session, user, 25, "auction", 7)  # type: ignore[arg-type]

    assert (user.balance_available, user.balance_frozen) == (75, 0)
    assert [record.type for record in session.records] == [
        TransactionType.AUCTION_FREEZE.value,
        TransactionType.AUCTION_UNFREEZE.value,
        TransactionType.AUCTION_SETTLEMENT.value,
    ]


def test_debit_rejects_an_overdraft_without_mutating_balance() -> None:
    session = RecordingSession()
    user = _user(5)

    with pytest.raises(InsufficientFundsError):
        BalanceBook.debit(session, user, 6, TransactionType.PURCHASE_DEBIT, "sale", 4)  # type: ignore[arg-type]

    assert (user.balance_available, user.balance_frozen) == (5, 0)
    assert session.records == []
