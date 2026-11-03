from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.admin import PgAdminRepo
from app.services import admin_mode, sessions
from tests.controllers.test_comments import reply
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.controllers.test_oauth import script_token
from tests.helpers import PASSWORD, codes, find_account, me, register

if TYPE_CHECKING:
    import httpx
    import pytest
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


async def make_admin(settings: Settings, name: str) -> None:
    """What scripts/admins.py grant does."""
    account = await find_account(settings, name)
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        await PgAccountRepo(session).set_admin(account.id, admin=True)
    await engine.dispose()


async def admin_on(
    client: AsyncTestClient[Litestar], modhash: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        '/admin/on',
        data={'uh': modhash, 'password': password, 'dest': '/in/pics/'},
        follow_redirects=False,
    )


async def remove(client: AsyncTestClient[Litestar], modhash: str) -> int:
    response = await client.post(
        '/api/remove', data={'uh': modhash, 'id': 't3_1', 'api_type': 'json'}
    )
    return response.status_code


async def bobs_link_and_alice(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> str:
    """Bob keeps in/pics with one link; alice, a site admin, has nothing to do with it."""
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    await create(other_client, bob)
    await submitted(other_client, bob, title="bob's", url='b.org')
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await make_admin(settings, 'alice')
    return alice


async def test_admin_mode(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    assert 'href="/admin/on?dest=/in/pics"' in (await client.get('/in/pics/')).text
    # Being an admin does nothing until admin mode is on.
    assert await remove(client, alice) == 403

    wrong = await admin_on(client, alice, password='nope')
    assert wrong.status_code == 200
    assert 'field-password' in wrong.text
    on = await admin_on(client, alice)
    assert on.headers['location'] == '/in/pics/'
    assert f'{admin_mode.COOKIE}=' in on.headers['set-cookie']
    assert 'HttpOnly' in on.headers['set-cookie']

    page = await client.get('/in/pics/')
    assert 'action="/admin/off"' in page.text
    # Each request renews it, so it ends after fifteen idle minutes.
    assert f'{admin_mode.COOKIE}=' in page.headers['set-cookie']
    # Every community is the admin's to keep.
    assert 'action="/api/remove"' in page.text
    assert await remove(client, alice) == 200
    assert (await client.get('/in/pics/about/log.json')).status_code == 200

    off = await client.post(
        '/admin/off', data={'uh': alice, 'dest': '/in/pics/'}, follow_redirects=False
    )
    assert 'Max-Age=0' in off.headers['set-cookie']
    assert await remove(client, alice) == 403
    assert 'href="/admin/on' in (await client.get('/in/pics/')).text


async def test_admin_mode_belongs_to_its_session(
    settings: Settings,
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    cookie = client.cookies.get(admin_mode.COOKIE)
    await client.post('/logout', data={'uh': alice})
    relogged: str = (
        await client.post(
            '/api/login', data={'user': 'alice', 'passwd': PASSWORD, 'api_type': 'json'}
        )
    ).json()['json']['data']['modhash']
    client.cookies.set(admin_mode.COOKIE, cookie or '')
    assert await remove(client, relogged) == 403
    client.cookies.delete(admin_mode.COOKIE)

    # And it lasts nine hours at most, however busy.
    await admin_on(client, relogged)
    assert await remove(client, relogged) == 200
    monkeypatch.setattr(admin_mode, 'LIFETIME', timedelta(seconds=-1))
    assert await remove(client, relogged) == 403


async def test_admin_pages_are_not_there_for_others(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await bobs_link_and_alice(settings, client, other_client)
    bob = sessions.decode(settings, other_client.cookies.get(sessions.COOKIE) or '')
    assert bob is not None
    assert (await other_client.get('/admin/on')).status_code == 404
    refused = await other_client.post(
        '/admin/on', data={'uh': bob.csrf, 'password': PASSWORD}, follow_redirects=False
    )
    assert refused.status_code == 404
    assert 'admin on' not in (await other_client.get('/in/pics/')).text


async def test_apps_never_act_as_admins(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    app = await script_token(client, alice, 'modposts')
    refused = await client.post('/api/remove', data={'id': 't3_1'}, headers=app)
    assert refused.status_code == 403


async def suspend(
    client: AsyncTestClient[Litestar], modhash: str, path: str = '/api/admin/suspend', **form: str
) -> httpx.Response:
    return await client.post(path, data={'uh': modhash, 'api_type': 'json', **form})


async def test_timeouts(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    bob = sessions.decode(settings, other_client.cookies.get(sessions.COOKIE) or '')
    assert bob is not None
    # Only in admin mode.
    refused = await suspend(client, alice, name='bob', days='3')
    assert codes(refused.json()['json']) == [('ADMIN_MODE_REQUIRED', None)]
    await admin_on(client, alice)
    assert codes((await suspend(client, alice, name='bob', days='0')).json()['json']) == [
        ('BAD_NUMBER', 'days')
    ]
    assert codes((await suspend(client, alice, name='alice')).json()['json']) == [
        ('CANT_RESTRICT_ADMIN', 'name')
    ]
    done = await suspend(client, alice, name='bob', days='3', reason='spamming')
    assert done.status_code == 200

    # Bob can read and tidy up, but not post, vote or send.
    assert (await other_client.get('/in/pics/')).status_code == 200
    saved = await other_client.post(
        '/api/save', data={'uh': bob.csrf, 'id': 't3_1', 'api_type': 'json'}
    )
    assert saved.status_code == 200
    voted = await other_client.post(
        '/api/vote', data={'uh': bob.csrf, 'id': 't3_1', 'dir': '1', 'api_type': 'json'}
    )
    assert voted.status_code == 403
    ((code, message, _),) = voted.json()['json']['errors']
    assert code == 'IN_TIMEOUT'
    assert 'you can read, but not post' in message
    assert (await me(other_client)).json()['is_suspended'] is True

    await suspend(client, alice, '/api/admin/unsuspend', name='bob')
    voted = await other_client.post(
        '/api/vote', data={'uh': bob.csrf, 'id': 't3_1', 'dir': '1', 'api_type': 'json'}
    )
    assert voted.status_code == 200


async def test_timeouts_end_by_themselves(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    await suspend(client, alice, name='bob', days='1')
    engine = db.create_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(
            text("update accounts set timeout_expires = now() - interval '1 second'")
        )
    await engine.dispose()
    assert (await me(other_client)).json()['is_suspended'] is False


async def test_suspended_for_good(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    await suspend(client, alice, name='bob', reason='ban evasion')
    # Signed out at once, and kept out.
    assert (await me(other_client)).status_code == 403
    login = await other_client.post(
        '/api/login', data={'user': 'bob', 'passwd': PASSWORD, 'api_type': 'json'}
    )
    assert login.json()['json']['errors'] != []
    assert (await client.get('/user/bob/about.json')).json()['data'] == {
        'name': 'bob',
        'is_suspended': True,
    }


def modhash(settings: Settings, client: AsyncTestClient[Litestar]) -> str:
    session = sessions.decode(settings, client.cookies.get(sessions.COOKIE) or '')
    assert session is not None
    return session.csrf


async def community_action(
    client: AsyncTestClient[Litestar], modhash: str, action: str, name: str = 'pics'
) -> httpx.Response:
    return await client.post(
        '/api/admin/community',
        data={'uh': modhash, 'name': name, 'action': action, 'api_type': 'json'},
    )


async def test_ban_communities(
    settings: Settings,
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    bob = modhash(settings, other_client)
    await create(other_client, bob, name='cats')
    await submitted(other_client, bob, title='cat', url='c.org', community='cats')
    published.clear()
    assert (await community_action(client, alice, 'ban')).status_code == 403
    await admin_on(client, alice)
    assert codes((await community_action(client, alice, 'explode')).json()['json']) == [
        ('INVALID_OPTION', 'action')
    ]
    assert (await community_action(client, alice, 'ban')).status_code == 200
    assert [m.actor_name for m in published] == ['index_community']

    gone = await other_client.get('/in/pics/')
    assert gone.status_code == 404
    assert 'in/pics was banned' in gone.text
    # Out of every listing and directory, and nothing more goes in.
    assert [
        t['data']['title']
        for t in (await other_client.get('/in/all.json')).json()['data']['children']
    ] == ['cat']
    assert 'pics' not in (await other_client.get('/ins.json')).text
    refused = await other_client.post(
        '/api/submit',
        data={
            'uh': bob,
            'community': 'pics',
            'kind': 'link',
            'title': 'x',
            'url': 'x.org',
            'api_type': 'json',
        },
    )
    assert refused.json()['json']['errors'] != []
    # Admins can still look, and the page says it's banned.
    page = await client.get('/in/pics/')
    assert page.status_code == 200
    assert 'This community is banned' in page.text
    assert 'bob&#39;s' in page.text

    await community_action(client, alice, 'unban')
    assert (await other_client.get('/in/pics/')).status_code == 200


async def test_quarantine_and_nsfw(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    await community_action(client, alice, 'quarantine')
    # Admins don't need to opt in to see a quarantined community.
    page = (await client.get('/in/pics/')).text
    assert '<option value="unquarantine">' in page
    await community_action(client, alice, 'nsfw')
    about = (await client.get('/in/pics/about.json')).json()['data']
    assert (about['quarantine'], about['over18']) == (True, True)
    await community_action(client, alice, 'unquarantine')
    await community_action(client, alice, 'sfw')
    about = (await client.get('/in/pics/about.json')).json()['data']
    assert (about['quarantine'], about['over18']) == (False, False)


async def admin_actions(settings: Settings) -> list[tuple[str, str]]:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session:
        actions = await PgAdminRepo(session).actions(limit=10)
    await engine.dispose()
    return [(a.action, a.details) for a in actions]


async def test_admin_removals_stay_removed(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    bob = modhash(settings, other_client)
    await admin_on(client, alice)
    assert await remove(client, alice) == 200
    # Without spam=false, /api/remove marks it spam, as r2's did.
    assert await admin_actions(settings) == [('spamlink', '')]
    # Bob keeps in/pics, but what an admin removed only an admin puts back.
    page = (await other_client.get('/in/pics/comments/1/')).text
    assert 'removed by an admin' in page
    assert 'action="/api/approve"' not in page
    refused = await other_client.post(
        '/api/approve', data={'uh': bob, 'id': 't3_1', 'api_type': 'json'}
    )
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('ADMIN_REMOVED', None)]
    approved = await client.post(
        '/api/approve', data={'uh': alice, 'id': 't3_1', 'api_type': 'json'}
    )
    assert approved.status_code == 200
    # An innkeeper's own removal is the innkeepers' to undo, as before.
    assert await remove(other_client, bob) == 200
    assert (
        await other_client.post('/api/approve', data={'uh': bob, 'id': 't3_1', 'api_type': 'json'})
    ).status_code == 200


async def distinguish(
    client: AsyncTestClient[Litestar], modhash: str, how: str, thing: str = 't3_2'
) -> httpx.Response:
    return await client.post(
        '/api/distinguish', data={'uh': modhash, 'id': thing, 'how': how, 'api_type': 'json'}
    )


async def distinguished(client: AsyncTestClient[Litestar]) -> str | None:
    page = (await client.get('/in/pics/comments/2.json')).json()
    value: str | None = page[0]['data']['children'][0]['data']['distinguished']
    return value


async def test_speak_as_admin(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await submitted(client, alice, title='a notice', url='n.org')
    assert (await distinguish(client, alice, 'admin')).status_code == 403
    await admin_on(client, alice)
    # Alice isn't one of in/pics' innkeepers, so she can't speak as one.
    assert (await distinguish(client, alice, 'yes')).status_code == 403
    assert (await distinguish(client, alice, 'admin')).status_code == 200
    assert await distinguished(other_client) == 'admin'
    assert 'class="userattrs admin"' in (await other_client.get('/in/pics/')).text
    assert await admin_actions(settings) == [('distinguish', 'admin')]
    assert (await distinguish(client, alice, 'no')).status_code == 200
    assert await distinguished(other_client) is None


async def test_remove_everything_an_account_posted(
    settings: Settings,
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    bob = modhash(settings, other_client)
    await create(other_client, bob, name='cats')
    await submitted(other_client, bob, title='cat', url='c.org', community='cats')
    await reply(other_client, bob, 't3_1', 'mine')
    await submitted(client, alice, title="alice's", url='a.org')
    await admin_on(client, alice)
    published.clear()
    assert codes(
        (await suspend(client, alice, '/api/admin/remove_all', name='alice')).json()['json']
    ) == [('CANT_RESTRICT_ADMIN', 'name')]
    done = await suspend(client, alice, '/api/admin/remove_all', name='bob', reason='spam')
    assert done.status_code == 200
    assert await admin_actions(settings) == [('removeall', '3 removed: spam')]
    # Both of bob's links leave search; alice's stays.
    assert sorted(m.args[0] for m in published if m.actor_name == 'index_link') == ['t3_1', 't3_2']
    engine = db.create_engine(settings)
    async with engine.begin() as connection:
        removed = (
            await connection.execute(
                text(
                    "select (select count(*) from links where removed_by = 'admin'),"
                    " (select count(*) from comments where removed_by = 'admin'),"
                    ' (select count(*) from links where not removed)'
                )
            )
        ).one()
    await engine.dispose()
    assert tuple(removed) == (2, 1, 1)
    # Bob's in/pics now shows only alice's link.
    titles = [
        t['data']['title']
        for t in (await other_client.get('/in/all.json')).json()['data']['children']
    ]
    assert titles == ["alice's"]


async def test_admin_page(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    bob = modhash(settings, other_client)
    await other_client.post(
        '/api/report', data={'uh': bob, 'thing_id': 't3_1', 'reason': 'odd', 'api_type': 'json'}
    )
    # Not there for anyone else, and admin mode first for an admin.
    assert (await other_client.get('/admin')).status_code == 404
    first = await client.get('/admin', follow_redirects=False)
    assert first.headers['location'] == '/admin/on?dest=/admin/reports'
    await admin_on(client, alice)
    reports = await client.get('/admin')
    assert reports.status_code == 200
    assert 'href="/admin" class="choice admin-mode"' in reports.text
    # Reported anywhere, with the reasons and the buttons to act on it.
    assert 'bob&#39;s' in reports.text
    assert '<li>odd</li>' in reports.text
    assert 'action="/api/remove"' in reports.text

    await suspend(client, alice, name='bob', days='2', reason='rude')
    await community_action(client, alice, 'ban')
    accounts = (await client.get('/admin/accounts')).text
    # Newest first: alice registered after bob.
    assert accounts.index('class="author">alice<') < accounts.index('class="author">bob<')
    assert 'suspended until' in accounts
    # Banned communities are listed too.
    assert 'banned' in (await client.get('/admin/communities')).text
    log = (await client.get('/admin/log')).text
    assert 'href="/in/pics/">in/pics</a>' in log
    assert 'href="/user/bob">u/bob</a>' in log
    assert '2 days: rude' in log
    assert (await client.get('/admin/elsewhere')).status_code == 404
    # On a profile in admin mode, the account's admin forms.
    assert 'action="/api/admin/unsuspend"' in (await client.get('/user/bob')).text


async def test_admin_pages_page(
    settings: Settings, client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await bobs_link_and_alice(settings, client, other_client)
    await admin_on(client, alice)
    for _ in range(26):
        await community_action(client, alice, 'nsfw')
    first = (await client.get('/admin/log')).text
    after = first.split('?after=')[1].split('"')[0]
    rest = (await client.get(f'/admin/log?after={after}')).text
    assert rest.count('<tr class="modactions') == 1
    # A cursor that can't be read starts from the top.
    assert (await client.get('/admin/log?after=nonsense')).status_code == 200
    assert (await client.get('/admin/accounts?after=t5_1')).status_code == 200
