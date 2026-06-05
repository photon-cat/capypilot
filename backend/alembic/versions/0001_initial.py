"""initial schema

Builds the whole schema from the ORM metadata so models.py stays the single
source of truth for a greenfield project.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-30
"""
from alembic import op

from app.db import Base
from app import models  # noqa: F401  (populate Base.metadata)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  Base.metadata.create_all(bind=bind)


def downgrade() -> None:
  bind = op.get_bind()
  Base.metadata.drop_all(bind=bind)
