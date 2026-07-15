from dataclasses import dataclass

from app.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class MarketplacePolicy:
    commission_percent: int = 10
    state_plate_price: int = 1
    auction_min_increment: int = 5
    auction_max_duration_hours: int = 24
    auction_sniping_minutes: int = 5
    reserve_duration_minutes: int = 5
    inactive_days: int = 365
    max_sale_price: int = 99999

    def validate_sale_price(self, price: int) -> None:
        if (
            not isinstance(price, int)
            or isinstance(price, bool)
            or not 1 <= price <= self.max_sale_price
        ):
            raise ValidationError(
                f"Цена должна быть целым числом от ⭐1 до ⭐{self.max_sale_price}."
            )

    def validate_auction(self, start_price: int, duration_hours: int) -> None:
        self.validate_sale_price(start_price)
        if (
            not isinstance(duration_hours, int)
            or not 1 <= duration_hours <= self.auction_max_duration_hours
        ):
            raise ValidationError(
                f"Длительность должна быть от 1 до {self.auction_max_duration_hours} часов."
            )

    def commission(self, price: int) -> int:
        return price * self.commission_percent // 100
