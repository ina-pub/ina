from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from litestar.testing import AsyncTestClient

from app.main import create_app
from app.settings import Settings

if TYPE_CHECKING:
    from litestar import Litestar


async def test_html_by_default(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/health')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert '<h1>ok</h1>' in response.text
    assert response.headers['vary'] == 'Accept, Authorization, Cookie'


@pytest.mark.parametrize(
    ('path', 'headers'),
    [
        ('/health.json', {}),
        ('/health?api_type=json', {}),
        ('/health', {'Accept': 'application/json'}),
    ],
)
async def test_json_when_asked(
    client: AsyncTestClient[Litestar], path: str, headers: dict[str, str]
) -> None:
    response = await client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/json'
    assert response.json() == {'status': 'ok', 'checks': {'postgres': 'ok'}}


async def test_browser_accept_gets_html(client: AsyncTestClient[Litestar]) -> None:
    accept = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    response = await client.get('/health', headers={'Accept': accept})
    assert response.headers['content-type'].startswith('text/html')


async def test_degraded_when_postgres_is_unreachable() -> None:
    settings = Settings(database_url='postgresql+asyncpg://ina:ina@localhost:1/ina')
    async with AsyncTestClient(app=create_app(settings)) as client:
        response = await client.get('/health.json')
    assert response.status_code == 503
    assert response.json() == {'status': 'degraded', 'checks': {'postgres': 'failed'}}
