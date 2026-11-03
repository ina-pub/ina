"""Shared fixtures for the repository contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra import db
from app.repos.base import LinkRepo, ListingQuery, NewLink, VoteRepo
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo, PgVoteRepo
from app.repos.pg.tables import communities
from app.services import ranking
from tests.repos.test_communities import TEXT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.repos.base import Link
    from app.settings import Settings


NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


@dataclass
class World:
    session: AsyncSession
    links: LinkRepo
    votes: VoteRepo
    account_id: int
    community_ids: dict[str, int]

    async def link(
        self,
        title: str,
        *,
        community: str = 'pics',
        ups: int = 1,
        downs: int = 0,
        age_hours: int = 0,
    ) -> Link:
        created = NOW - timedelta(hours=age_hours)
        return await self.links.create(
            NewLink(
                community_id=self.community_ids[community],
                author_id=self.account_id,
                title=title,
                url=f'https://example.com/{title}',
                domain='example.com',
                is_self=False,
                selftext_md='',
                selftext_html='',
                nsfw=False,
                sendreplies=True,
                ip='127.0.0.1',
                created=created,
                upvotes=ups,
                downvotes=downs,
                hot_score=ranking.hot(ups, downs, created),
                controversy_score=ranking.controversy(ups, downs),
            )
        )

    async def titles(self, query: ListingQuery) -> list[str]:
        found = await self.links.by_ids(ids := await self.links.listing(query))
        return [found[link_id].title for link_id in ids]


@pytest.fixture(params=['pg'])
async def world(settings: Settings) -> AsyncIterator[World]:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        account = await PgAccountRepo(session).create(name='alice', password_hash='h', email=None)
        srs = PgCommunityRepo(session)
        ids = {
            name: (await srs.create(name=name, text=TEXT, creator_id=account.id)).id
            for name in ('pics', 'funny', 'secret')
        }
        await session.execute(
            update(communities).where(communities.c.name == 'secret').values(type='private')
        )
        yield World(session, PgLinkRepo(session), PgVoteRepo(session), account.id, ids)
    await engine.dispose()
