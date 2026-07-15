"""Initial transactional marketplace schema.

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13
"""

from dataclasses import asdict

import app.infrastructure.db.models  # noqa: F401 - force mapper registration before create_all
from alembic import op
from app.domain.enums import Screen
from app.domain.policies import MarketplacePolicy
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import BlacklistedSeries, BotCard, PlatformSetting

revision = "20260713_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.bulk_insert(
        PlatformSetting.__table__,
        [
            {"key": key, "value": str(value), "updated_by": None}
            for key, value in asdict(MarketplacePolicy()).items()
        ],
    )
    op.bulk_insert(
        BlacklistedSeries.__table__,
        [
            {"country_code": "KZ", "series": series, "created_by": None}
            for series in ("SEX", "ASS", "XXX", "BLY", "XER", "GEI")
        ],
    )
    op.bulk_insert(
        BotCard.__table__,
        [
            {
                "card_id": screen.value,
                "title": screen.value.replace("_", " ").title(),
                "description": "",
                "enabled": True,
                "updated_by": None,
            }
            for screen in Screen
        ],
    )


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
