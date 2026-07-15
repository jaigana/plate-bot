"""Store the one public plate and the Telegram-hosted profile cover for each user.

Revision ID: 20260715_0003
Revises: 20260715_0002
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "20260715_0003"
down_revision = "20260715_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "cover_photo_file_id" not in columns:
        op.add_column("users", sa.Column("cover_photo_file_id", sa.Text(), nullable=True))
    if "primary_plate_id" in columns:
        return
    op.add_column("users", sa.Column("primary_plate_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_users_primary_plate_id_plates",
        "users",
        "plates",
        ["primary_plate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_primary_plate_id", "users", ["primary_plate_id"])


def downgrade() -> None:
    op.drop_index("ix_users_primary_plate_id", table_name="users")
    op.drop_constraint("fk_users_primary_plate_id_plates", "users", type_="foreignkey")
    op.drop_column("users", "primary_plate_id")
    op.drop_column("users", "cover_photo_file_id")
