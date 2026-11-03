"""The LinkRepo and VoteRepo contracts. Every implementation runs the same tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.ids import Fullname, Kind
from app.repos.base import Embed, ListingQuery, Sort
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.tables import links
from app.services import ranking
from tests.repos.conftest import NOW, World


async def test_create_and_find(world: World) -> None:
    link = await world.link('first')
    assert link.fullname == Fullname(Kind.LINK, link.id)
    assert (link.community_name, link.author_name, link.score) == ('pics', 'alice', 1)
    assert await world.links.by_id(link.id) == link
    assert await world.links.by_ids([link.id, link.id + 99]) == {link.id: link}
    assert await world.links.by_url(link.community_id, link.url) == link
    assert await world.links.by_url(world.community_ids['funny'], link.url) is None


async def test_media_keeps_the_thumbnail_and_the_embed(world: World) -> None:
    link = await world.link('video')
    assert link.embed is None
    await world.links.set_thumbnail(link.id, 'thumbs/a.jpg', (70, 40))
    await world.links.set_embed(link.id, Embed('youtube', 'dQw4w9WgXcQ', 43))
    found = await world.links.by_id(link.id)
    assert found is not None
    assert found.embed == Embed('youtube', 'dQw4w9WgXcQ', 43)
    assert (found.thumbnail, found.thumbnail_width) == ('thumbs/a.jpg', 70)


async def test_sorts_and_pages(world: World) -> None:
    await world.link('old-popular', ups=500, age_hours=48)
    await world.link('fresh', ups=2)
    await world.link('fight', ups=40, downs=37, age_hours=5)
    await world.link('older', ups=1, age_hours=10)
    pics = (world.community_ids['pics'],)

    hot = ListingQuery(community_ids=pics, sort=Sort.HOT, limit=10)
    assert await world.titles(hot) == ['fresh', 'fight', 'older', 'old-popular']
    new = replace(hot, sort=Sort.NEW)
    assert await world.titles(new) == ['older', 'fight', 'fresh', 'old-popular']
    top = replace(hot, sort=Sort.TOP)
    assert await world.titles(top) == ['old-popular', 'fight', 'fresh', 'older']
    day = replace(top, since=NOW - timedelta(days=1))
    assert await world.titles(day) == ['fight', 'fresh', 'older']
    controversial = replace(hot, sort=Sort.CONTROVERSIAL)
    assert (await world.titles(controversial))[0] == 'fight'

    first = await world.links.listing(replace(top, limit=2))
    found = await world.links.by_ids(first)
    after = replace(top, limit=2, after=found[first[-1]])
    assert await world.titles(after) == ['fresh', 'older']
    second = await world.links.listing(after)
    back = replace(top, limit=2, before=(await world.links.by_ids(second))[second[0]])
    assert sorted(await world.titles(back)) == ['fight', 'old-popular']


async def test_front_page_merges_communities(world: World) -> None:
    await world.link('p1', ups=10)
    await world.link('f1', community='funny', ups=20)
    await world.link('p2', ups=5, age_hours=1)
    await world.link('f2', community='funny', ups=1, age_hours=30)
    both = (world.community_ids['pics'], world.community_ids['funny'])
    query = ListingQuery(community_ids=both, sort=Sort.TOP, limit=3)
    assert await world.titles(query) == ['f1', 'p1', 'p2']
    page = await world.links.listing(query)
    last = (await world.links.by_ids(page))[page[-1]]
    assert await world.titles(replace(query, after=last)) == ['f2']


async def test_all_skips_private_removed_and_nsfw(world: World) -> None:
    await world.link('public')
    await world.link('private', community='secret')
    gone = await world.link('removed')
    racy = await world.link('racy')
    await world.session.execute(update(links).where(links.c.id == gone.id).values(removed=True))
    await world.session.execute(update(links).where(links.c.id == racy.id).values(nsfw=True))
    query = ListingQuery(community_ids=None, sort=Sort.NEW, limit=10)
    assert await world.titles(query) == ['racy', 'public']
    assert await world.titles(replace(query, include_nsfw=False)) == ['public']
    assert await world.titles(replace(query, min_score=2)) == []


async def test_recount_from_votes(world: World) -> None:
    link = await world.link('voted', ups=0)
    others = PgAccountRepo(world.session)
    voters = [
        (await others.create(name=f'v{i}', password_hash='h', email=None)).id for i in range(3)
    ]
    for voter, direction in zip(voters, (1, 1, -1), strict=True):
        await world.votes.cast(voter, link.fullname, direction)
    await world.votes.cast(voters[1], link.fullname, 0)
    recounted = await world.links.recount(link.id)
    assert recounted is not None
    assert (recounted.upvotes, recounted.downvotes, recounted.score) == (1, 1, 0)
    assert recounted.hot_score == ranking.hot(1, 1, link.created)
    assert recounted.controversy_score == ranking.controversy(1, 1)
    assert await world.votes.directions(voters[0], Kind.LINK, [link.id]) == {link.id: 1}
    assert await world.votes.directions(voters[1], Kind.LINK, [link.id]) == {}


async def test_sql_ranking_matches_python(world: World) -> None:
    cases = [(0, 0), (1, 0), (0, 1), (7, 3), (3, 7), (1000, 1), (55, 54), (2, 900)]
    made = []
    for index, (ups, downs) in enumerate(cases):
        link = await world.link(f'case{index}', ups=0, age_hours=index * 7)
        voters = PgAccountRepo(world.session)
        for n in range(ups + downs):
            voter = await voters.create(name=f'c{index}x{n}', password_hash='h', email=None)
            await world.votes.cast(voter.id, link.fullname, 1 if n < ups else -1)
        made.append((link, ups, downs))
    first, last = made[0][0].id, made[-1][0].id
    assert await world.links.rebuild_scores(first, last) == len(cases)
    rebuilt = await world.links.by_ids([link.id for link, _, _ in made])
    for link, ups, downs in made:
        assert rebuilt[link.id].hot_score == ranking.hot(ups, downs, link.created)
        assert rebuilt[link.id].controversy_score == pytest.approx(
            ranking.controversy(ups, downs), rel=1e-12
        )
    assert await world.links.max_id() == last
