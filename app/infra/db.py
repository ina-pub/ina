"""Postgres engine and per-request sessions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from litestar.enums import ScopeType
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar import Request
    from litestar.types import ASGIApp, Message, Receive, Scope, Send
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


# Where the request's session is kept in the ASGI scope's state.
DB_SESSION = 'db_session'


def transaction_middleware(engine: AsyncEngine) -> Callable[[ASGIApp], ASGIApp]:
    """One session per request, and the request is one transaction.

    It commits just before the response starts, so a redirect's next page sees what was written,
    and rolls back if anything raises. This is a middleware rather than a generator dependency:
    Litestar does not close a dependency's generator when a later dependency raises, which left
    connections for the garbage collector.
    """
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def middleware(app: ASGIApp) -> ASGIApp:
        async def handle(scope: Scope, receive: Receive, send: Send) -> None:
            if scope['type'] != ScopeType.HTTP:
                await app(scope, receive, send)
                return
            async with sessions() as session:
                scope.setdefault('state', {})[DB_SESSION] = session

                async def committing_send(message: Message) -> None:
                    if message['type'] == 'http.response.start' and session.in_transaction():
                        await session.commit()
                    await send(message)

                try:
                    await app(scope, receive, committing_send)
                except BaseException:
                    await session.rollback()
                    raise

        return handle

    return middleware


def provide_db_session(request: Request[Any, Any, Any]) -> AsyncSession:
    session: AsyncSession = request.scope['state'][DB_SESSION]
    return session


PING_TIMEOUT = 2.0


async def ping(session: AsyncSession) -> bool:
    try:
        async with asyncio.timeout(PING_TIMEOUT):
            await session.execute(text('select 1'))
    except (SQLAlchemyError, OSError, TimeoutError):
        return False
    return True
