from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from litestar.testing import AsyncTestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.jobs import search as search_jobs
from app.main import create_app
from app.repos.pg.tables import links
from app.search import create_search
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.controllers.test_moderation import act
from tests.helpers import post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar

    from app.settings import Settings


@pytest.fixture(autouse=True)
async def empty_indices(settings: Settings) -> None:
    """Each test starts with empty indices; other tests' documents would otherwise match."""
    search = create_search(settings)
    await search.recreate()
    await search.aclose()


async def posts(client: AsyncTestClient[Litestar], published: list[Message[Any]]) -> str:
    """Alice moderates /in/pics and /in/dogs, with a few posts in each, all indexed."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    await create(client, alice, name='dogs', title='Dogs', description='all about dogs')
    await submitted(client, alice, title='My cat sitting in a box', url='https://i.example.com/1')
    await submitted(client, alice, title='Two black cats', url='https://other.org/cats')
    await submitted(
        client, alice, kind='self', title='Meta: rules', text='Be nice to **cats** here'
    )
    await submitted(
        client, alice, community='dogs', title='A dog and a cat', url='https://dog.example.com/'
    )
    await run_published(published)
    return alice


def titles(response: Any) -> list[str]:  # noqa: ANN401
    return [child['data']['title'] for child in response.json()['data']['children']]


async def test_words_fields_and_operators(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)

    async def search(q: str, **params: str) -> list[str]:
        return sorted(titles(await client.get('/search.json', params={'q': q, **params})))

    # Stemmed: "cat" finds "cats", in titles and self text.
    assert await search('cat') == [
        'A dog and a cat',
        'Meta: rules',
        'My cat sitting in a box',
        'Two black cats',
    ]
    assert await search('cat in:dogs') == ['A dog and a cat']
    assert await search('cat -in:dogs NOT self:yes') == [
        'My cat sitting in a box',
        'Two black cats',
    ]
    assert await search('site:example.com') == ['A dog and a cat', 'My cat sitting in a box']
    assert await search('"black cats" OR title:box') == [
        'My cat sitting in a box',
        'Two black cats',
    ]
    assert await search('author:alice self:yes') == ['Meta: rules']
    assert await search('nothing matches this') == []
    assert await search('') == []


async def test_sorts_and_pages(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    newest = titles(await client.get('/search.json', params={'q': 'cat', 'sort': 'new'}))
    assert newest == ['A dog and a cat', 'Meta: rules', 'Two black cats', 'My cat sitting in a box']

    first = await client.get('/search.json', params={'q': 'cat', 'sort': 'new', 'limit': '3'})
    assert titles(first) == newest[:3]
    after = first.json()['data']['after']
    assert after is not None
    assert first.json()['data']['before'] is None
    second = await client.get(
        '/search.json',
        params={'q': 'cat', 'sort': 'new', 'limit': '3', 'count': '3', 'after': after},
    )
    assert titles(second) == newest[3:]
    assert second.json()['data']['after'] is None
    assert second.json()['data']['before'] is not None

    page = await client.get('/search', params={'q': 'cat', 'sort': 'new', 'limit': '3'})
    assert 'href="?q=cat&amp;sort=new&amp;count=3&amp;after=' in page.text
    assert '<span class="rank">1</span>' in page.text


async def test_time_filter(
    settings: Settings, client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        two_days = datetime.now(UTC) - timedelta(days=2)
        await session.execute(update(links).where(links.c.id == 1).values(created=two_days))
    await engine.dispose()
    await search_jobs.link_indexing('t3_1')

    async def found(t: str) -> list[str]:
        response = await client.get('/search.json', params={'q': 'cat', 't': t, 'sort': 'new'})
        return titles(response)

    assert 'My cat sitting in a box' in await found('all')
    assert 'My cat sitting in a box' in await found('week')
    assert await found('day') == ['A dog and a cat', 'Meta: rules', 'Two black cats']


async def test_restrict_to_a_community(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    everywhere = await client.get('/in/dogs/search.json', params={'q': 'cat'})
    assert len(titles(everywhere)) == 4
    here = await client.get('/in/dogs/search.json', params={'q': 'cat', 'restrict_community': 'on'})
    assert titles(here) == ['A dog and a cat']
    # With nothing to search for, a restricted search lists the community, as on r2.
    listed = await client.get('/in/dogs/search.json', params={'restrict_community': 'on'})
    assert titles(listed) == ['A dog and a cat']

    page = await client.get('/in/dogs/search', params={'q': 'cat', 'restrict_community': 'on'})
    assert '<title>dogs: search results</title>' in page.text
    assert 'only in in/dogs' in page.text
    # Every community page has the search box, starting limited to that community.
    sidebar = (await client.get('/in/dogs/')).text
    assert 'action="/in/dogs/search"' in sidebar
    assert 'name="restrict_community" checked' in sidebar


async def test_private_and_nsfw_communities(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await posts(client, published)
    await create(client, alice, name='secret', title='Secret', type='private')
    await create(client, alice, name='spicy', title='Spicy', over_18='on')
    await submitted(client, alice, community='secret', title='A secret cat', url='https://s.org/')
    await submitted(client, alice, community='spicy', title='A spicy cat', url='https://n.org/')
    await run_published(published)
    await register(other_client, 'bob')

    async def seen_by(browser: AsyncTestClient[Litestar], **params: str) -> list[str]:
        response = await browser.get('/search.json', params={'q': 'secret OR spicy', **params})
        return sorted(titles(response))

    # Bob is not a member of the private one and has not opted in to NSFW.
    assert await seen_by(other_client) == []
    assert await seen_by(client) == ['A secret cat']
    friend = {'uh': alice, 'community': 'secret', 'type': 'contributor', 'name': 'bob'}
    assert (await post_json(client, '/api/friend', friend))['errors'] == []
    assert await seen_by(other_client) == ['A secret cat']
    # Searching inside an NSFW community shows it, as its listing does.
    spicy = await other_client.get(
        '/in/spicy/search.json', params={'q': 'cat', 'restrict_community': 'on'}
    )
    assert titles(spicy) == ['A spicy cat']


async def test_removed_links_leave_the_index(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    alice = await posts(client, published)

    async def boxes() -> list[str]:
        return titles(await client.get('/search.json', params={'q': 'box'}))

    assert await boxes() == ['My cat sitting in a box']
    await act(client, '/api/remove', alice, id='t3_1', spam='false')
    await run_published(published)
    assert await boxes() == []
    await act(client, '/api/approve', alice, id='t3_1')
    await run_published(published)
    assert await boxes() == ['My cat sitting in a box']


async def test_unreadable_queries_are_cleaned(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    response = await client.get('/search.json', params={'q': '"black cats'})
    assert titles(response) == ['Two black cats']
    page = await client.get('/search', params={'q': '"black cats'})
    assert 'so this is a search for "black cats"' in page.text


async def test_community_results(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    # The site shows communities above the links; the API asks for them with type=community.
    page = await client.get('/search', params={'q': 'dogs'})
    assert 'class="search-title">in/dogs</a>' in page.text
    assert titles(await client.get('/search.json', params={'q': 'dogs'})) == ['A dog and a cat']
    only = await client.get('/search.json', params={'q': 'dogs', 'type': 'community'})
    assert [c['data']['display_name'] for c in only.json()['data']['children']] == ['dogs']
    both = (await client.get('/search.json', params={'q': 'dogs', 'type': 'link,community'})).json()
    assert [listing['data']['children'][0]['kind'] for listing in both] == ['t3', 't5']
    # A fielded query is about links only.
    fielded = await client.get('/search', params={'q': 'in:dogs'})
    assert 'search-result-community' not in fielded.text

    directory = await client.get('/ins/search.json', params={'q': 'dogs'})
    assert [c['data']['display_name'] for c in directory.json()['data']['children']] == ['dogs']
    assert 'value="dogs"' in (await client.get('/ins/search', params={'q': 'dogs'})).text


async def test_search_community_names(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    await posts(client, published)
    names = await client.get('/api/search_community_names', params={'query': 'D'})
    assert names.json() == {'names': ['dogs']}
    posted = await client.post('/api/search_community_names', data={'query': 'p'})
    assert posted.json() == {'names': ['pics']}
    exact = await client.get(
        '/api/search_community_names', params={'query': 'pics', 'exact': 'true'}
    )
    assert exact.json() == {'names': ['pics']}
    missing = await client.get(
        '/api/search_community_names', params={'query': 'pi', 'exact': 'true'}
    )
    assert missing.status_code == 404


async def test_search_down(settings: Settings) -> None:
    down = settings.model_copy(update={'opensearch_url': 'http://127.0.0.1:1'})
    async with AsyncTestClient(app=create_app(down)) as browser:
        page = await browser.get('/search', params={'q': 'cats'})
        assert page.status_code == 503
        assert 'search isn&#39;t answering right now' in page.text
        names = await browser.get('/api/search_community_names', params={'query': 'p'})
        assert names.json() == {'names': []}
