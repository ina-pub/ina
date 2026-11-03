from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply, thread_json, thread_with_link
from tests.controllers.test_listings import submitted, titles
from tests.helpers import codes, find_account, post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings

THREAD = '/in/pics/comments/1/discuss/'


async def edit(client: AsyncTestClient[Litestar], modhash: str, thing: str, text: str) -> Any:  # noqa: ANN401
    return await client.post(
        '/api/editusertext',
        data={'uh': modhash, 'thing_id': thing, 'text': text, 'api_type': 'json'},
    )


async def test_edit_a_comment(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await thread_with_link(client)
    await reply(client, alice, 't3_1', 'first try')
    edited = await edit(client, alice, 't1_1', 'second *try*')
    assert edited.json()['json'] == {
        'errors': [],
        'data': {
            'things': [
                {
                    'kind': 't1',
                    'data': {
                        'id': 't1_1',
                        'contentText': 'second *try*',
                        'contentHTML': '<p>second <em>try</em></p>\n',
                    },
                }
            ]
        },
    }
    shown = thread_json((await client.get(f'{THREAD}.json')).json())[0]['data']
    assert shown['body'] == 'second *try*'
    assert shown['edited'] is not False
    assert 'class="edited-timestamp"' in (await client.get(THREAD)).text

    assert codes((await edit(client, alice, 't1_1', ' ')).json()['json']) == [('NO_TEXT', 'text')]
    bob = (await register(other_client, 'bob'))['data']['modhash']
    refused = await edit(other_client, bob, 't1_1', 'mine now')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('NOT_AUTHOR', 'thing_id')]


async def test_edit_a_self_post(client: AsyncTestClient[Litestar]) -> None:
    alice = await thread_with_link(client)
    await submitted(client, alice, kind='self', title='Words', text='hello')
    assert (await edit(client, alice, 't3_2', 'hello **there**')).status_code == 200
    data = (await client.get('/in/pics/comments/2/words/.json')).json()[0]['data']['children'][0]
    assert (data['data']['selftext'], data['data']['selftext_html']) == (
        'hello **there**',
        '<p>hello <strong>there</strong></p>\n',
    )
    # A self post may lose its text; a link post has none to edit.
    assert (await edit(client, alice, 't3_2', '')).status_code == 200
    assert (await edit(client, alice, 't3_1', 'text')).status_code == 403


async def test_edit_without_js(client: AsyncTestClient[Litestar]) -> None:
    alice = await thread_with_link(client)
    await reply(client, alice, 't3_1', 'first try')
    permalink = f'{THREAD}1/'
    closed = (await client.get(permalink)).text
    assert 'class="usertext edit-usertext"\n        hidden' in closed
    assert f'href="{permalink}?edit=1" class="edit-usertext"' in closed
    opened = (await client.get(f'{permalink}?edit=1')).text
    assert 'class="usertext edit-usertext"\n        >' in opened
    assert '>first try</textarea>' in opened

    saved = await client.post(
        '/api/editusertext',
        data={'uh': alice, 'thing_id': 't1_1', 'text': 'fixed'},
        follow_redirects=False,
    )
    assert saved.headers['location'] == permalink
    empty = await client.post('/api/editusertext', data={'uh': alice, 'thing_id': 't1_1'})
    assert 'this can&#39;t be empty' in empty.text
    assert '></textarea>' in empty.text


async def test_delete_a_comment(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
    settings: Settings,
) -> None:
    alice = await thread_with_link(client)
    bob = (await register(other_client, 'bob'))['data']['modhash']
    await reply(other_client, bob, 't3_1', 'regrettable')
    await reply(client, alice, 't1_1', 'an answer')
    await post_json(client, '/api/vote', {'uh': alice, 'id': 't1_1', 'dir': '1'})
    await run_published(published)
    assert (await find_account(settings, 'bob')).comment_karma == 1

    refused = await client.post('/api/del', data={'uh': alice, 'id': 't1_1', 'api_type': 'json'})
    assert refused.status_code == 403
    deleted = await other_client.post(
        '/api/del', data={'uh': bob, 'id': 't1_1', 'dest': THREAD}, follow_redirects=False
    )
    assert deleted.headers['location'] == THREAD
    await run_published(published)
    page = (await client.get(f'{THREAD}.json')).json()
    top = thread_json(page)[0]['data']
    assert (top['author'], top['body']) == ('[deleted]', '[deleted]')
    # The reply under it stays.
    assert top['replies']['data']['children'][0]['data']['body'] == 'an answer'
    assert page[0]['data']['children'][0]['data']['num_comments'] == 1
    assert (await find_account(settings, 'bob')).comment_karma == 0
    assert (await client.get('/user/bob/comments.json')).json()['data']['children'] == []


async def test_delete_a_link(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await thread_with_link(client)
    await submitted(client, alice, kind='self', title='Words', text='secret')
    await run_published(published)
    bob = (await register(other_client, 'bob'))['data']['modhash']
    refused = await other_client.post(
        '/api/del', data={'uh': bob, 'id': 't3_2', 'api_type': 'json'}
    )
    assert refused.status_code == 403
    gone = await client.post('/api/del', data={'uh': alice, 'id': 't3_2', 'api_type': 'json'})
    assert gone.json() == {'json': {'errors': []}}
    await run_published(published)
    assert titles((await client.get('/in/pics/new.json')).json()) == ['Discuss']
    # Its page stays, as on r2, without the author or the text, and takes no more comments.
    page = await other_client.get('/in/pics/comments/2/words/')
    assert page.status_code == 200
    assert 'secret' not in page.text
    assert '[deleted]' in page.text
    data = (await other_client.get('/in/pics/comments/2/words/.json')).json()[0]['data'][
        'children'
    ][0]['data']
    assert (data['author'], data['selftext']) == ('[deleted]', '[deleted]')
    late = await post_json(
        other_client, '/api/comment', {'uh': bob, 'thing_id': 't3_2', 'text': 'hi'}
    )
    assert codes(late) == [('DELETED_COMMENT', 'parent')]
