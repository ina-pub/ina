"""Reading a thread's comments in a given sort, from the database or the precomputed tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ids import Fullname, Kind
from app.repos.base import Comment
from app.services.comments import PRECOMPUTED_THRESHOLD, SORTS, SortedTree, sort_tree, tree_rows

if TYPE_CHECKING:
    from app.infra.cache import Cache
    from app.repos.base import CommentRepo, Link

TREE_TTL = 24 * 60 * 60


def tree_key(link_id: int, sort: str) -> str:
    return f'tree:{link_id}:{sort}'


def precomputed(link: Link) -> bool:
    return link.comment_count >= PRECOMPUTED_THRESHOLD


async def rebuild_trees(comments: CommentRepo, cache: Cache, link: Link) -> None:
    """Order a large thread in every sort and cache it; this is the tree job's work."""
    rows = await comments.tree_rows(link.id)
    for sort in SORTS:
        tree = sort_tree(rows, sort, answerers={link.author_id})
        await cache.put(tree_key(link.id, sort), tree, ttl=TREE_TTL)


async def ordered(
    comments: CommentRepo, cache: Cache, link: Link, sort: str
) -> tuple[SortedTree, dict[int, Comment]]:
    """The thread in ``sort``, with whatever comments had to be read to order it.

    A small thread is one query, ordered here, and every comment comes back. A large one is read
    from the precomputed tree, built on a miss, and no comments come back.
    """
    if not precomputed(link):
        thread = await comments.for_link(link.id)
        tree = sort_tree(tree_rows(thread), sort, answerers={link.author_id})
        return tree, {comment.id: comment for comment in thread}

    async def build() -> SortedTree:
        rows = await comments.tree_rows(link.id)
        return sort_tree(rows, sort, answerers={link.author_id})

    tree = await cache.get_or_load(tree_key(link.id, sort), build, type_=SortedTree, ttl=TREE_TTL)
    return tree, {}


async def load(
    comments: CommentRepo, cache: Cache, ids: list[int], known: dict[int, Comment]
) -> dict[int, Comment]:
    """The comments for ``ids``: those already read, the rest through the thing cache."""
    missing = {i: str(Fullname(Kind.COMMENT, i)) for i in ids if i not in known}
    found = dict(known)
    found.update(await cache.things(missing, comments.by_ids, type_=Comment))
    return found
