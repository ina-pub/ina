from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply, thread_with_link
from tests.controllers.test_communities import create
from tests.helpers import codes, me, post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def compose(client: AsyncTestClient[Litestar], modhash: str, **form: str) -> dict[str, Any]:
    fields = {'uh': modhash, 'to': 'bob', 'subject': 'hello', 'text': 'how are you?', **form}
    return await post_json(client, '/api/compose', fields)


def children(listing: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = listing['data']['children']
    return found


async def test_private_messages(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = (await register(client, 'alice'))['data']['modhash']
    bob = (await register(other_client, 'bob'))['data']['modhash']
    assert (await compose(client, alice))['errors'] == []
    await run_published(published)
    assert (await me(other_client)).json()['inbox_count'] == 1
    assert (await me(other_client)).json()['has_mail'] is True
    page = await other_client.get('/')
    assert 'class="havemail"' in page.text

    inbox = children((await other_client.get('/message/unread.json')).json())
    (message,) = inbox
    assert message['kind'] == 't4'
    data = message['data']
    assert (data['author'], data['dest'], data['subject'], data['new']) == (
        'alice',
        'bob',
        'hello',
        True,
    )
    # Opening the inbox marked it read (bob's mark_messages_read is on by default).
    assert (await me(other_client)).json()['inbox_count'] == 0
    assert children((await other_client.get('/message/unread.json')).json()) == []

    answer = await post_json(
        other_client, '/api/comment', {'uh': bob, 'thing_id': data['name'], 'text': 'fine!'}
    )
    assert answer['errors'] == []
    (thing,) = answer['data']['things']
    assert thing['kind'] == 't4'
    assert 'fine!' in thing['data']['content']
    await run_published(published)
    conversation = (await client.get(f'/message/messages/{data["id"]}.json')).json()
    (first,) = children(conversation)
    replies = children(first['data']['replies'])
    assert [r['data']['subject'] for r in replies] == ['re: hello']
    assert replies[0]['data']['dest'] == 'alice'
    sent = children((await client.get('/message/sent.json')).json())
    assert [m['data']['subject'] for m in sent] == ['hello']


async def test_compose_errors_and_without_js(client: AsyncTestClient[Litestar]) -> None:
    alice = (await register(client, 'alice'))['data']['modhash']
    cases = [
        ({'to': ''}, [('NO_USER', 'to')]),
        ({'to': 'nobody'}, [('USER_DOESNT_EXIST', 'to')]),
        ({'to': '/in/nowhere'}, [('COMMUNITY_NOEXIST', 'to')]),
        ({'to': 'in/nowhere'}, [('COMMUNITY_NOEXIST', 'to')]),
        ({'to': 'alice', 'subject': ''}, [('NO_SUBJECT', 'subject')]),
        ({'to': 'alice', 'text': ''}, [('NO_TEXT', 'text')]),
        ({'to': 'alice', 'subject': 'x' * 101}, [('TOO_LONG', 'subject')]),
        ({'to': 'alice', 'from_community': 'nowhere'}, [('COMMUNITY_NOEXIST', 'from_community')]),
    ]
    for form, expected in cases:
        assert codes(await compose(client, alice, **form)) == expected, form
    done = await client.post(
        '/api/compose',
        data={'uh': alice, 'to': 'u/alice', 'subject': 's', 'text': 't'},
        follow_redirects=False,
    )
    assert done.headers['location'] == '/message/sent/'
    page = await client.get('/message/compose?to=bob&subject=hey')
    assert 'value="bob"' in page.text


async def test_replies_and_mentions_reach_the_inbox(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await thread_with_link(client)
    mine = await reply(client, alice, 't3_1', 'my comment')
    bob = (await register(other_client, 'bob'))['data']['modhash']
    await reply(other_client, bob, 't3_1', 'nice post')
    await reply(other_client, bob, mine['data']['id'], 'nice comment')
    await reply(other_client, bob, 't3_1', 'hey /u/alice and u/nobody')
    await reply(client, alice, 't3_1', 'talking to myself, /u/alice')
    await run_published(published)

    assert (await me(client)).json()['inbox_count'] == 3
    inbox = children((await client.get('/message/inbox.json')).json())
    assert [(c['data']['subject'], c['data']['body']) for c in inbox] == [
        ('post reply', 'hey /u/alice and u/nobody'),
        ('comment reply', 'nice comment'),
        ('post reply', 'nice post'),
    ]
    assert inbox[0]['data']['was_comment'] is True
    assert inbox[0]['data']['link_title'] == 'Discuss'
    assert inbox[0]['data']['context'] == '/in/pics/comments/1/discuss/4/?context=3'
    # The mention is the same comment as a post reply, so it is not listed twice.
    assert children((await client.get('/message/mentions.json')).json()) == []


async def test_mentions(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await thread_with_link(client)
    await register(other_client, 'bob')
    top = await reply(client, alice, 't3_1', 'a comment by alice')
    await reply(client, alice, top['data']['id'], 'ping /u/bob')
    await reply(client, alice, top['data']['id'], 'u/bob u/a1 u/a2 u/a3: too many to notify')
    await run_published(published)
    mentions = children((await other_client.get('/message/mentions.json')).json())
    assert [(m['data']['subject'], m['data']['body']) for m in mentions] == [
        ('username mention', 'ping /u/bob')
    ]


async def test_read_state(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = (await register(client, 'alice'))['data']['modhash']
    bob = (await register(other_client, 'bob'))['data']['modhash']
    await compose(client, alice)
    await run_published(published)
    await other_client.post('/post/options', data={'uh': bob, 'numsites': '25'})
    listed = children((await other_client.get('/message/inbox.json')).json())
    name = listed[0]['data']['name']
    assert (await me(other_client)).json()['inbox_count'] == 1
    await post_json(other_client, '/api/read_message', {'uh': bob, 'id': name})
    assert (await me(other_client)).json()['inbox_count'] == 0
    await post_json(other_client, '/api/unread_message', {'uh': bob, 'id': name})
    assert (await me(other_client)).json()['inbox_count'] == 1
    # alice cannot touch bob's inbox.
    await post_json(client, '/api/read_message', {'uh': alice, 'id': name})
    assert (await me(other_client)).json()['inbox_count'] == 1
    await post_json(other_client, '/api/read_all_messages', {'uh': bob})
    assert (await me(other_client)).json()['inbox_count'] == 0


async def test_modmail(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob = (await register(other_client, 'bob'))['data']['modhash']
    assert (await compose(other_client, bob, to='/in/pics', subject='a question'))['errors'] == []
    await run_published(published)

    assert 'class="havemail access-required"' in (await client.get('/')).text
    modmail = children((await client.get('/message/moderator.json')).json())
    (question,) = modmail
    assert (question['data']['dest'], question['data']['community']) == ('#pics', 'pics')
    assert 'class="nohavemail access-required"' in (await client.get('/')).text

    await post_json(
        client, '/api/comment', {'uh': alice, 'thing_id': question['data']['name'], 'text': 'yes'}
    )
    await post_json(
        client,
        '/api/compose',
        {'uh': alice, 'to': 'bob', 'from_community': 'pics', 'subject': 'notice', 'text': 'hi'},
    )
    await run_published(published)
    received = children((await other_client.get('/message/messages.json')).json())
    assert [(m['data']['subject'], m['data']['author']) for m in received] == [
        ('notice', None),
        ('re: a question', 'alice'),
    ]
    denied = await compose(other_client, bob, to='alice', from_community='pics')
    assert codes(denied) == [('COMMUNITY_NOTALLOWED', 'from_community')]


async def test_conversations_are_private(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = (await register(client, 'alice'))['data']['modhash']
    await register(other_client, 'carol')
    await post_json(
        client, '/api/compose', {'uh': alice, 'to': 'alice', 'subject': 's', 'text': 't'}
    )
    assert (await client.get('/message/messages/1')).status_code == 200
    assert (await other_client.get('/message/messages/1')).status_code == 404
    carol = (await other_client.get('/api/v1/me')).json()['modhash']
    refused = await other_client.post(
        '/api/comment', data={'uh': carol, 'thing_id': 't4_1', 'text': 'x', 'api_type': 'json'}
    )
    assert refused.status_code == 403
    assert (await client.get('/message/nonsense')).status_code == 404
