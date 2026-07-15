import pytest
from app.application.dto import TelegramUserData
from app.application.services.marketplace import MarketplaceService
from app.domain.enums import PlateStatus
from app.domain.errors import ValidationError
from app.infrastructure.db.models import Plate


@pytest.mark.integration
async def test_user_can_publish_only_one_owned_available_primary_plate(postgres_uow) -> None:
    marketplace = MarketplaceService(postgres_uow)
    owner = await marketplace.register_user(
        TelegramUserData(telegram_id=3001, username="owner", full_name="Owner"), frozenset()
    )
    other = await marketplace.register_user(
        TelegramUserData(telegram_id=3002, username="other", full_name="Other"), frozenset()
    )
    async with postgres_uow.transaction() as session:
        plate = Plate(
            plate_number="А555АА77",
            country_code="RU",
            owner_id=owner.id,
            status=PlateStatus.OWNED.value,
            created_by_state=True,
        )
        session.add(plate)
        await session.flush()
        plate_id = plate.id

    selected = await marketplace.set_primary_plate(owner.telegram_id, plate_id)
    await marketplace.set_cover_photo(owner.telegram_id, "telegram-photo-file-id")
    profile, primary = await marketplace.profile(owner.telegram_id)

    assert selected.id == plate_id
    assert primary is not None and primary.id == plate_id
    assert profile.cover_photo_file_id == "telegram-photo-file-id"
    with pytest.raises(ValidationError):
        await marketplace.set_primary_plate(other.telegram_id, plate_id)
