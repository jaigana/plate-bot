from datetime import UTC, datetime

import pytest
from app.domain.enums import PlateStatus
from app.infrastructure.db.models import Plate
from app.infrastructure.repositories.marketplace import PlateRepository, UserRepository


@pytest.mark.integration
async def test_repository_persists_users_and_returns_only_market_listings(postgres_uow) -> None:
    users = UserRepository()
    plates = PlateRepository()
    now = datetime.now(UTC)

    async with postgres_uow.transaction() as session:
        seller = await users.create_or_touch(
            session,
            telegram_id=101,
            username="seller",
            telegram_name="Seller",
            is_admin=False,
            now=now,
        )
        session.add_all(
            [
                Plate(
                    plate_number="А777АА77",
                    country_code="RU",
                    owner_id=seller.id,
                    status=PlateStatus.FIXED_SALE.value,
                    created_by_state=True,
                ),
                Plate(
                    plate_number="А888АА77",
                    country_code="RU",
                    owner_id=seller.id,
                    status=PlateStatus.OWNED.value,
                    created_by_state=True,
                ),
            ]
        )

    async with postgres_uow.transaction() as session:
        results = await plates.search(session, "777", "RU", 0, 10)

    assert [plate.plate_number for plate in results] == ["А777АА77"]
