"""Persist the image used by the single-message application.

Revision ID: 20260715_0002
Revises: 20260713_0001
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "20260715_0002"
down_revision = "20260713_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    # The original initial migration uses current SQLAlchemy metadata.  On a
    # newly created database this column may therefore already be present.
    if "app_image_file_id" not in columns:
        op.add_column("users", sa.Column("app_image_file_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "app_image_file_id")
