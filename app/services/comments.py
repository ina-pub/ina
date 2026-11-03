"""Comment threads: posting, and choosing which comments to show in what order.

A thread is first put in order (``SortedTree``), then a page of it is chosen the way r2's comment
builder chose one: candidates sit in a heap by sort rank, starting with the top-level comments;
taking one makes its replies candidates, until the comment limit or the depth limit. Whatever is
left becomes "load more comments" (more replies than the limit) or "continue this thread" (deeper
than the depth limit).

Threads under ``PRECOMPUTED_THRESHOLD`` comments are ordered in Python after one query. Larger
ones are ordered by the tree job and read from the cache.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import msgspec

from app.errors import ErrorCode, InaError
from app.repos.base import ModPermission, NewComment, TreeRow
from app.services import ranking
from app.services.perms import Action, Standing, can
from app.text import markdown

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from app.repos.base import Account, Comment, CommentRepo, Community, Link, VoteRepo

PRECOMPUTED_THRESHOLD = 500
MAX_DEPTH = 10
MAX_LIMIT = 500
MAX_COMMENT = 10000
SORTS = ('confidence', 'top', 'new', 'controversial', 'old', 'qa')
DEFAULT_SORT = 'confidence'
# The parent id of top-level comments in a SortedTree.
LINK = 0

SORT_KEYS: dict[str, Callable[[TreeRow], tuple[float, int]]] = {
    'confidence': lambda row: (-row.confidence, row.id),
    'qa': lambda row: (-row.confidence, row.id),
    'top': lambda row: (-row.score, row.id),
    'new': lambda row: (-row.created, row.id),
    'old': lambda row: (row.created, row.id),
    'controversial': lambda row: (-row.controversy_score, row.id),
}


class SortedTree(msgspec.Struct, frozen=True):
    """One thread in one sort: every comment id best first, and each parent's replies likewise."""

    order: list[int]
    # Parent id (LINK for top-level comments) to its replies.
    children: dict[int, list[int]]


def _qa(rows: list[TreeRow], answerers: set[int]) -> list[TreeRow]:
    """Keep what r2's Q&A sort shows.

    That is top-level comments, the answerers' comments, replies to the answerers, distinguished
    comments, and every ancestor of those, so answers keep their questions.
    """
    by_id = {row.id: row for row in rows}
    kept = {
        row.id
        for row in rows
        if row.parent_id is None
        or row.author_id in answerers
        or by_id[row.parent_id].author_id in answerers
        or row.distinguished
    }
    for comment_id in list(kept):
        parent = by_id[comment_id].parent_id
        while parent is not None and parent not in kept:
            kept.add(parent)
            parent = by_id[parent].parent_id
    return [row for row in rows if row.id in kept]


def sort_tree(rows: list[TreeRow], sort: str, *, answerers: Iterable[int] = ()) -> SortedTree:
    if sort == 'qa':
        rows = _qa(rows, set(answerers))
    ordered = sorted(rows, key=SORT_KEYS[sort])
    children: defaultdict[int, list[int]] = defaultdict(list)
    for row in ordered:
        children[row.parent_id if row.parent_id is not None else LINK].append(row.id)
    return SortedTree(order=[row.id for row in ordered], children=dict(children))


def tree_rows(thread: list[Comment]) -> list[TreeRow]:
    return [
        TreeRow(
            c.id,
            c.parent_id,
            c.score,
            c.confidence,
            c.controversy_score,
            c.created.timestamp(),
            c.author_id,
            c.distinguished,
        )
        for c in thread
    ]


@dataclass(frozen=True, slots=True)
class More:
    """Replies not shown: ``ids`` are the hidden children of ``parent_id`` (None: the link)."""

    parent_id: int | None
    depth: int
    ids: tuple[int, ...]
    # The hidden comments and all their replies.
    count: int


@dataclass(frozen=True, slots=True)
class Shown:
    id: int
    depth: int
    children: tuple[Shown, ...]
    more: More | None
    # Replies exist beyond the depth limit: "continue this thread".
    continued: bool


@dataclass(frozen=True, slots=True)
class Selection:
    roots: tuple[Shown, ...]
    more: More | None

    def ids(self) -> list[int]:
        found: list[int] = []
        stack = list(self.roots)
        while stack:
            node = stack.pop()
            found.append(node.id)
            stack.extend(node.children)
        return found


def _descendants(tree: SortedTree) -> dict[int, int]:
    """How many replies, at any depth, each comment has. One walk, children before parents."""
    counts: dict[int, int] = {}
    stack: list[tuple[int, bool]] = [(LINK, False)]
    while stack:
        node, done = stack.pop()
        kids = tree.children.get(node, [])
        if done:
            counts[node] = sum(1 + counts[kid] for kid in kids)
        else:
            stack.append((node, True))
            stack.extend((kid, False) for kid in kids)
    return counts


def select(
    tree: SortedTree,
    *,
    starts: list[int] | None = None,
    parent_id: int | None = None,
    limit: int,
    max_depth: int = MAX_DEPTH,
    first: int | None = None,
) -> Selection:
    """Choose up to ``limit`` comments, best first, from ``starts`` (default: the top level).

    ``first`` is a stickied comment, taken before anything else when it is at the top level.
    Depths count from the starts, which are depth 0.
    """
    rank = {comment_id: index for index, comment_id in enumerate(tree.order)}
    if starts is None:
        starts = tree.children.get(LINK, [])
    starts = sorted((s for s in starts if s in rank), key=rank.__getitem__)
    if first is not None and first in starts:
        starts.remove(first)
        starts.insert(0, first)
    descendants = _descendants(tree)
    chosen: dict[int, int] = {}
    heap = [(-1 if s == first else rank[s], s, 0) for s in starts]
    heapq.heapify(heap)
    while heap and len(chosen) < limit:
        _, comment_id, depth = heapq.heappop(heap)
        chosen[comment_id] = depth
        if depth + 1 < max_depth:
            for kid in tree.children.get(comment_id, []):
                heapq.heappush(heap, (rank[kid], kid, depth + 1))

    def hidden(ids: list[int], parent: int | None, depth: int) -> More | None:
        if not ids:
            return None
        count = sum(1 + descendants.get(i, 0) for i in ids)
        return More(parent_id=parent, depth=depth, ids=tuple(ids), count=count)

    def build(comment_id: int, depth: int) -> Shown:
        kids = tree.children.get(comment_id, [])
        shown = tuple(build(kid, depth + 1) for kid in kids if kid in chosen)
        rest = [kid for kid in kids if kid not in chosen]
        too_deep = depth + 1 >= max_depth
        return Shown(
            id=comment_id,
            depth=depth,
            children=shown,
            more=None if too_deep else hidden(rest, comment_id, depth + 1),
            continued=too_deep and bool(rest),
        )

    roots = tuple(build(s, 0) for s in starts if s in chosen)
    return Selection(roots, hidden([s for s in starts if s not in chosen], parent_id, 0))


def parents(tree: SortedTree) -> dict[int, int]:
    return {kid: parent for parent, kids in tree.children.items() for kid in kids}


def is_archived(link: Link, community: Community, *, now: datetime | None = None) -> bool:
    age = (now or datetime.now(UTC)) - link.created
    return age > timedelta(days=community.settings.archive_age_days)


def check_thread(link: Link, community: Community, *, field: str) -> None:
    """A thread takes no new comments or votes once deleted, removed or archived."""
    if link.deleted or link.removed:
        raise InaError.of(ErrorCode.DELETED_COMMENT, field)
    if is_archived(link, community):
        raise InaError.of(ErrorCode.TOO_OLD, field)


async def post(
    comments: CommentRepo,
    votes: VoteRepo,
    account: Account,
    link: Link,
    community: Community,
    parent: Comment | None,
    text: str,
    *,
    standing: Standing,
    ip: str,
    now: datetime | None = None,
) -> Comment:
    """Reply to a link (``parent`` None) or a comment, upvoted by its author as on reddit."""
    if not can(account, Action.COMMENT, community, standing=standing):
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'parent', status_code=403)
    check_thread(link, community, field='parent')
    moderator = standing.has(ModPermission.POSTS)
    if link.locked and not moderator:
        raise InaError.of(ErrorCode.THREAD_LOCKED, 'parent')
    # Only the author or a moderator may reply to a removed comment, as on r2.
    removed = parent is not None and (parent.removed or parent.spam)
    if parent is not None and (
        parent.deleted or (removed and not moderator and parent.author_id != account.id)
    ):
        raise InaError.of(ErrorCode.DELETED_COMMENT, 'parent')
    if not text.strip():
        raise InaError.of(ErrorCode.NO_TEXT, 'text')
    if len(text) > MAX_COMMENT:
        raise InaError.of(ErrorCode.TOO_LONG, 'text', max_length=MAX_COMMENT)
    comment = await comments.create(
        NewComment(
            link_id=link.id,
            parent=parent,
            author_id=account.id,
            body_md=text,
            body_html=markdown.render(text),
            sendreplies=True,
            ip=ip,
            created=now or datetime.now(UTC),
            upvotes=1,
            downvotes=0,
            confidence=ranking.confidence(1, 0),
            controversy_score=ranking.controversy(1, 0),
        )
    )
    await votes.cast(account.id, comment.fullname, 1)
    return comment
