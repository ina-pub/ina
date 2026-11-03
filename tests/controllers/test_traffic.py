from __future__ import annotations

from typing import TYPE_CHECKING

from tests.controllers.test_communities import create, settings_form
from tests.helpers import codes, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def test_traffic(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    # Pages count; the API does not.
    for path in ('/in/pics/', '/in/pics/new/', '/in/pics/about/moderators'):
        await other_client.get(path)
    await other_client.get('/in/pics.json')
    await client.get('/in/pics/')
    form = {'uh': bob, 'action': 'sub', 'community': 't5_1'}
    await other_client.post('/api/subscribe', data=form, follow_redirects=False)

    stats = (await client.get('/in/pics/about/traffic.json')).json()
    assert stats['hour'][0][1:] == [2, 4]
    assert stats['day'][0][1:] == [2, 4, 1]
    assert stats['month'][0][1:] == [2, 4]
    assert (len(stats['hour']), len(stats['day']), len(stats['month'])) == (72, 60, 12)
    page = (await client.get('/in/pics/about/traffic')).text
    assert '<caption>by hour</caption>' in page
    assert 'href="/in/pics/about/traffic/"' in page

    # Moderators only, unless the community makes its traffic public.
    refused = await other_client.get('/in/pics/about/traffic.json')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('MOD_REQUIRED', None)]
    form = {'uh': alice, 'community': 't5_1', **settings_form(public_traffic='on')}
    await post_json(client, '/api/site_admin', form)
    assert (await other_client.get('/in/pics/about/traffic.json')).status_code == 200
