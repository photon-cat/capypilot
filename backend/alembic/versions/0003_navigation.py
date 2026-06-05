"""navigation tables

Adds `nav_destination` (the single pending "next" destination per device) and
`nav_location` (saved favorites + recent destinations), backing the
`/v1/navigation/*` endpoints.

Revision ID: 0003_navigation
Revises: 0002_device_connect_fields
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_navigation"
down_revision = "0002_device_connect_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "nav_destination",
    sa.Column("dongle_id", sa.String(length=16), primary_key=True),
    sa.Column("place_name", sa.String(), nullable=False),
    sa.Column("place_details", sa.String(), nullable=True),
    sa.Column("latitude", sa.Float(), nullable=False),
    sa.Column("longitude", sa.Float(), nullable=False),
    sa.Column("modified", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
  )
  op.create_table(
    "nav_location",
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column("dongle_id", sa.String(length=16), nullable=False),
    sa.Column("save_type", sa.String(length=16), nullable=False),
    sa.Column("label", sa.String(), nullable=True),
    sa.Column("place_name", sa.String(), nullable=False),
    sa.Column("place_details", sa.String(), nullable=True),
    sa.Column("latitude", sa.Float(), nullable=False),
    sa.Column("longitude", sa.Float(), nullable=False),
    sa.Column("modified", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
  )
  op.create_index("ix_nav_location_dongle_id", "nav_location", ["dongle_id"])


def downgrade() -> None:
  op.drop_index("ix_nav_location_dongle_id", table_name="nav_location")
  op.drop_table("nav_location")
  op.drop_table("nav_destination")
