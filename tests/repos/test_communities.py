"""The CommunityRepo and SubscriptionRepo contracts. Every implementation runs the same tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.base import (
    AccountRepo,
    CommunityRepo,
    CommunitySettings,
    CommunityText,
    ModPermission,
    NameTakenError,
    SubscriptionRepo,
)
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.subscriptions import PgSubscriptionRepo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.settings import Settings

TEXT = CommunityText(
    title='Pictures',
    description_md='pics',
    description_html='<p>pics</p>\n',
    sidebar_md='**rules**',
    sidebar_html='<p><strong>rules</strong></p>\n',
    type='public',
    nsfw=False,
    settings=CommunitySettings(),
)


@dataclass
class Repos:
    accounts: AccountRepo
    communities: CommunityRepo
    subscriptions: SubscriptionRepo

    async def account(self, name: str) -> int:
        return (await self.accounts.create(name=name, password_hash='h', email=None)).id


@pytest.fixture(params=['pg'])
async def repos(settings: Settings) -> AsyncIterator[Repos]:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        yield Repos(PgAccountRepo(session), PgCommunityRepo(session), PgSubscriptionRepo(session))
    await engine.dispose()


async def test_create_find_update(repos: Repos) -> None:
    alice = await repos.account('alice')
    created = await repos.communities.create(name='Pics', text=TEXT, creator_id=alice)
    assert created.fullname.kind == 't5'
    assert created.path == '/in/Pics/'
    assert await repos.communities.by_name('pics') == created
    assert await repos.communities.by_id(created.id) == created
    settings = CommunitySettings(link_type='self', sticky_fullnames=('t3_1',))
    changed = replace(TEXT, title='Pics!', settings=settings)
    updated = await repos.communities.update(created.id, changed)
    assert updated.title == 'Pics!'
    assert updated.settings == settings
    assert await repos.communities.by_name('nope') is None


async def test_names_are_unique_ignoring_case(repos: Repos) -> None:
    alice = await repos.account('alice')
    await repos.communities.create(name='pics', text=TEXT, creator_id=alice)
    with pytest.raises(NameTakenError):
        await repos.communities.create(name='PICS', text=TEXT, creator_id=alice)
    assert await repos.communities.by_name('pics') is not None


async def test_moderators(repos: Repos) -> None:
    alice, bob = await repos.account('alice'), await repos.account('bob')
    community = await repos.communities.create(name='pics', text=TEXT, creator_id=alice)
    await repos.communities.add_moderator(
        community.id, alice, permissions=ModPermission.ALL, added_by=None
    )
    await repos.communities.add_moderator(
        community.id, bob, permissions=ModPermission.POSTS | ModPermission.MAIL, added_by=alice
    )
    assert [(m.name, m.permissions) for m in await repos.communities.moderators(community.id)] == [
        ('alice', ModPermission.ALL),
        ('bob', ModPermission.POSTS | ModPermission.MAIL),
    ]
    assert (
        await repos.communities.permissions(community.id, bob)
        == ModPermission.POSTS | ModPermission.MAIL
    )
    assert await repos.communities.permissions(community.id, alice + 100) is None
    assert [s.name for s in await repos.communities.moderated_by(bob, limit=10, after=None)] == [
        'pics'
    ]


async def test_subscriptions_keep_the_count(repos: Repos) -> None:
    alice, bob = await repos.account('alice'), await repos.account('bob')
    community = await repos.communities.create(name='pics', text=TEXT, creator_id=alice)
    assert await repos.subscriptions.subscribe(alice, community.id) is True
    assert await repos.subscriptions.subscribe(alice, community.id) is False
    assert await repos.subscriptions.subscribe(bob, community.id) is True
    found = await repos.communities.by_id(community.id)
    assert found is not None
    assert found.subscriber_count == 2
    assert await repos.subscriptions.unsubscribe(bob, community.id) is True
    assert await repos.subscriptions.unsubscribe(bob, community.id) is False
    found = await repos.communities.by_id(community.id)
    assert found is not None
    assert found.subscriber_count == 1
    assert await repos.subscriptions.subscribed_among(alice, [community.id, community.id + 1]) == {
        community.id
    }
    assert await repos.subscriptions.subscribed_among(bob, [community.id]) == set()
    assert await repos.subscriptions.subscribed_among(bob, []) == set()


async def test_listings_page_by_keyset(repos: Repos) -> None:
    accounts = [await repos.account(f'user{i}') for i in range(3)]
    names = ['beta', 'Alpha', 'gamma', 'delta']
    created = {
        name: await repos.communities.create(name=name, text=TEXT, creator_id=accounts[0])
        for name in names
    }
    # gamma has 3 subscribers, alpha 2, beta and delta none.
    for account in accounts:
        await repos.subscriptions.subscribe(account, created['gamma'].id)
    for account in accounts[:2]:
        await repos.subscriptions.subscribe(account, created['Alpha'].id)

    first = await repos.communities.popular(limit=2, after=None)
    assert [s.name for s in first] == ['gamma', 'Alpha']
    rest = await repos.communities.popular(limit=10, after=first[-1])
    assert [s.name for s in rest] == ['delta', 'beta']

    newest = await repos.communities.newest(limit=2, after=None)
    assert [s.name for s in newest] == ['delta', 'gamma']
    assert [s.name for s in await repos.communities.newest(limit=5, after=newest[-1])] == [
        'Alpha',
        'beta',
    ]

    mine = await repos.subscriptions.subscribed(accounts[0], limit=1, after=None)
    assert [s.name for s in mine] == ['Alpha']
    more = await repos.subscriptions.subscribed(accounts[0], limit=5, after=mine[-1])
    assert [s.name for s in more] == ['gamma']
