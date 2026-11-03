from __future__ import annotations

import re
from typing import TYPE_CHECKING

from litestar.testing import AsyncTestClient

from app.main import create_app
from tests.controllers.test_admin import admin_on, bobs_link_and_alice
from tests.helpers import register

if TYPE_CHECKING:
    from litestar import Litestar

    from app.settings import Settings


def bar_request(page: str) -> str | None:
    found = re.search(r'id="admin-bar"[^>]*data-request="([0-9a-f]+)"', page, re.DOTALL)
    return found.group(1) if found else None


async def test_admin_bar(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    # Nothing is recorded for anyone else, or for an admin out of admin mode.
    for browser in (client, other_client):
        page = await browser.get('/in/pics/')
        assert 'server-timing' not in page.headers
        assert bar_request(page.text) is None

    await admin_on(client, alice)
    page = await client.get('/in/pics/')
    timing = page.headers['server-timing']
    assert timing.startswith('total;dur=')
    assert 'pg;desc="Postgres, ' in timing
    assert 'render;desc="rendering, 1"' in timing
    request_id = bar_request(page.text)
    assert request_id is not None
    assert '/admin</a>' in page.text
    # JSON gets the header too, but keeps no recording.
    assert 'server-timing' in (await client.get('/in/pics.json')).headers

    recording = (await client.get(f'/admin/timings/{request_id}')).json()
    assert recording['id'] == request_id
    assert recording['total'] > 0
    assert recording['totals']['pg']['count'] >= 1
    statements = [s['label'] for s in recording['spans'] if s['kind'] == 'pg']
    # The SQL as written, without the values bound to it.
    assert any('$1' in s for s in statements)
    assert not any('pics' in s or 'alice' in s for s in statements)
    redis = [s['label'] for s in recording['spans'] if s['kind'] == 'redis']
    assert redis
    assert all(' ' not in label or label.startswith('pipeline') for label in redis)
    assert any(s['kind'] == 'render' and s['label'] == 'community.html' for s in recording['spans'])

    # Only admins read them, and a missing one is a 404.
    assert (await other_client.get(f'/admin/timings/{request_id}')).status_code == 404
    assert (await client.get('/admin/timings/0123')).status_code == 404


async def test_admin_bar_in_debug(settings: Settings) -> None:
    debugging = settings.model_copy(update={'debug': True})
    async with AsyncTestClient(app=create_app(debugging)) as browser:
        # Logged out, a page may be cached at the edge, so it carries nothing.
        page = await browser.get('/')
        assert page.headers['cache-control'].startswith('public')
        assert 'server-timing' not in page.headers
        assert bar_request(page.text) is None
        await register(browser, 'dana')
        page = await browser.get('/')
        assert 'server-timing' in page.headers
        request_id = bar_request(page.text)
        assert request_id is not None
        assert '>debug</strong>' in page.text
        assert (await browser.get(f'/admin/timings/{request_id}')).status_code == 200
