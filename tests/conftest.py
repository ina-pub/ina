from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from litestar.testing import AsyncTestClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.infra import db
from app.jobs import broker
from app.main import create_app
from app.repos.pg.tables import metadata
from app.settings import Settings, get_settings
from tests.helpers import Pages

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from dramatiq import Message
    from litestar import Litestar

PYPROJECT = Path(__file__).parent.parent / 'pyproject.toml'


@pytest.fixture(scope='session')
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope='session', autouse=True)
def schema() -> None:
    command.upgrade(Config(toml_file=str(PYPROJECT)), 'head')


async def empty(settings: Settings) -> None:
    """Empty every app table and the test Redis db."""
    tables = ', '.join(
        table.name for table in metadata.sorted_tables if table.name != 'alembic_version'
    )
    engine = db.create_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(text(f'truncate {tables} restart identity cascade'))
    await engine.dispose()
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture(autouse=True)
async def clean(settings: Settings) -> AsyncIterator[None]:
    """Each test starts from, and leaves, an empty database."""
    await empty(settings)
    yield
    await empty(settings)


@pytest.fixture(autouse=True)
def published(monkeypatch: pytest.MonkeyPatch) -> list[Message[Any]]:
    """Every job message the code under test publishes, captured instead of sent to RabbitMQ."""
    sent: list[Message[Any]] = []

    def enqueue(message: Message[Any], *, delay: int | None = None) -> Message[Any]:  # noqa: ARG001
        sent.append(message)
        return message

    monkeypatch.setattr(broker.broker, 'enqueue', enqueue)
    return sent


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncTestClient[Litestar]]:
    async with AsyncTestClient(app=create_app(settings)) as client:
        yield client


@pytest.fixture
async def other_client(settings: Settings) -> AsyncIterator[AsyncTestClient[Litestar]]:
    """A second browser, with its own cookies."""
    async with AsyncTestClient(app=create_app(settings)) as client:
        yield client


@pytest.fixture
def server() -> Iterator[str]:
    """A web server on this machine, serving ``Pages.routes``; its base URL."""
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()
    httpd.server_close()
    Pages.routes = {}
