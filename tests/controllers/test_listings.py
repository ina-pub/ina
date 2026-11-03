from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ids import Fullname
from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.links import PgVoteRepo
from tests.controllers.test_communities import create
from tests.helpers import codes, post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


async def submit(client: AsyncTestClient[Litestar], modhash: str, **form: str) -> dict[str, Any]:
    fields = {'uh': modhash, 'community': 'pics', 'kind': 'link', 'sendreplies': 'on', **form}
    return await post_json(client, '/api/submit', fields)


async def submitted(client: AsyncTestClient[Litestar], modhash: str, **form: str) -> str:
    body = await submit(client, modhash, **form)
    assert body['errors'] == [], body
    name: str = body['data']['name']
    return name


async def vote(settings: Settings, fullname: str, ups: int, downs: int, prefix: str) -> None:
    """Votes straight into the table, from new accounts; the vote endpoints come later."""
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        accounts, votes = PgAccountRepo(session), PgVoteRepo(session)
        for n in range(ups + downs):
            voter = await accounts.create(name=f'{prefix}{n}', password_hash='h', email=None)
            await votes.cast(voter.id, Fullname.parse(fullname), 1 if n < ups else -1)
    await engine.dispose()


def titles(listing: dict[str, Any]) -> list[str]:
    return [child['data']['title'] for child in listing['data']['children']]


async def test_submit_a_link(
    client: AsyncTestClient[Litestar], published: list[Message[Any]]
) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    body = await submit(client, modhash, title='A picture', url='example.com/cat.jpg')
    assert body['errors'] == []
    assert body['data'] == {'url': '/in/pics/comments/1/a_picture/', 'id': '1', 'name': 't3_1'}
    # Creating the community indexed it; the submit published its recount.
    assert [m.actor_name for m in published] == ['index_community', 'recount_link']

    listing = (await client.get('/in/pics.json')).json()
    (child,) = listing['data']['children']
    data = child['data']
    assert child['kind'] == 't3'
    assert (data['title'], data['url'], data['domain']) == (
        'A picture',
        'http://example.com/cat.jpg',
        'example.com',
    )
    assert (data['score'], data['ups'], data['downs'], data['likes']) == (1, 1, 0, True)
    assert (data['author'], data['community'], data['community_id']) == ('alice', 'pics', 't5_1')

    page = await client.get('/in/pics/comments/1/a_picture/')
    assert page.status_code == 200
    assert 'href="http://example.com/cat.jpg"' in page.text
    two = (await client.get('/in/pics/comments/1.json')).json()
    assert [part['kind'] for part in two] == ['Listing', 'Listing']
    assert two[0]['data']['children'][0]['data']['name'] == 't3_1'


async def test_submit_a_self_post(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    await submitted(client, modhash, kind='self', title='Ask me', text='**anything**')
    data = (await client.get('/in/pics/new.json')).json()['data']['children'][0]['data']
    assert data['is_self'] is True
    assert data['domain'] == 'self.pics'
    assert data['url'] == data['permalink'] == '/in/pics/comments/1/ask_me/'
    assert data['selftext_html'] == '<p><strong>anything</strong></p>\n'
    page = await client.get(data['permalink'])
    assert '<div class="md"><p><strong>anything</strong></p>' in page.text


async def test_submit_without_js(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    form = {'uh': modhash, 'community': 'pics', 'kind': 'link', 'title': 'hi', 'url': 'x.org'}
    response = await client.post('/api/submit', data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/in/pics/comments/1/hi/'
    page = await client.get('/submit')
    assert 'name="sendreplies"' in page.text
    bad = await client.post('/api/submit', data={**form, 'url': ''})
    assert 'class="error NO_URL field-url"' in bad.text


async def test_submit_errors(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    await create(client, modhash, name='textonly', link_type='self')
    await create(client, modhash, name='locked', type='restricted')
    await submitted(client, modhash, title='first', url='https://example.com/a')

    cases = [
        ({'url': ''}, [('NO_URL', 'url')]),
        ({'url': 'javascript:alert(1)'}, [('BAD_URL', 'url')]),
        ({'title': ''}, [('NO_TEXT', 'title')]),
        ({'title': 'x' * 301}, [('TOO_LONG', 'title')]),
        ({'url': 'https://example.com/a'}, [('ALREADY_SUB', 'url')]),
        ({'community': 'textonly'}, [('NO_LINKS', 'community')]),
        ({'community': 'nowhere'}, [('COMMUNITY_NOEXIST', 'community')]),
        ({'community': ''}, [('COMMUNITY_REQUIRED', 'community')]),
        ({'kind': 'video'}, [('INVALID_OPTION', 'kind')]),
    ]
    for change, expected in cases:
        form = {'title': 'a title', 'url': 'https://example.com/b', **change}
        assert codes(await submit(client, modhash, **form)) == expected, change
    again = {'title': 'again', 'url': 'https://example.com/a', 'resubmit': 'on'}
    assert (await submit(client, modhash, **again))['errors'] == []

    bob = (await register(other_client, 'bob'))['data']['modhash']
    denied = await other_client.post(
        '/api/submit',
        data={'uh': bob, 'community': 'locked', 'kind': 'link', 'title': 't', 'url': 'x.org'},
        headers={'Accept': 'application/json'},
    )
    assert codes(denied.json()['json']) == [('COMMUNITY_NOTALLOWED', 'community')]


async def test_scores_and_cache_follow_the_votes(
    client: AsyncTestClient[Litestar], published: list[Message[Any]], settings: Settings
) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    first = await submitted(client, modhash, title='first', url='a.com')
    second = await submitted(client, modhash, title='second', url='b.com')
    # Each recount reindexes its link, after the others already waiting.
    assert await run_published(published) == [
        'index_community',
        'recount_link',
        'recount_link',
        'index_link',
        'index_link',
    ]
    assert titles((await client.get('/in/pics/top.json')).json()) == ['second', 'first']

    await vote(settings, first, ups=5, downs=1, prefix='v')
    # Until the job runs, the cached listing stands.
    assert titles((await client.get('/in/pics/top.json')).json()) == ['second', 'first']
    published.append(published_message(first))
    await run_published(published)
    top = (await client.get('/in/pics/top.json')).json()
    assert titles(top) == ['first', 'second']
    assert [child['data']['score'] for child in top['data']['children']] == [5, 1]
    assert top['data']['children'][1]['data']['name'] == second


def published_message(fullname: str) -> Message[Any]:
    from app.jobs.scores import recount_link  # noqa: PLC0415

    return recount_link.message(fullname)


async def test_pages(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    for n in range(5):
        await submitted(client, modhash, title=f'link{n}', url=f'x{n}.org')
    first = (await client.get('/in/pics/new.json?limit=2')).json()['data']
    assert titles({'data': first}) == ['link4', 'link3']
    assert (first['after'], first['before']) == ('t3_4', None)
    second = (await client.get('/in/pics/new.json?limit=2&count=2&after=t3_4')).json()['data']
    assert titles({'data': second}) == ['link2', 'link1']
    assert (second['after'], second['before']) == ('t3_2', 't3_3')
    back = (await client.get('/in/pics/new.json?limit=2&count=3&before=t3_3')).json()['data']
    assert titles({'data': back}) == ['link4', 'link3']
    assert back['before'] is None
    last = (await client.get('/in/pics/new.json?limit=2&after=t3_2')).json()['data']
    assert titles({'data': last}) == ['link0']
    assert last['after'] is None

    html = await client.get('/in/pics/new/?limit=2&count=2&after=t3_4')
    assert '<span class="rank">3</span>' in html.text
    assert '?count=4&amp;after=t3_2' in html.text
    assert '?count=3&amp;before=t3_3' in html.text


async def test_front_page_and_all(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash, name='pics')
    await create(client, modhash, name='hidden', allow_top='')
    await create(client, modhash, name='secret', type='private')
    await create(client, modhash, name='racy', over_18='on')
    await submitted(client, modhash, community='pics', title='in pics', url='a.org')
    await submitted(client, modhash, community='hidden', title='in hidden', url='b.org')
    await submitted(client, modhash, community='secret', title='in secret', url='c.org')
    await submitted(client, modhash, community='racy', title='in racy', url='d.org')

    logged_out = (await other_client.get('/.json')).json()
    assert titles(logged_out) == ['in pics']
    everything = (await other_client.get('/in/all/new.json')).json()
    assert titles(everything) == ['in pics']

    bob = (await register(other_client, 'bob'))['data']['modhash']
    await post_json(
        other_client, '/api/subscribe', {'uh': bob, 'action': 'sub', 'community_name': 'hidden'}
    )
    # A first subscription brings the defaults along.
    front = (await other_client.get('/new.json')).json()
    assert titles(front) == ['in hidden', 'in pics']
    page = await other_client.get('/')
    assert '>in/hidden</a>' in page.text


async def test_link_page_urls(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    await create(client, modhash)
    await submitted(client, modhash, title='Hello there', url='a.org')
    moved = await client.get('/in/elsewhere/comments/1/', follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers['location'] == '/in/pics/comments/1/hello_there/'
    assert (await client.get('/in/pics/comments/zz/')).status_code == 404
    assert (await client.get('/in/pics/comments/NOT-36/')).status_code == 404
    assert (await client.get('/in/pics/gilded')).status_code == 404
