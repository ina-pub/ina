"""Fill a development database with a few accounts, communities, links and comments.

    uv run python -m scripts.seed_dev_data [--reset]

Every account's password is ``hunter22``. Running it twice adds nothing: it stops if ``alice``
already exists. ``--reset`` first empties every table and the Redis db, which signs everyone out;
it refuses a database that is not on this machine.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo, PgVoteRepo
from app.repos.pg.subscriptions import PgSubscriptionRepo
from app.repos.pg.tables import metadata
from app.services import comments as comments_service
from app.services import communities as communities_service
from app.services import links as links_service
from app.services.communities import CommunityForm
from app.services.links import SubmitForm
from app.services.passwords import Passwords
from app.services.perms import NOBODY
from app.settings import get_settings

if TYPE_CHECKING:
    from app.repos.base import Comment
    from app.settings import Settings

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
PASSWORD = 'hunter22'  # noqa: S105 - a dev-only password, printed for the developer
ACCOUNTS = ('alice', 'bob', 'carol')
COMMUNITIES = (
    ('pics', 'Pictures and images', 'A place for pictures.\n\n**Rules**: be nice.'),
    ('askina', 'Ask ina', 'Ask a question, start a discussion.'),
    ('programming', 'in/programming', 'Computer programming.'),
)
# (link index, parent comment index or None, author index, text)
COMMENTS = (
    (2, None, 1, 'Measure twice, cut once.'),
    (2, 0, 2, 'And then **measure again**, apparently.'),
    (2, None, 2, 'Always carry a pen.'),
    (2, 1, 0, 'Words to live by.'),
    (0, None, 2, 'What a good cat.'),
)
LINKS = (
    ('pics', 'link', 'My cat, sitting in a box', 'https://example.com/cat.jpg', ''),
    ('pics', 'link', 'Sunset over the bay', 'https://example.com/sunset.jpg', ''),
    ('askina', 'self', 'What is the best advice you ever got?', '', 'Mine: *read the spec*.'),
    ('askina', 'self', 'What small thing makes your day better?', '', ''),
    ('programming', 'link', 'A tour of keyset pagination', 'https://example.com/keyset', ''),
)


async def seed(*, reset: bool = False) -> None:
    settings = get_settings()
    engine = db.create_engine(settings)
    passwords = Passwords(settings)
    try:
        if reset:
            await _reset(engine, settings)
        await _seed(engine, passwords)
    finally:
        await engine.dispose()


async def _reset(engine: AsyncEngine, settings: Settings) -> None:
    """Empty every app table and the Redis db, as the tests do between tests."""
    host = make_url(settings.database_url).host
    if host not in LOCAL_HOSTS:
        sys.exit(f'refusing to reset a database on {host}; --reset is for local development')
    tables = ', '.join(
        table.name for table in metadata.sorted_tables if table.name != 'alembic_version'
    )
    async with engine.begin() as connection:
        await connection.execute(text(f'truncate {tables} restart identity cascade'))
    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()
    sys.stdout.write('emptied the database and Redis\n')


async def _seed(engine: AsyncEngine, passwords: Passwords) -> None:
    settings = get_settings()
    async with async_sessionmaker(engine)() as session, session.begin():
        accounts = PgAccountRepo(session)
        if await accounts.by_name(ACCOUNTS[0]):
            sys.stdout.write('already seeded\n')
            return
        password_hash = await passwords.hash(PASSWORD)
        people = [
            await accounts.create(name=name, password_hash=password_hash, email=None)
            for name in ACCOUNTS
        ]
        communities = PgCommunityRepo(session)
        created = {}
        for name, title, sidebar in COMMUNITIES:
            form = CommunityForm(name=name, title=title, description=sidebar)
            created[name] = await communities_service.create(communities, settings, people[0], form)
        subscriptions = PgSubscriptionRepo(session)
        for person in people[1:]:
            for community in created.values():
                await subscriptions.subscribe(person.id, community.id)
        links, votes = PgLinkRepo(session), PgVoteRepo(session)
        now = datetime.now(UTC)
        posted = []
        for index, (cname, kind, title, url, text) in enumerate(LINKS):
            post = SubmitForm(
                community=cname, kind=kind, title=title, url=url, text=text, sendreplies='on'
            )
            link = await links_service.submit(
                links,
                votes,
                people[index % len(people)],
                created[cname],
                post,
                standing=NOBODY,
                ip='127.0.0.1',
                now=now - timedelta(hours=index * 3),
            )
            for voter in people:
                await votes.cast(voter.id, link.fullname, 1)
            posted.append(await links.recount(link.id) or link)
        comments = PgCommentRepo(session)
        written: list[Comment] = []
        for link_index, parent_index, author_index, body in COMMENTS:
            link = posted[link_index]
            parent = written[parent_index] if parent_index is not None else None
            written.append(
                await comments_service.post(
                    comments,
                    votes,
                    people[author_index],
                    link,
                    created[link.community_name],
                    parent,
                    body,
                    standing=NOBODY,
                    ip='127.0.0.1',
                )
            )
        for link in posted:
            await links.recount_comments(link.id)
    sys.stdout.write(
        f'seeded {len(ACCOUNTS)} accounts, {len(COMMUNITIES)} communities, {len(LINKS)} links '
        f'and {len(COMMENTS)} comments; every password is {PASSWORD}\n'
    )


if __name__ == '__main__':
    asyncio.run(seed(reset='--reset' in sys.argv[1:]))
