from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply, thread_with_link
from tests.controllers.test_listings import published_message, submitted, vote
from tests.controllers.test_moderation import act
from tests.helpers import codes, find_account, post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings

PRIVATE = {'modhash', 'inbox_count', 'has_mail', 'has_mod_mail', 'over_18', 'is_suspended'}


async def test_profile_page(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await register(client, 'Alice')
    page = await other_client.get('/user/alice')
    assert page.status_code == 200
    assert '<title>overview for Alice</title>' in page.text
    assert 'post karma' in page.text
    listing = (await other_client.get('/user/alice.json')).json()
    assert listing['kind'] == 'Listing'
    assert listing['data']['children'] == []


async def test_about_is_a_t2(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    public = (await other_client.get('/user/alice/about.json')).json()
    assert public['kind'] == 't2'
    assert public['data']['name'] == 'alice'
    assert public['data']['id'] == '1'
    assert not PRIVATE & public['data'].keys()
    own = (await client.get('/user/alice/about.json')).json()
    assert own['data'].keys() >= PRIVATE
    assert own['data']['modhash'] == modhash


async def test_me(client: AsyncTestClient[Litestar]) -> None:
    await register(client, 'alice')
    wrapped = (await client.get('/api/me.json')).json()
    assert wrapped['kind'] == 't2'
    assert wrapped['data']['name'] == 'alice'
    redirect = await client.get('/user/me', follow_redirects=False)
    assert redirect.headers['location'] == '/user/alice'


async def test_short_user_urls_redirect(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/u/alice', follow_redirects=False)
    assert response.status_code == 301
    assert response.headers['location'] == '/user/alice'
    deep = await client.get('/u/alice/about', follow_redirects=False)
    assert deep.headers['location'] == '/user/alice/about'


async def test_unknown_user_is_404(client: AsyncTestClient[Litestar]) -> None:
    page = await client.get('/user/nobody')
    assert page.status_code == 404
    assert 'there&#39;s nothing at this address' in page.text
    data = await client.get('/user/nobody.json')
    assert data.status_code == 404
    assert codes(data.json()['json']) == [('NOT_FOUND', None)]


async def test_api_needs_a_login(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/api/v1/me', follow_redirects=False)
    assert response.status_code == 403
    assert codes(response.json()['json']) == [('USER_REQUIRED', None)]


def kinds_and_names(listing: dict[str, Any]) -> list[tuple[str, str]]:
    return [(child['kind'], child['data']['name']) for child in listing['data']['children']]


async def posted(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> tuple[str, str]:
    """Alice runs /in/pics and posts two links; bob comments on the first."""
    alice = await thread_with_link(client)
    await submitted(client, alice, title='Second', url='b.org')
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    await reply(other_client, bob, 't3_1', 'first comment')
    return alice, bob


async def test_profile_tabs(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await posted(client, other_client)
    await reply(client, alice, 't1_1', 'a reply')
    overview = (await other_client.get('/user/alice.json')).json()
    assert kinds_and_names(overview) == [('t1', 't1_2'), ('t3', 't3_2'), ('t3', 't3_1')]
    comment = overview['data']['children'][0]['data']
    assert (comment['link_title'], comment['link_author'], comment['body']) == (
        'Discuss',
        'alice',
        'a reply',
    )
    assert comment['permalink'] == '/in/pics/comments/1/discuss/2/'
    assert kinds_and_names((await other_client.get('/user/alice/submitted.json')).json()) == [
        ('t3', 't3_2'),
        ('t3', 't3_1'),
    ]
    assert kinds_and_names((await other_client.get('/user/bob/comments.json')).json()) == [
        ('t1', 't1_1')
    ]
    assert (await other_client.get('/user/alice/nonsense')).status_code == 404

    page = (await client.get('/user/bob')).text
    assert '<title>overview for bob</title>' in page
    assert 'href="/user/bob/comments/"' in page
    assert 'whole thread (' in page
    assert 'first comment' in page
    assert 'href="/user/bob/upvoted/"' not in page
    # A removed link stays on its author's own profile, and leaves everyone else's view.
    await act(client, '/api/remove', alice, id='t3_2')
    assert kinds_and_names((await other_client.get('/user/alice/submitted.json')).json()) == [
        ('t3', 't3_1')
    ]
    own = (await client.get('/user/alice/submitted.json')).json()
    assert kinds_and_names(own) == [('t3', 't3_2'), ('t3', 't3_1')]


async def test_sorts_and_pages(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
    settings: Settings,
) -> None:
    await posted(client, other_client)
    await vote(settings, 't3_1', 3, 0, 'up')
    published.append(published_message('t3_1'))
    await run_published(published)
    top = (await other_client.get('/user/alice/submitted.json?sort=top')).json()
    assert kinds_and_names(top) == [('t3', 't3_1'), ('t3', 't3_2')]
    first = (await other_client.get('/user/alice/submitted.json?limit=1')).json()
    assert kinds_and_names(first) == [('t3', 't3_2')]
    assert first['data']['after'] == 't3_2'
    rest = (await other_client.get('/user/alice/submitted.json?limit=1&after=t3_2')).json()
    assert kinds_and_names(rest) == [('t3', 't3_1')]
    assert rest['data']['after'] is None
    page = (await other_client.get('/user/alice/submitted/?limit=1')).text
    assert 'href="?count=1&amp;after=t3_2"' in page
    timed = (await other_client.get('/user/alice/submitted/?sort=top&t=day')).text
    assert 'from the' in timed


async def test_vote_tabs(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    _, bob = await posted(client, other_client)
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '1'})
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_2', 'dir': '-1'})
    upvoted = (await other_client.get('/user/bob/upvoted.json')).json()
    assert kinds_and_names(upvoted) == [('t3', 't3_1')]
    assert upvoted['data']['children'][0]['data']['likes'] is True
    assert kinds_and_names((await other_client.get('/user/bob/downvoted.json')).json()) == [
        ('t3', 't3_2')
    ]
    # Votes are private unless their owner makes them public.
    assert (await client.get('/user/bob/upvoted')).status_code == 404
    await other_client.post(
        '/post/options', data={'uh': bob, 'public_votes': 'on'}, follow_redirects=False
    )
    assert kinds_and_names((await client.get('/user/bob/upvoted.json')).json()) == [('t3', 't3_1')]
    assert 'href="/user/bob/upvoted/"' in (await client.get('/user/bob')).text


async def test_karma_follows_votes(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
    settings: Settings,
) -> None:
    alice, bob = await posted(client, other_client)
    await run_published(published)
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '1'})
    await post_json(client, '/api/vote', {'uh': alice, 'id': 't1_1', 'dir': '1'})
    await run_published(published)
    assert (await find_account(settings, 'alice')).link_karma == 1
    assert (await find_account(settings, 'bob')).comment_karma == 1
    # The score counts bob's vote already; taking it back shows one fewer.
    upvoted = (await other_client.get('/user/bob/upvoted')).text
    assert '<div class="score unvoted">1</div>' in upvoted
    assert '<div class="score likes">2</div>' in upvoted
    about = (await other_client.get('/user/alice/about.json')).json()['data']
    assert (about['link_karma'], about['comment_karma']) == (1, 0)
    # Self posts earn none, as on r2.
    await submitted(client, alice, kind='self', title='Words', text='hi')
    await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_3', 'dir': '1'})
    await run_published(published)
    assert (await find_account(settings, 'alice')).link_karma == 1
