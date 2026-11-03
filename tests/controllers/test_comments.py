from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services import comments as comments_service
from app.services import threads
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted, vote
from tests.helpers import codes, post_json, register, run_published

if TYPE_CHECKING:
    import pytest
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


THREAD = '/in/pics/comments/1/discuss/'


async def thread_with_link(client: AsyncTestClient[Litestar]) -> str:
    modhash: str = (await register(client))['data']['modhash']
    await create(client, modhash)
    await submitted(client, modhash, title='Discuss', url='a.org')
    return modhash


async def reply(
    client: AsyncTestClient[Litestar], modhash: str, parent: str, text: str
) -> dict[str, Any]:
    body = await post_json(
        client, '/api/comment', {'uh': modhash, 'thing_id': parent, 'text': text}
    )
    assert body['errors'] == [], body
    thing: dict[str, Any] = body['data']['things'][0]
    return thing


def thread_json(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = page[1]['data']['children']
    return children


async def test_reply_and_nest(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    modhash = await thread_with_link(client)
    top = await reply(client, modhash, 't3_1', 'first **comment**')
    assert top['kind'] == 't1'
    assert (top['data']['id'], top['data']['parent'], top['data']['link']) == (
        't1_1',
        't3_1',
        't3_1',
    )
    assert 'data-fullname="t1_1"' in top['data']['content']
    assert '<strong>comment</strong>' in top['data']['content']
    await reply(client, modhash, 't1_1', 'a reply')
    await run_published(published)

    page = (await client.get('/in/pics/comments/1/discuss.json')).json()
    assert page[0]['data']['children'][0]['data']['num_comments'] == 2
    (first,) = thread_json(page)
    assert first['data']['body'] == 'first **comment**'
    assert (first['data']['score'], first['data']['likes'], first['data']['depth']) == (1, True, 0)
    child = first['data']['replies']['data']['children'][0]['data']
    assert (child['body'], child['parent_id'], child['depth']) == ('a reply', 't1_1', 1)
    assert child['replies'] == ''

    html = await client.get('/in/pics/comments/1/discuss/')
    assert '2 comments</span>' in html.text
    assert 'data-fullname="t1_2"' in html.text
    assert 'class="author submitter"' in html.text


async def test_reply_buttons_show_when_logged_in(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    """r2's stylesheet hides reply buttons unless the body has the loggedin class."""
    modhash = await thread_with_link(client)
    await reply(client, modhash, 't3_1', 'hello')
    assert '<body class="loggedin ' in (await client.get(THREAD)).text
    assert '<body class="loggedin' not in (await other_client.get(THREAD)).text


async def test_reply_without_js(client: AsyncTestClient[Litestar]) -> None:
    modhash = await thread_with_link(client)
    form = {'uh': modhash, 'thing_id': 't3_1', 'text': 'hello'}
    done = await client.post('/api/comment', data=form, follow_redirects=False)
    assert done.status_code == 303
    assert done.headers['location'] == '/in/pics/comments/1/discuss/1/?context=1'
    empty = await client.post('/api/comment', data={**form, 'thing_id': 't1_1', 'text': ' '})
    assert empty.status_code == 200
    assert 'class="error NO_TEXT field-text"' in empty.text
    permalink = await client.get('/in/pics/comments/1/discuss/1/')
    assert 'This is one comment and its replies.' in permalink.text
    assert 'name="thing_id" value="t1_1"' in permalink.text


async def test_comment_errors(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = await thread_with_link(client)
    too_long = 'x' * (comments_service.MAX_COMMENT + 1)
    body = await post_json(
        client, '/api/comment', {'uh': modhash, 'thing_id': 't3_1', 'text': too_long}
    )
    assert codes(body) == [('TOO_LONG', 'text')]
    missing = await client.post(
        '/api/comment', data={'uh': modhash, 'thing_id': 't3_9', 'text': 'x', 'api_type': 'json'}
    )
    assert missing.status_code == 403
    assert codes(missing.json()['json']) == [('NO_THING_ID', 'parent')]
    logged_out = await other_client.post(
        '/api/comment', data={'thing_id': 't3_1', 'text': 'x', 'api_type': 'json'}
    )
    assert codes(logged_out.json()['json']) == [('USER_REQUIRED', None)]


async def test_permalink_and_context(client: AsyncTestClient[Litestar]) -> None:
    modhash = await thread_with_link(client)
    parent = 't3_1'
    for depth in range(4):
        parent = (await reply(client, modhash, parent, f'level {depth}'))['data']['id']
    focus = (await client.get('/in/pics/comments/1/discuss/3.json?context=1')).json()
    (outer,) = thread_json(focus)
    assert outer['data']['body'] == 'level 1'
    inner = outer['data']['replies']['data']['children'][0]['data']
    # Depths count from the top of the page, as on r2.
    assert (inner['body'], inner['depth']) == ('level 2', 1)
    deeper = inner['replies']['data']['children'][0]['data']
    assert deeper['body'] == 'level 3'
    wrong = await client.get('/in/elsewhere/comments/1/discuss/3/', follow_redirects=False)
    assert wrong.headers['location'] == '/in/pics/comments/1/discuss/3/'
    assert (await client.get('/in/pics/comments/1/discuss/zz/')).status_code == 404


async def test_limits_leave_more_and_continue(client: AsyncTestClient[Litestar]) -> None:
    modhash = await thread_with_link(client)
    for n in range(3):
        await reply(client, modhash, 't3_1', f'top {n}')
    parent = 't1_1'
    for depth in range(3):
        parent = (await reply(client, modhash, parent, f'deep {depth}'))['data']['id']

    limited = thread_json((await client.get('/in/pics/comments/1.json?limit=2&sort=old')).json())
    # Oldest first, two fit: top 0 and top 1. top 0's reply and top 2 are left for later.
    assert [c['kind'] for c in limited] == ['t1', 't1', 'more']
    more = limited[-1]['data']
    assert (more['parent_id'], more['children']) == ('t3_1', ['3'])
    first_more = limited[0]['data']['replies']['data']['children'][0]
    assert (first_more['kind'], first_more['data']['children']) == ('more', ['4'])

    shallow = thread_json((await client.get('/in/pics/comments/1.json?depth=2&sort=old')).json())
    html = await client.get('/in/pics/comments/1/discuss/?depth=2&sort=old')
    assert 'more replies further down this thread' in html.text
    assert 'load more comments' not in html.text
    first_reply = shallow[0]['data']['replies']['data']['children'][0]['data']
    assert first_reply['replies'] == ''

    loaded = await post_json(
        client, '/api/morechildren', {'link_id': 't3_1', 'children': '3', 'sort': 'old'}
    )
    (thing,) = loaded['data']['things']
    assert (thing['data']['id'], thing['data']['parent']) == ('t1_3', 't3_1')
    assert 'top 2' in thing['data']['content']


async def test_sorts(
    client: AsyncTestClient[Litestar], settings: Settings, published: list[Message[Any]]
) -> None:
    modhash = await thread_with_link(client)
    for name in ('quiet', 'loved', 'hated'):
        await reply(client, modhash, 't3_1', name)
    await vote(settings, 't1_2', ups=5, downs=0, prefix='a')
    await vote(settings, 't1_3', ups=0, downs=5, prefix='b')
    for fullname in ('t1_1', 't1_2', 't1_3'):
        await recount(published, fullname)
    await run_published(published)

    def bodies(page: list[dict[str, Any]]) -> list[str]:
        return [c['data']['body'] for c in thread_json(page)]

    url = '/in/pics/comments/1.json?sort='
    assert bodies((await client.get(url + 'top')).json()) == ['loved', 'quiet', 'hated']
    assert bodies((await client.get(url + 'new')).json()) == ['hated', 'loved', 'quiet']
    assert bodies((await client.get(url + 'old')).json()) == ['quiet', 'loved', 'hated']
    assert bodies((await client.get(url + 'confidence')).json())[0] == 'loved'


async def recount(published: list[Message[Any]], fullname: str) -> None:
    from app.jobs.scores import recount_comment  # noqa: PLC0415

    published.append(recount_comment.message(fullname))


async def test_voting(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    modhash = await thread_with_link(client)
    await reply(client, modhash, 't3_1', 'vote on me')
    await run_published(published)
    bob = (await register(other_client, 'bob'))['data']['modhash']

    body = await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '1'})
    assert body['errors'] == []
    assert [m.actor_name for m in published] == ['recount_link']
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't1_1', 'dir': '-1'})
    await run_published(published)
    page = (await other_client.get('/in/pics/comments/1.json')).json()
    link = page[0]['data']['children'][0]['data']
    comment = thread_json(page)[0]['data']
    assert (link['score'], link['likes']) == (2, True)
    assert (comment['score'], comment['likes']) == (0, False)

    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '0'})
    await run_published(published)
    link = (await other_client.get('/in/pics/comments/1.json')).json()[0]['data']['children'][0][
        'data'
    ]
    assert (link['score'], link['likes']) == (1, None)

    without_js = await other_client.post(
        '/api/vote',
        data={'uh': bob, 'id': 't3_1', 'dir': '1', 'dest': '/in/pics/'},
        follow_redirects=False,
    )
    assert without_js.headers['location'] == '/in/pics/'
    for fields, expected in (
        ({'id': 't3_1', 'dir': '2'}, [('BAD_NUMBER', 'dir')]),
        ({'id': 't3_99', 'dir': '1'}, [('NO_THING_ID', 'id')]),
        ({'id': 'nonsense', 'dir': '1'}, [('NO_THING_ID', 'id')]),
    ):
        assert codes(await post_json(other_client, '/api/vote', {'uh': bob, **fields})) == expected
    no_csrf = await other_client.post(
        '/api/vote', data={'id': 't3_1', 'dir': '1', 'api_type': 'json'}
    )
    assert no_csrf.status_code == 403


async def test_hide_voted_links(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = await thread_with_link(client)
    bob = (await register(other_client, 'bob'))['data']['modhash']
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '1'})
    assert len((await other_client.get('/in/pics.json')).json()['data']['children']) == 1
    form = {'uh': bob, 'hide_ups': 'on', 'numsites': '25'}
    await other_client.post('/post/options', data=form)
    assert (await other_client.get('/in/pics.json')).json()['data']['children'] == []
    # Never your own: alice upvoted her link on submitting it.
    await client.post('/post/options', data={**form, 'uh': modhash})
    assert len((await client.get('/in/pics.json')).json()['data']['children']) == 1


async def test_large_threads_use_the_precomputed_tree(
    client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(comments_service, 'PRECOMPUTED_THRESHOLD', 2)
    monkeypatch.setattr(threads, 'PRECOMPUTED_THRESHOLD', 2)
    from app.controllers import comments as controller  # noqa: PLC0415

    monkeypatch.setattr(controller, 'PRECOMPUTED_THRESHOLD', 2)
    modhash = await thread_with_link(client)
    await reply(client, modhash, 't3_1', 'one')
    await run_published(published)
    await reply(client, modhash, 't3_1', 'two')
    assert 'rebuild_tree' in [m.actor_name for m in published]
    await run_published(published)
    await reply(client, modhash, 't3_1', 'three')
    await run_published(published)
    page = (await client.get('/in/pics/comments/1.json?sort=old')).json()
    assert [c['data']['body'] for c in thread_json(page)] == ['one', 'two', 'three']


async def test_static_files_and_csp(client: AsyncTestClient[Litestar]) -> None:
    script = await client.get('/static/js/main.js')
    assert script.status_code == 200
    assert 'enableVoting' in script.text
    page = await client.get('/login')
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert '<script type="module" src="/static/js/main.js"></script>' in page.text
