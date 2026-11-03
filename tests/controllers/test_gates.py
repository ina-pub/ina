from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.tables import accounts, communities, links
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.helpers import codes, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient
    from sqlalchemy.sql.dml import Update

    from app.settings import Settings


async def execute(settings: Settings, statement: Update) -> None:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        await session.execute(statement)
    await engine.dispose()


async def test_over_18(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], settings: Settings
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice, name='adult', title='adult', over_18='on')
    await create(client, alice, name='pics', title='pics')
    await submitted(client, alice, community='adult', title='hot take', url='https://a.org/')
    await submitted(client, alice, community='pics', title='careful', url='https://b.org/')

    # Logged out: asked first, then a cookie remembers the answer.
    asked = await other_client.get('/in/adult/new/?t=day', follow_redirects=False)
    assert asked.headers['location'] == '/over18?dest=%2Fin%2Fadult%2Fnew%3Ft%3Dday'
    page = (await other_client.get('/over18?dest=/in/adult/')).text
    assert 'This community is for adults' in page
    assert (await other_client.get('/in/adult.json')).status_code == 200
    no = await other_client.post(
        '/over18', data={'over18': 'no', 'dest': '/in/adult/'}, follow_redirects=False
    )
    assert no.headers['location'] == '/'
    yes = await other_client.post(
        '/over18', data={'over18': 'yes', 'dest': '/in/adult/'}, follow_redirects=False
    )
    assert yes.headers['location'] == '/in/adult/'
    assert (await other_client.get('/in/adult/', follow_redirects=False)).status_code == 200

    # An NSFW link in a safe community asks too; logged in, the answer is a preference.
    await execute(settings, update(links).where(links.c.id == 2).values(nsfw=True))
    assert (await client.get('/in/pics/', follow_redirects=False)).status_code == 200
    asked = await client.get('/in/pics/comments/2/careful/', follow_redirects=False)
    assert asked.headers['location'].startswith('/over18?')
    agreed = await client.post(
        '/over18', data={'uh': alice, 'over18': 'yes', 'dest': '/in/pics/'}, follow_redirects=False
    )
    assert agreed.headers['location'] == '/in/pics/', agreed.text
    assert (await client.get('/api/v1/me/prefs')).json()['over_18'] is True
    shown = await client.get('/in/pics/comments/2/careful/', follow_redirects=False)
    assert shown.status_code == 200


async def test_quarantine(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], settings: Settings
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice, name='edgy', title='edgy')
    quarantined = communities.c.settings.op('||')(text('\'{"quarantine": true}\'::jsonb'))
    await execute(settings, update(communities).values(settings=quarantined))

    sent = await other_client.get('/in/edgy/', follow_redirects=False)
    assert sent.headers['location'] == '/quarantine?dest=%2Fin%2Fedgy'
    page = await other_client.get('/quarantine?dest=/in/edgy/')
    assert page.status_code == 403
    assert '<a href="/login?dest=' in page.text
    refused = await other_client.get('/in/edgy.json')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('QUARANTINED', None)]

    # Opting in takes a confirmed email address.
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    assert (
        'confirmed email address</a>' in (await other_client.get('/quarantine?dest=/in/edgy/')).text
    )
    early = await other_client.post(
        '/api/quarantine_optin', data={'uh': bob, 'community_name': 'edgy', 'api_type': 'json'}
    )
    assert early.status_code == 403
    await execute(settings, update(accounts).values(email_verified=True))
    assert (
        'Do you want to see it anyway?'
        in (await other_client.get('/quarantine?dest=/in/edgy/')).text
    )
    opted = await other_client.post(
        '/quarantine',
        data={'uh': bob, 'community_name': 'edgy', 'accept': 'yes', 'dest': '/in/edgy/'},
        follow_redirects=False,
    )
    assert opted.headers['location'] == '/in/edgy/'
    assert (await other_client.get('/in/edgy/', follow_redirects=False)).status_code == 200
    assert (await other_client.get('/quarantine?dest=/in/edgy/', follow_redirects=False)).headers[
        'location'
    ] == '/in/edgy/'
    out = await other_client.post(
        '/api/quarantine_optout', data={'uh': bob, 'community_name': 'edgy'}, follow_redirects=False
    )
    assert out.headers['location'] == '/'
    assert (await other_client.get('/in/edgy/', follow_redirects=False)).status_code == 302
