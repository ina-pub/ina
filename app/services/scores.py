"""Keeping stored scores and rankings in step with the votes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.listings import community_scope

if TYPE_CHECKING:
    from app.infra.cache import Cache
    from app.repos.base import AccountRepo, CommentRepo, Link, LinkRepo


async def recount_link(links: LinkRepo, accounts: AccountRepo, cache: Cache, link_id: int) -> None:
    """Recount one link's votes and its author's karma, then drop the cached copies.

    Recounting from the votes table, never incrementing, makes this safe to repeat.
    """
    link = await links.recount(link_id)
    if link is None:
        return
    await accounts.recount_karma(link.author_id)
    await cache.forget_things([str(link.fullname)])
    await cache.bump(community_scope(link.community_id))


async def recount_comment(
    comments: CommentRepo, links: LinkRepo, accounts: AccountRepo, cache: Cache, comment_id: int
) -> Link | None:
    """Recount one comment's votes, its author's karma and its thread's comment count.

    Returns the thread's link, recounted.
    """
    comment = await comments.recount(comment_id)
    if comment is None:
        return None
    await accounts.recount_karma(comment.author_id)
    link = await links.recount_comments(comment.link_id)
    stale = [str(comment.fullname)] + ([str(link.fullname)] if link else [])
    await cache.forget_things(stale)
    return link
