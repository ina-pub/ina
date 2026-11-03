"""The CommentRepo contract. Every implementation runs the same tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from app.repos.base import Comment, CommentRepo, NewComment
from app.repos.pg.comments import PgCommentRepo
from app.services import ranking

if TYPE_CHECKING:
    from tests.repos.conftest import World


@pytest.fixture
def repo(world: World) -> CommentRepo:
    return PgCommentRepo(world.session)


async def reply(repo: CommentRepo, world: World, link_id: int, parent: Comment | None) -> Comment:
    return await repo.create(
        NewComment(
            link_id=link_id,
            parent=parent,
            author_id=world.account_id,
            body_md='hi',
            body_html='<p>hi</p>\n',
            sendreplies=True,
            ip='127.0.0.1',
            created=datetime(2026, 11, 1, tzinfo=UTC),
            upvotes=1,
            downvotes=0,
            confidence=ranking.confidence(1, 0),
            controversy_score=0.0,
        )
    )


async def test_paths_and_subtrees(repo: CommentRepo, world: World) -> None:
    link = await world.link('thread')
    top = await reply(repo, world, link.id, None)
    child = await reply(repo, world, link.id, top)
    grandchild = await reply(repo, world, link.id, child)
    other = await reply(repo, world, link.id, None)
    assert (top.path, top.depth, top.parent_id) == ('1', 0, None)
    assert (grandchild.path, grandchild.depth) == ('1.2.3', 2)
    assert grandchild.parent_fullname == child.fullname
    assert top.parent_fullname == link.fullname
    assert (top.author_name, top.community_name) == ('alice', 'pics')
    subtree = await world.session.execute(
        text("select id from comments where path <@ '1.2' order by id")
    )
    assert list(subtree.scalars()) == [child.id, grandchild.id]
    assert [c.id for c in await repo.for_link(link.id)] == [1, 2, 3, other.id]
    rows = await repo.tree_rows(link.id)
    assert [(row.id, row.parent_id) for row in rows] == [(1, None), (2, 1), (3, 2), (4, None)]
    assert await repo.by_ids([2, 99]) == {2: child}


async def test_recounts(repo: CommentRepo, world: World) -> None:
    link = await world.link('thread')
    comment = await reply(repo, world, link.id, None)
    await world.votes.cast(world.account_id, comment.fullname, -1)
    recounted = await repo.recount(comment.id)
    assert recounted is not None
    assert (recounted.upvotes, recounted.downvotes, recounted.score) == (0, 1, -1)
    assert recounted.confidence == ranking.confidence(0, 1)
    await reply(repo, world, link.id, comment)
    counted = await world.links.recount_comments(link.id)
    assert counted is not None
    assert counted.comment_count == 2
    assert await repo.rebuild_scores(1, await repo.max_id()) == 2
