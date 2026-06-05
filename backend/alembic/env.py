"""Alembic environment — async engine, ORM metadata as the schema source."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401  (register all tables on Base.metadata)

config = context.config
if config.config_file_name is not None:
  fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _do_run_migrations(connection) -> None:
  context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
  with context.begin_transaction():
    context.run_migrations()


async def run_async_migrations() -> None:
  engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
  async with engine.connect() as connection:
    await connection.run_sync(_do_run_migrations)
  await engine.dispose()


def run_migrations_offline() -> None:
  context.configure(
    url=get_settings().database_url,
    target_metadata=target_metadata,
    literal_binds=True,
    dialect_opts={"paramstyle": "named"},
  )
  with context.begin_transaction():
    context.run_migrations()


if context.is_offline_mode():
  run_migrations_offline()
else:
  asyncio.run(run_async_migrations())
