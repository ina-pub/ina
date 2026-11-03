"""What actors share within a worker process: a database engine, Redis and search clients."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.infra.fetch import Fetcher
from app.infra.redis import create_redis
from app.infra.storage import create_store
from app.search import create_search
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from app.infra.opensearch import SearchClient
    from app.infra.storage import ObjectStore


@cache
def engine() -> AsyncEngine:
    return db.create_engine(get_settings())


@cache
def redis() -> Redis:
    return create_redis(get_settings())


@cache
def search() -> SearchClient:
    return create_search(get_settings())


@cache
def store() -> ObjectStore:
    return create_store(get_settings())


@cache
def fetcher() -> Fetcher:
    return Fetcher(allow_private=get_settings().media_allow_private)


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine())() as session, session.begin():
        yield session
