import pytest
from app.application.dto import TelegramUserData
from app.application.services.marketplace import MarketplaceService
from app.domain.enums import PlateStatus


@pytest.mark.integration
async def test_state_emission_is_idempotent_and_creates_an_owned_asset(postgres_uow) -> None:
    marketplace = MarketplaceService(postgres_uow)
    owner = await marketplace.register_user(
        TelegramUserData(telegram_id=2001, username="owner", full_name="Owner"), frozenset()
    )
    invoice = await marketplace.start_state_emission(owner.telegram_id, "RU", "А777АА77")

    await marketplace.complete_telegram_payment(
        owner.telegram_id, invoice.payload, invoice.amount, "telegram-charge-2001"
    )
    await marketplace.complete_telegram_payment(
        owner.telegram_id, invoice.payload, invoice.amount, "telegram-charge-2001"
    )

    result = await marketplace.find_exact_or_offer_state("RU", "А777АА77")
    plate = await marketplace.get_plate(result.plate_id or 0)
    assert plate.owner_id == owner.id
    assert plate.status == PlateStatus.OWNED.value
    assert result.can_buy_from_state is False
