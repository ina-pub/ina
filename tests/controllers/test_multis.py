from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.helpers import codes, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

PATH = '/api/multi/user/alice/m/pictures'


async def setup(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> tuple[str, str]:
    """Alice runs /in/pics, /in/dogs and /in/news, with a post in each; bob is a reader."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    for name in ('pics', 'dogs', 'news'):
        await create(client, alice, name=name, title=name)
        await submitted(
            client, alice, community=name, title=f'in {name}', url=f'https://{name}.example/'
        )
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    return alice, bob


def model(**fields: Any) -> str:  # noqa: ANN401
    return json.dumps(fields)


async def make(client: AsyncTestClient[Litestar], modhash: str, **fields: Any) -> Any:  # noqa: ANN401
    return await client.post(
        PATH, data={'uh': modhash, 'model': model(**fields)}, headers={'Accept': 'application/json'}
    )


def titles(response: Any) -> list[str]:  # noqa: ANN401
    return sorted(child['data']['title'] for child in response.json()['data']['children'])


async def test_create_read_and_list(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    subs = [{'name': 'pics'}, {'name': 'dogs'}]
    made = await make(client, alice, display_name='Pictures', communities=subs, visibility='public')
    assert made.status_code == 201
    data = made.json()['data']
    assert made.json()['kind'] == 'LabeledMulti'
    assert (data['path'], data['display_name'], data['visibility'], data['can_edit']) == (
        '/user/alice/m/pictures',
        'Pictures',
        'public',
        True,
    )
    assert data['communities'] == [{'name': 'dogs'}, {'name': 'pics'}]

    again = await make(client, alice, display_name='Again')
    assert again.status_code == 409
    assert codes(again.json()['json']) == [('MULTI_EXISTS', 'multipath')]
    not_bobs = await other_client.post(
        PATH, data={'uh': bob, 'model': '{}'}, headers={'Accept': 'application/json'}
    )
    assert not_bobs.status_code == 403

    seen = (await other_client.get(PATH)).json()['data']
    assert seen['can_edit'] is False
    assert [
        m['data']['name'] for m in (await other_client.get('/api/multi/user/alice')).json()
    ] == ['pictures']
    assert [m['data']['name'] for m in (await client.get('/api/multi/mine')).json()] == ['pictures']

    listing = await other_client.get('/user/alice/m/pictures.json')
    assert titles(listing) == ['in dogs', 'in pics']
    assert titles(await other_client.get('/user/alice/m/pictures/new.json')) == [
        'in dogs',
        'in pics',
    ]
    page = (await other_client.get('/user/alice/m/pictures')).text
    assert 'put together by' in page
    assert 'make your own copy' in page


async def test_visibility(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)
    await make(client, alice, communities=[{'name': 'pics'}])
    # Private by default: to anyone else it does not exist.
    assert (await other_client.get('/user/alice/m/pictures')).status_code == 404
    assert (await other_client.get(PATH)).status_code == 404
    assert (await client.get('/user/alice/m/pictures')).status_code == 200
    await client.put(PATH, data={'uh': alice, 'model': model(visibility='hidden')})
    # Hidden: readable with the link, but not listed.
    assert (await other_client.get('/user/alice/m/pictures')).status_code == 200
    assert (await other_client.get('/api/multi/user/alice')).json() == []


async def test_communities_in_a_multi(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)
    await make(client, alice, visibility='public')
    missing = await make(client, alice, communities=[{'name': 'nowhere'}])
    assert codes(missing.json()['json']) == [('COMMUNITY_NOEXIST', 'communities')]

    added = await client.put(f'{PATH}/in/news', data={'uh': alice})
    assert added.json() == {'name': 'news'}
    assert (await client.get(f'{PATH}/in/news')).json() == {'name': 'news'}
    assert (await client.get(PATH)).json()['data']['communities'] == [{'name': 'news'}]
    removed = await client.delete(f'{PATH}/in/news', headers={'X-Modhash': alice})
    assert removed.json() == {}
    assert (await client.get(PATH)).json()['data']['communities'] == []

    # A private community stays out of the listing for those it is closed to.
    await create(client, alice, name='secret', title='secret', type='private')
    await submitted(client, alice, community='secret', title='in secret', url='https://s.example/')
    put = await client.put(
        PATH,
        data={
            'uh': alice,
            'model': model(communities=[{'name': 'in/secret'}, {'name': '/in/news'}]),
        },
    )
    assert put.status_code == 200
    assert titles(await other_client.get('/user/alice/m/pictures.json')) == ['in news']
    assert titles(await client.get('/user/alice/m/pictures.json')) == ['in news', 'in secret']


async def test_the_forms_on_the_page(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    created = await client.post(
        '/api/multi', data={'uh': alice, 'name': 'stuff'}, follow_redirects=False
    )
    assert created.headers['location'] == '/user/alice/m/stuff'
    assert 'href="/user/alice/m/stuff"' in (await client.get('/')).text

    async def edit(**form: str) -> Any:  # noqa: ANN401
        return await client.post(
            '/user/alice/m/stuff/edit', data={'uh': alice, **form}, follow_redirects=False
        )

    await edit(action='add', community_name='pics')
    await edit(action='add', community_name='dogs')
    await edit(action='remove', community_name='dogs')
    await edit(action='update', display_name='My stuff', visibility='public', description_md='*hi*')
    data = (await client.get('/api/multi/user/alice/m/stuff')).json()['data']
    assert (data['display_name'], data['visibility'], data['description_html']) == (
        'My stuff',
        'public',
        '<p><em>hi</em></p>\n',
    )
    assert data['communities'] == [{'name': 'pics'}]

    copied = await other_client.post(
        '/user/alice/m/stuff/edit',
        data={'uh': bob, 'action': 'copy', 'name': 'mine'},
        follow_redirects=False,
    )
    assert copied.headers['location'] == '/user/bob/m/mine'
    copy = (await other_client.get('/api/multi/user/bob/m/mine')).json()['data']
    assert (copy['visibility'], copy['copied_from'], copy['communities']) == (
        'private',
        '/user/alice/m/stuff',
        [{'name': 'pics'}],
    )
    refused = await other_client.post(
        '/user/alice/m/stuff/edit', data={'uh': bob, 'action': 'delete'}
    )
    assert refused.status_code == 403
    deleted = await edit(action='delete')
    assert deleted.headers['location'] == '/user/alice'
    assert (await client.get('/user/alice/m/stuff')).status_code == 404


async def test_communities_joined_in_the_path(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await setup(client, other_client)
    assert titles(await other_client.get('/in/pics+dogs.json')) == ['in dogs', 'in pics']
    assert titles(await other_client.get('/in/pics+nowhere/new.json')) == ['in pics']
    assert (await other_client.get('/in/nowhere+nope')).status_code == 404
    page = (await other_client.get('/in/pics+dogs')).text
    assert '<title>pics+dogs</title>' in page
