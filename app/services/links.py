"""Submitting links and self posts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import NewLink
from app.services import ranking
from app.services.perms import Action, Standing, can
from app.text import markdown, urls

if TYPE_CHECKING:
    from app.repos.base import Account, Community, Link, LinkRepo, VoteRepo

MAX_TITLE = 300
MAX_SELFTEXT = 40000
KINDS = ('link', 'self')


@dataclass
class SubmitForm:
    """``/api/submit``, with r2's field names."""

    community: str = ''
    kind: str = 'link'
    title: str = ''
    url: str = ''
    text: str = ''
    # An unticked checkbox is not sent, so absent means no, as on r2; the blank form ticks it.
    sendreplies: str = ''
    resubmit: str = ''


def _checked(value: str) -> bool:
    return value not in {'', 'off', 'false'}


def _kind_errors(kind: str, community: Community) -> list[FieldError]:
    link_type = community.settings.link_type
    if kind not in KINDS:
        return [FieldError(ErrorCode.INVALID_OPTION, 'kind')]
    if kind == 'link' and link_type == 'self':
        return [FieldError(ErrorCode.NO_LINKS, 'community')]
    if kind == 'self' and link_type == 'link':
        return [FieldError(ErrorCode.NO_SELFS, 'community')]
    return []


def _title_errors(title: str) -> list[FieldError]:
    if not title:
        return [FieldError(ErrorCode.NO_TEXT, 'title')]
    if len(title) > MAX_TITLE:
        return [FieldError(ErrorCode.TOO_LONG, 'title', {'max_length': MAX_TITLE})]
    return []


def _body(form: SubmitForm) -> tuple[str, list[FieldError]]:
    """The cleaned URL of a link post (empty for a self post), and any errors in the body."""
    if form.kind != 'link':
        if len(form.text) > MAX_SELFTEXT:
            return '', [FieldError(ErrorCode.TOO_LONG, 'text', {'max_length': MAX_SELFTEXT})]
        return '', []
    if not form.url.strip():
        return '', [FieldError(ErrorCode.NO_URL, 'url')]
    if (url := urls.clean_url(form.url)) is None:
        return '', [FieldError(ErrorCode.BAD_URL, 'url')]
    return url, []


async def submit(
    links: LinkRepo,
    votes: VoteRepo,
    account: Account,
    community: Community,
    form: SubmitForm,
    *,
    standing: Standing,
    ip: str,
    now: datetime | None = None,
) -> Link:
    """Post a link or self post, upvoted by its author as on reddit."""
    if not can(account, Action.SUBMIT, community, standing=standing):
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'community', status_code=403)
    title = form.title.strip()
    url, body_errors = _body(form)
    errors = _kind_errors(form.kind, community) + _title_errors(title) + body_errors
    resubmitting = _checked(form.resubmit)
    if url and not errors and not resubmitting and await links.by_url(community.id, url):
        errors.append(FieldError(ErrorCode.ALREADY_SUB, 'url'))
    if errors:
        raise InaError(*errors)
    is_self = form.kind == 'self'
    created = now or datetime.now(UTC)
    link = await links.create(
        NewLink(
            community_id=community.id,
            author_id=account.id,
            title=title,
            url=url,
            domain=f'self.{community.name}' if is_self else urls.domain(url),
            is_self=is_self,
            selftext_md=form.text if is_self else '',
            selftext_html=markdown.render(form.text) if is_self else '',
            nsfw=community.nsfw,
            sendreplies=_checked(form.sendreplies),
            ip=ip,
            created=created,
            upvotes=1,
            downvotes=0,
            hot_score=ranking.hot(1, 0, created),
            controversy_score=ranking.controversy(1, 0),
        )
    )
    await votes.cast(account.id, link.fullname, 1)
    return link
