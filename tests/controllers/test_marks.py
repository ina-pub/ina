from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply, thread_json, thread_with_link
from tests.controllers.test_listings import submitted, titles
from tests.controllers.test_users import kinds_and_names
from tests.helpers import codes, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

THREAD = '/in/pics/comments/1/discuss/'


async def mark(client: AsyncTestClient[Litestar], modhash: str, path: str, thing: str) -> Any:  # noqa: ANN401
    return await post_json(client, path, {'uh': modhash, 'id': thing})


async def test_save_links_and_comments(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await thread_with_link(client)
    await reply(client, alice, 't3_1', 'worth keeping')
    assert (await mark(client, alice, '/api/save', 't3_1'))['errors'] == []
    await mark(client, alice, '/api/save', 't1_1')
    saved = (await client.get('/user/alice/saved.json')).json()
    assert kinds_and_names(saved) == [('t1', 't1_1'), ('t3', 't3_1')]
    assert saved['data']['children'][0]['data']['saved'] is True
    thread = (await client.get(f'{THREAD}.json')).json()
    assert thread[0]['data']['children'][0]['data']['saved'] is True
    assert thread_json(thread)[0]['data']['saved'] is True
    page = (await client.get('/in/pics/')).text
    assert 'action="/api/unsave"' in page
    assert 'href="/user/alice/saved/"' in (await client.get('/user/alice')).text

    # Only the owner sees what they saved.
    await register(other_client, 'bob')
    assert (await other_client.get('/user/alice/saved')).status_code == 404
    assert 'href="/user/alice/saved/"' not in (await other_client.get('/user/alice')).text
    assert (await other_client.get(f'{THREAD}.json')).json()[0]['data']['children'][0]['data'][
        'saved'
    ] is False

    without_js = await client.post(
        '/api/unsave', data={'uh': alice, 'id': 't3_1', 'dest': THREAD}, follow_redirects=False
    )
    assert without_js.headers['location'] == THREAD
    assert kinds_and_names((await client.get('/user/alice/saved.json')).json()) == [('t1', 't1_1')]


async def test_hide_links(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await thread_with_link(client)
    await submitted(client, alice, title='Second', url='b.org')
    await register(other_client, 'bob')
    await mark(client, alice, '/api/hide', 't3_1,t3_2')
    assert titles((await client.get('/in/pics/new.json')).json()) == []
    assert titles((await other_client.get('/in/pics/new.json')).json()) == ['Second', 'Discuss']
    hidden = (await client.get('/user/alice/hidden.json')).json()
    assert {child['data']['name'] for child in hidden['data']['children']} == {'t3_1', 't3_2'}
    assert all(child['data']['hidden'] for child in hidden['data']['children'])
    assert 'action="/api/unhide"' in (await client.get('/user/alice/hidden/')).text

    await mark(client, alice, '/api/unhide', 't3_1')
    assert titles((await client.get('/in/pics/new.json')).json()) == ['Discuss']
    await reply(client, alice, 't3_1', 'a comment')
    comment = await client.post('/api/hide', data={'uh': alice, 'id': 't1_1', 'api_type': 'json'})
    assert codes(comment.json()['json']) == [('NO_THING_ID', 'id')]
