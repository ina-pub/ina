from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.tables import accounts, links
from app.search import create_search
from app.search.documents import COMMUNITIES, LINKS
from scripts import admins, rebuild_karma, rebuild_scores, rebuild_search, seed_dev_data

if TYPE_CHECKING:
    from app.settings import Settings


async def test_seed_then_rebuild(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    await seed_dev_data.seed()
    await seed_dev_data.seed()
    assert 'already seeded' in capsys.readouterr().out

    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        scores = (await session.execute(select(links.c.score).order_by(links.c.id))).scalars()
        assert list(scores) == [3] * len(seed_dev_data.LINKS)
        await session.execute(update(links).values(score=0, hot_score=0, upvotes=0))
    assert await rebuild_scores.rebuild(start=1, batch=2, things=('links',)) == len(
        seed_dev_data.LINKS
    )
    async with async_sessionmaker(engine)() as session:
        rows = (await session.execute(select(links.c.score, links.c.hot_score))).all()
    await engine.dispose()
    assert all(score == 3 and hot > 0 for score, hot in rows)


async def test_rebuild_karma(settings: Settings) -> None:
    await seed_dev_data.seed()
    engine = db.create_engine(settings)
    earned = func.sum(links.c.score - 1)
    async with async_sessionmaker(engine)() as session, session.begin():
        expected = (await session.execute(select(earned).where(~links.c.is_self))).scalar()
        await session.execute(update(accounts).values(link_karma=0, comment_karma=0))
    assert await rebuild_karma.rebuild(batch=2) > 0
    async with async_sessionmaker(engine)() as session:
        total = (await session.execute(select(func.sum(accounts.c.link_karma)))).scalar()
    await engine.dispose()
    assert expected
    assert total == expected


async def test_reset_starts_over(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    await seed_dev_data.seed()
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        await session.execute(update(links).values(title='changed'))
    await seed_dev_data.seed(reset=True)
    assert 'emptied the database and Redis' in capsys.readouterr().out
    async with async_sessionmaker(engine)() as session:
        rows = (await session.execute(select(links.c.id, links.c.title).order_by(links.c.id))).all()
    await engine.dispose()
    # Identities restart, so the sample data has the same ids every time.
    assert rows == [(n + 1, link[2]) for n, link in enumerate(seed_dev_data.LINKS)]


async def test_reset_refuses_a_remote_database(settings: Settings) -> None:
    remote = settings.model_copy(
        update={'database_url': 'postgresql+asyncpg://ina:ina@db.example.com/ina'}
    )
    engine = db.create_engine(settings)
    with pytest.raises(SystemExit, match=r'refusing to reset a database on db\.example\.com'):
        await seed_dev_data._reset(engine, remote)  # noqa: SLF001
    await engine.dispose()


async def test_rebuild_search(settings: Settings) -> None:
    await seed_dev_data.seed()
    written = await rebuild_search.rebuild(recreate=True, batch=2)
    assert written == len(seed_dev_data.LINKS) + len(seed_dev_data.COMMUNITIES)

    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        await session.execute(update(links).where(links.c.id == 1).values(removed=True))
    await engine.dispose()
    # Without --recreate, a removed link's document is deleted.
    assert await rebuild_search.rebuild(things=(LINKS,)) == len(seed_dev_data.LINKS) - 1

    search = create_search(settings)
    every = {'query': {'match_all': {}}, 'size': 100}
    found = await search.search(LINKS, every)
    communities = await search.search(COMMUNITIES, every)
    await search.aclose()
    assert sorted(found.ids) == [str(n) for n in range(2, len(seed_dev_data.LINKS) + 1)]
    assert communities.total == len(seed_dev_data.COMMUNITIES)


async def test_admins(capsys: pytest.CaptureFixture[str]) -> None:
    await seed_dev_data.seed()
    capsys.readouterr()
    assert await admins.run('grant', 'alice') == 0
    assert await admins.run('list') == 0
    assert capsys.readouterr().out == 'alice is now an admin\nalice\n'
    assert await admins.run('revoke', 'alice') == 0
    assert await admins.run('list') == 0
    assert capsys.readouterr().out == 'alice is no longer an admin\n'
    assert await admins.run('grant', 'nobody') == 1
