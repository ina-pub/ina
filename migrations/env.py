"""Alembic environment: the URL comes from app settings, the schema from the pg tables module."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.repos.pg.tables import metadata
from app.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logging.basicConfig(format='%(levelname)-5.5s [%(name)s] %(message)s')
logging.getLogger('alembic').setLevel(logging.INFO)


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
