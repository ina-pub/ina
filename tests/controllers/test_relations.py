from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted, titles
from tests.controllers.test_messages import children, compose
from tests.helpers import codes, post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def relation(
    client: AsyncTestClient[Litestar], path: str, modhash: str, **form: str
) -> dict[str, Any]:
    return await post_json(client, path, {'uh': modhash, **form})


async def test_friends(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    await submitted(other_client, bob, title="bob's", url='https://b.org/')
    await submitted(client, alice, title="alice's", url='https://a.org/')
    await reply(other_client, bob, 't3_2', 'a friendly word')

    added = await relation(client, '/api/friend', alice, type='friend', name='bob', note='pal')
    assert added['errors'] == []
    friends = (await client.get('/prefs/friends.json')).json()
    assert [(f['name'], f['id']) for f in friends['data']['children']] == [('bob', 't2_2')]
    assert 'pal' in (await client.get('/prefs/friends/')).text
    # Friends stand out, and /in/friends lists what they submitted.
    assert 'class="author friend"' in (await client.get('/in/pics/')).text
    assert 'author friend' in (await client.get('/in/pics/comments/2/')).text
    assert 'author friend' not in (await other_client.get('/in/pics/comments/2/')).text
    assert titles((await client.get('/in/friends.json')).json()) == ["bob's"]
    assert 'unfriend' in (await client.get('/user/bob')).text

    for name, error in (('alice', 'INVALID_OPTION'), ('nobody', 'USER_DOESNT_EXIST')):
        bad = await relation(client, '/api/friend', alice, type='friend', name=name)
        assert codes(bad) == [(error, 'name')]
    await relation(client, '/api/unfriend', alice, type='friend', name='bob')
    assert titles((await client.get('/in/friends.json')).json()) == []
    assert 'add as a friend' in (await client.get('/user/bob')).text
    assert (await other_client.get('/in/friends', follow_redirects=False)).status_code == 200


async def test_blocking(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    await submitted(client, alice, title='mine', url='https://a.org/')
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    await compose(other_client, bob, to='alice', subject='hey')
    await run_published(published)
    inbox = children((await client.get('/message/inbox.json')).json())
    assert [m['data']['subject'] for m in inbox] == ['hey']
    assert 'block sender' in (await client.get('/message/inbox/')).text

    blocked = await client.post(
        '/api/block', data={'uh': alice, 'id': inbox[0]['data']['name']}, follow_redirects=False
    )
    assert blocked.headers['location'] == '/message/inbox/'
    # What bob sent, and sends, stays out of the inbox; he is not told.
    assert (await compose(other_client, bob, to='alice', subject='again'))['errors'] == []
    await reply(other_client, bob, 't3_1', 'a reply to your link')
    await run_published(published)
    assert children((await client.get('/message/inbox.json')).json()) == []
    assert (await client.get('/api/me.json')).json()['data']['inbox_count'] == 0
    listed = (await client.get('/prefs/blocked.json')).json()['data']['children']
    assert [b['name'] for b in listed] == ['bob']

    await relation(client, '/api/unfriend', alice, type='enemy', name='bob')
    assert len(children((await client.get('/message/inbox.json')).json())) == 3


async def test_modmail_mutes(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    assert (await compose(other_client, bob, to='/in/pics', subject='a question'))['errors'] == []

    muted = await relation(client, '/api/friend', alice, community='pics', type='muted', name='bob')
    assert muted['errors'] == []
    await run_published(published)
    notices = children((await other_client.get('/message/messages.json')).json())
    assert notices[0]['data']['subject'] == 'muted from in/pics modmail'
    listed = (await client.get('/in/pics/about/muted.json')).json()['data']['children']
    assert [m['name'] for m in listed] == ['bob']
    assert 'href="/in/pics/about/muted/"' in (await client.get('/in/pics/')).text

    again = await compose(other_client, bob, to='/in/pics', subject='let me in')
    assert codes(again) == [('USER_MUTED', 'to')]
    question = children((await client.get('/message/moderator.json')).json())[0]['data']['name']
    answer = await relation(other_client, '/api/comment', bob, thing_id=question, text='hello?')
    assert codes(answer) == [('USER_MUTED', 'parent')]
    # Moderators cannot be muted, and others may not mute.
    assert codes(
        await relation(client, '/api/friend', alice, community='pics', type='muted', name='alice')
    ) == [('CANT_RESTRICT_MODERATOR', 'name')]
    refused = await other_client.post(
        '/api/friend', data={'uh': bob, 'community': 'pics', 'type': 'muted', 'name': 'alice'}
    )
    assert refused.status_code == 403

    await relation(client, '/api/unfriend', alice, community='pics', type='muted', name='bob')
    assert (await compose(other_client, bob, to='/in/pics', subject='thanks'))['errors'] == []
