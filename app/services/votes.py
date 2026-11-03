"""Casting votes on links and comments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.errors import ErrorCode, InaError
from app.ids import Kind
from app.services.comments import check_thread
from app.services.perms import Action, Standing, can

if TYPE_CHECKING:
    from app.ids import Fullname
    from app.repos.base import Account, Community, Link, VoteRepo

DIRECTIONS = (-1, 0, 1)


async def cast(
    votes: VoteRepo,
    account: Account,
    thing: Fullname,
    direction: int,
    *,
    link: Link,
    community: Community,
    deleted: bool,
    standing: Standing,
) -> None:
    """Record a vote; ``link`` is the thing itself or the comment's thread.

    The score columns are rewritten by the score job, not here.
    """
    if direction not in DIRECTIONS:
        raise InaError.of(ErrorCode.BAD_NUMBER, 'dir', range='-1 - 1')
    if thing.kind not in {Kind.LINK, Kind.COMMENT}:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id')
    if not can(account, Action.VOTE, community, standing=standing):
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'id', status_code=403)
    if deleted:
        raise InaError.of(ErrorCode.DELETED_COMMENT, 'id')
    check_thread(link, community, field='id')
    await votes.cast(account.id, thing, direction)
