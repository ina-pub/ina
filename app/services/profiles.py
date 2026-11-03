"""A user's profile listings, as r2 drew them: overview, comments, submitted, votes, and marks.

Links and comments come in the sort asked for, with ``t`` limiting top and controversial; the
overview merges the two. Each page ends at an anchor, the last thing's sort value and creation
time, so the next page carries on from there. Removed things show only on their author's own
profile and deleted ones on none; private communities' things only to their members.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.ids import Fullname, Kind
from app.repos.base import Comment, Link, Mark, ProfileAnchor, ProfileQuery, Sort
from app.services.listings import TIME_FILTERS

if TYPE_CHECKING:
    from app.repos.base import CommentRepo, LinkRepo, MarkRepo, VoteRepo

TABS = ('overview', 'comments', 'submitted', 'upvoted', 'downvoted', 'hidden', 'saved')
# Whose votes these are is the owner's business, unless they made them public.
VOTE_TABS = {'upvoted': 1, 'downvoted': -1}
# What the owner hid and saved, which only they see; newest mark first.
MARK_TABS = {'hidden': Mark.HIDDEN, 'saved': Mark.SAVED}
SORTS = (Sort.NEW, Sort.HOT, Sort.TOP, Sort.CONTROVERSIAL)
DEFAULT_TIME_FILTER = 'all'

type Thing = Link | Comment


def sort_value(thing: Thing, sort: Sort) -> float:
    if sort is Sort.NEW:
        return thing.created.timestamp()
    if sort is Sort.TOP:
        return thing.score
    if sort is Sort.CONTROVERSIAL:
        return thing.controversy_score
    return thing.hot_score if isinstance(thing, Link) else thing.confidence


def anchor(thing: Thing, sort: Sort) -> ProfileAnchor:
    return ProfileAnchor(sort_value(thing, sort), thing.created)


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    tab: str
    sort: Sort
    time_filter: str
    limit: int
    # The thing the previous page ended with.
    after: Thing | None
    own: bool
    excluded: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Page:
    things: list[Thing]
    more: bool


def _query(author_id: int, request: Request, now: datetime) -> ProfileQuery:
    window = TIME_FILTERS.get(request.time_filter)
    timed = request.sort in {Sort.TOP, Sort.CONTROVERSIAL}
    return ProfileQuery(
        author_id=author_id,
        sort=request.sort,
        since=now - window if window and timed else None,
        limit=request.limit + 1,
        after=anchor(request.after, request.sort) if request.after else None,
        include_removed=request.own,
        excluded=request.excluded,
    )


async def page(
    links: LinkRepo,
    comments: CommentRepo,
    votes: VoteRepo,
    marks: MarkRepo,
    author_id: int,
    request: Request,
    *,
    now: datetime | None = None,
) -> Page:
    if request.tab in VOTE_TABS:
        return await _voted(links, votes, author_id, request)
    if request.tab in MARK_TABS:
        return await _marked(links, comments, marks, author_id, request)
    query = _query(author_id, request, now or datetime.now(UTC))
    found: list[Thing] = []
    if request.tab in {'overview', 'submitted'}:
        found += await links.by_author(query)
    if request.tab in {'overview', 'comments'}:
        found += await comments.by_author(query)
    found.sort(key=lambda thing: (sort_value(thing, request.sort), thing.created), reverse=True)
    return Page(found[: request.limit], len(found) > request.limit)


async def _voted(links: LinkRepo, votes: VoteRepo, account_id: int, request: Request) -> Page:
    before = None
    if request.after is not None:
        before = await votes.voted_at(account_id, request.after.fullname)
    rows = await votes.voted_links(
        account_id, VOTE_TABS[request.tab], limit=request.limit + 1, before=before
    )
    found = await links.by_ids([link_id for link_id, _ in rows])
    shown = [
        found[link_id]
        for link_id, _ in rows[: request.limit]
        if link_id in found
        and not found[link_id].deleted
        and found[link_id].community_id not in request.excluded
    ]
    return Page(list(shown), len(rows) > request.limit)


async def _marked(
    links: LinkRepo, comments: CommentRepo, marks: MarkRepo, account_id: int, request: Request
) -> Page:
    mark = MARK_TABS[request.tab]
    before = None
    if request.after is not None:
        before = await marks.marked_at(account_id, mark, request.after.fullname)
    names = await marks.page(account_id, mark, limit=request.limit + 1, before=before)
    found_links = await links.by_ids([name.id for name in names if name.kind is Kind.LINK])
    found_comments = await comments.by_ids([name.id for name in names if name.kind is Kind.COMMENT])
    shown: list[Thing] = []
    for name in names[: request.limit]:
        thing: Thing | None = (
            found_links.get(name.id) if name.kind is Kind.LINK else found_comments.get(name.id)
        )
        if thing is not None and not thing.deleted and thing.community_id not in request.excluded:
            shown.append(thing)
    return Page(shown, len(names) > request.limit)


async def thing_by_fullname(links: LinkRepo, comments: CommentRepo, value: str) -> Thing | None:
    try:
        fullname = Fullname.parse(value)
    except ValueError:
        return None
    if fullname.kind is Kind.LINK:
        return await links.by_id(fullname.id)
    if fullname.kind is Kind.COMMENT:
        return await comments.by_id(fullname.id)
    return None
