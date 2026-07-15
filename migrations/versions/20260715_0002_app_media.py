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
    op.add_column("users", sa.Column("app_image_file_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "app_image_file_id")
