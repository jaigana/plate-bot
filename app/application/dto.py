from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TelegramUserData:
    telegram_id: int
    username: str | None
    full_name: str


@dataclass(frozen=True, slots=True)
class InvoiceDTO:
    payload: str
    title: str
    description: str
    amount: int


@dataclass(frozen=True, slots=True)
class Page:
    offset: int = 0
    limit: int = 10

    def __post_init__(self) -> None:
        if self.offset < 0 or not 1 <= self.limit <= 50:
            raise ValueError("Invalid pagination")


@dataclass(frozen=True, slots=True)
class PlateSearchResult:
    plate_id: int | None
    country_code: str
    plate_number: str
    can_buy_from_state: bool


@dataclass(frozen=True, slots=True)
class StatisticsDTO:
    users: int
    plates: int
    active_sales: int
    active_auctions: int
    state_revenue: int


@dataclass(frozen=True, slots=True)
class BackupDTO:
    file_name: str
    storage_path: str
    size_bytes: int
    created_at: datetime
