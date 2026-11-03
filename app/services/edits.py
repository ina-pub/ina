"""Authors editing and deleting what they posted, as r2's /api/editusertext and /api/del.

Only the author may do either. A self post's text and a comment's body can be edited until the
thread is archived; a link's title and URL never can. Deleting keeps the row: the thing shows as
[deleted], its comments stay, and it leaves listings, search and karma.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.errors import ErrorCode, InaError
from app.repos.base import Comment, Link
from app.services.comments import MAX_COMMENT, is_archived
from app.services.links import MAX_SELFTEXT
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import Account, CommentRepo, Community, LinkRepo


def _require_author(account: Account, thing: Link | Comment) -> None:
    if thing.author_id != account.id:
        raise InaError.of(ErrorCode.NOT_AUTHOR, 'thing_id', status_code=403)


def check_editable(
    account: Account, thing: Link | Comment, link: Link, community: Community
) -> None:
    """Raise unless ``account`` may edit ``thing`` now."""
    _require_author(account, thing)
    if thing.deleted or (isinstance(thing, Link) and not thing.is_self):
        raise InaError.of(ErrorCode.NOT_AUTHOR, 'thing_id', status_code=403)
    if is_archived(link, community):
        raise InaError.of(ErrorCode.TOO_OLD, 'thing_id')


async def edit(
    links: LinkRepo,
    comments: CommentRepo,
    account: Account,
    thing: Link | Comment,
    link: Link,
    community: Community,
    text: str,
    *,
    now: datetime | None = None,
) -> str:
    """Replace the text of a self post or comment; return its new HTML."""
    check_editable(account, thing, link, community)
    limit = MAX_SELFTEXT if isinstance(thing, Link) else MAX_COMMENT
    if isinstance(thing, Comment) and not text.strip():
        raise InaError.of(ErrorCode.NO_TEXT, 'text')
    if len(text) > limit:
        raise InaError.of(ErrorCode.TOO_LONG, 'text', max_length=limit)
    html = markdown.render(text) if text.strip() else ''
    at = now or datetime.now(UTC)
    if isinstance(thing, Link):
        await links.edit(thing.id, text, html, at)
    else:
        await comments.edit(thing.id, text, html, at)
    return html


async def delete(
    links: LinkRepo, comments: CommentRepo, account: Account, thing: Link | Comment
) -> bool:
    """Delete the author's own thing; return False when it already was."""
    _require_author(account, thing)
    if thing.deleted:
        return False
    if isinstance(thing, Link):
        await links.delete(thing.id)
    else:
        await comments.delete(thing.id)
    return True
