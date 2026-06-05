"""add device_type and openpilot_version to device

These back the connect-compatible device objects (`/v1/me/devices/`):
`device_type` -> connect's display-name mapping, `openpilot_version` shown in
the device panel. Existing rows default to a comma 3X ("threex").

Revision ID: 0002_device_connect_fields
Revises: 0001_initial
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_device_connect_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "device",
    sa.Column("device_type", sa.String(length=16), nullable=False, server_default="threex"),
  )
  op.add_column("device", sa.Column("openpilot_version", sa.String(), nullable=True))


def downgrade() -> None:
  op.drop_column("device", "openpilot_version")
  op.drop_column("device", "device_type")
