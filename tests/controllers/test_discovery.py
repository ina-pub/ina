from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.tables import links
from tests.controllers.test_communities import create
from tests.controllers.test_listings import published_message, submitted, titles, vote
from tests.helpers import register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


async def two_communities(client: AsyncTestClient[Litestar]) -> str:
    """Alice runs /in/pics and /in/news, and a private /in/secret, each with a link to a.org."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice, name='pics', title='pics')
    await create(client, alice, name='news', title='news')
    await create(client, alice, name='secret', title='secret', type='private')
    await submitted(client, alice, community='pics', title='in pics', url='https://a.org/story')
    await submitted(client, alice, community='news', title='in news', url='https://a.org/story')
    await submitted(client, alice, community='secret', title='in secret', url='https://a.org/story')
    await submitted(client, alice, community='news', title='elsewhere', url='https://b.org/')
    return alice


async def test_domain_listing(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await two_communities(client)
    listing = (await other_client.get('/domain/a.org/new.json')).json()
    assert titles(listing) == ['in news', 'in pics']
    page = await other_client.get('/domain/A.org/')
    assert '<title>a.org</title>' in page.text
    assert 'href="/domain/a.org/new/"' in page.text
    assert titles((await other_client.get('/domain/nowhere.example.json')).json()) == []


async def test_rising(
    client: AsyncTestClient[Litestar], published: list[Message[Any]], settings: Settings
) -> None:
    alice = await two_communities(client)
    await submitted(client, alice, community='pics', title='old', url='https://c.org/')
    await submitted(client, alice, community='pics', title='popular', url='https://d.org/')
    await vote(settings, 't3_6', 4, 0, 'v')
    await vote(settings, 't3_5', 9, 0, 'w')
    published.append(published_message('t3_6'))
    published.append(published_message('t3_5'))
    await run_published(published)
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        old = datetime.now(UTC) - timedelta(days=2)
        await session.execute(update(links).where(links.c.id == 5).values(created=old))
    await engine.dispose()

    # The last day's links, by score per hour; a link older than a day is not rising.
    rising = (await client.get('/in/pics/rising.json')).json()
    assert titles(rising) == ['popular', 'in pics']
    first = (await client.get('/in/pics/rising.json?limit=1')).json()
    assert (titles(first), first['data']['after']) == (['popular'], 't3_6')
    rest = (await client.get('/in/pics/rising.json?limit=1&after=t3_6')).json()
    assert titles(rest) == ['in pics']
    assert 'href="/in/pics/rising/"' in (await client.get('/in/pics/')).text
    assert (await client.get('/rising.json')).status_code == 200


async def test_random_community(client: AsyncTestClient[Litestar]) -> None:
    await two_communities(client)
    found = await client.get('/in/random', follow_redirects=False)
    assert found.headers['location'] in {'/in/pics/', '/in/news/'}


async def test_other_discussions(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await two_communities(client)
    await register(other_client, 'bob')
    original, others = (await other_client.get('/in/pics/duplicates/1.json')).json()
    assert titles(original) == ['in pics']
    # The private community's copy is only for its members.
    assert titles(others) == ['in news']
    assert titles((await client.get('/in/pics/duplicates/1.json')).json()[1]) == [
        'in secret',
        'in news',
    ]
    page = (await other_client.get('/in/pics/duplicates/1/in_pics/')).text
    assert 'elsewhere (1)' in page
    assert 'href="/in/pics/duplicates/1/"' in (await other_client.get('/in/pics/comments/1/')).text


async def test_community_bar_and_footer(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await two_communities(client)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    # Without subscriptions, the defaults; with them, one's own.
    page = (await other_client.get('/')).text
    assert '<a href="/in/news/" class="choice">news</a>' in page
    assert 'ina is free software' in page
    assert '<a href="https://github.com/ina-pub/ina">Its source code</a>' in page
    # A first subscription keeps the defaults, as on r2; then leave one of them.
    for action, fullname in (('sub', 't5_1'), ('unsub', 't5_2')):
        form = {'uh': bob, 'action': action, 'community': fullname}
        await other_client.post('/api/subscribe', data=form)
    page = (await other_client.get('/')).text
    assert '<a href="/in/pics/" class="choice">pics</a>' in page
    assert '<a href="/in/news/" class="choice">news</a>' not in page
    assert 'id="community-bar"' not in (await other_client.get('/.json')).text


async def test_expandos(client: AsyncTestClient[Litestar]) -> None:
    alice = await two_communities(client)
    await submitted(client, alice, community='pics', kind='self', title='Words', text='*hello*')
    await submitted(client, alice, community='pics', title='A cat', url='https://i.example/cat.png')
    page = (await client.get('/in/pics/new/')).text
    assert 'class="expando-button collapsed selftext"' in page
    assert '<div class="md"><p><em>hello</em></p>' in page
    assert 'class="expando-button collapsed image"' in page
    assert 'data-src="https://i.example/cat.png"' in page
    # A plain link has none, and a self post's own page shows its text instead.
    assert page.count('expando-button') == 2
    assert 'expando-button' not in (await client.get('/in/pics/comments/5/')).text
