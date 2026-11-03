"""Private messages, modmail, and what reaches an inbox.

A message goes to a user, or to a community's moderators (modmail). Comment replies are not
messages: an inbox reads them straight from the comments. Username mentions are recorded by the
inbox job, which also keeps each account's unread count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import ModPermission, NewMessage
from app.services.perms import Action, Standing, can
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import (
        Account,
        AccountRepo,
        Comment,
        Community,
        CommunityRepo,
        Link,
        Message,
        MessageRepo,
    )

MAX_SUBJECT = 100
MAX_BODY = 10000
# A comment naming more people than this notifies none of them, as on r2.
MAX_MENTIONS = 3
COMMUNITY_RECIPIENT = re.compile(r'\A(?:/?in/|#)(?P<name>[\w-]+)/?\Z')
USER_RECIPIENT = re.compile(r'\A(?:/?u/)?(?P<name>[\w-]+)/?\Z')
# snudown autolinks /u/name and u/name to href="/u/name".
MENTION = re.compile(r'href="/u/([\w-]+)"')


@dataclass
class ComposeForm:
    """``/api/compose``, with r2's field names."""

    to: str = ''
    subject: str = ''
    text: str = ''
    # A community the sender moderates, to write as it.
    from_community: str = ''


@dataclass(frozen=True, slots=True)
class Recipient:
    account: Account | None
    community: Community | None


async def recipient(accounts: AccountRepo, communities: CommunityRepo, to: str) -> Recipient:
    to = to.strip()
    if not to:
        raise InaError.of(ErrorCode.NO_USER, 'to')
    if match := COMMUNITY_RECIPIENT.match(to):
        community = await communities.by_name(match['name'])
        if community is None:
            raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'to')
        return Recipient(None, community)
    match = USER_RECIPIENT.match(to)
    account = await accounts.by_name(match['name']) if match else None
    if account is None or account.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'to')
    return Recipient(account, None)


def _text_errors(subject: str, body: str) -> list[FieldError]:
    errors = []
    if not subject.strip():
        errors.append(FieldError(ErrorCode.NO_SUBJECT, 'subject'))
    elif len(subject) > MAX_SUBJECT:
        errors.append(FieldError(ErrorCode.TOO_LONG, 'subject', {'max_length': MAX_SUBJECT}))
    if not body.strip():
        errors.append(FieldError(ErrorCode.NO_TEXT, 'text'))
    elif len(body) > MAX_BODY:
        errors.append(FieldError(ErrorCode.TOO_LONG, 'text', {'max_length': MAX_BODY}))
    return errors


async def compose(
    messages: MessageRepo,
    accounts: AccountRepo,
    communities: CommunityRepo,
    sender: Account,
    form: ComposeForm,
    *,
    ip: str,
) -> Message:
    errors = _text_errors(form.subject, form.text)
    try:
        to = await recipient(accounts, communities, form.to)
    except InaError as error:
        errors.extend(error.errors)
        to = None
    as_community = None
    if form.from_community.strip():
        as_community = await communities.by_name(form.from_community.strip())
        if as_community is None:
            errors.append(FieldError(ErrorCode.COMMUNITY_NOEXIST, 'from_community'))
        elif ModPermission.MAIL not in (
            await communities.permissions(as_community.id, sender.id) or ModPermission(0)
        ):
            errors.append(FieldError(ErrorCode.COMMUNITY_NOTALLOWED, 'from_community'))
        elif to is not None and to.community is not None:
            errors.append(FieldError(ErrorCode.NO_USER, 'to'))
    if (
        to is not None
        and to.community is not None
        and await communities.is_muted(to.community.id, sender.id)
    ):
        errors.append(FieldError(ErrorCode.USER_MUTED, 'to'))
    if errors or to is None:
        raise InaError(*errors)
    modmail = as_community or to.community
    return await messages.create(
        NewMessage(
            from_id=sender.id,
            to_id=to.account.id if to.account else None,
            community_id=modmail.id if modmail else None,
            parent=None,
            subject=form.subject.strip(),
            body_md=form.text,
            body_html=markdown.render(form.text),
            from_community=as_community is not None,
            ip=ip,
            created=datetime.now(UTC),
        )
    )


def can_read(account: Account, message: Message, mail_communities: list[int]) -> bool:
    """The sender, the recipient, and moderators with mail access for modmail."""
    return account.id in {message.from_id, message.to_id} or message.community_id in set(
        mail_communities
    )


async def reply(
    messages: MessageRepo,
    communities: CommunityRepo,
    sender: Account,
    parent: Message,
    text: str,
    *,
    mail_communities: list[int],
    ip: str,
) -> Message:
    """Answer a message within its conversation.

    In modmail a moderator's reply goes to the person who wrote in, and that person's reply
    goes back to the moderators.
    """
    if not can_read(sender, parent, mail_communities):
        raise InaError.of(ErrorCode.NO_THING_ID, 'parent', status_code=403)
    if errors := _text_errors('re', text):
        raise InaError(*errors)
    first = await messages.by_id(parent.first_message_id) or parent
    if parent.community_id is not None and parent.community_id in set(mail_communities):
        # A moderator answering: to whoever is on the other side of the conversation.
        other = first.from_id if first.to_id is None else first.to_id
        to_id = other if other != sender.id else first.from_id
    elif parent.community_id is not None:
        # Writing in to the moderators, which a mute stops.
        if await communities.is_muted(parent.community_id, sender.id):
            raise InaError.of(ErrorCode.USER_MUTED, 'parent')
        to_id = None
    else:
        to_id = parent.from_id if parent.from_id != sender.id else parent.to_id
    subject = first.subject if first.subject.startswith('re: ') else f're: {first.subject}'
    return await messages.create(
        NewMessage(
            from_id=sender.id,
            to_id=to_id,
            community_id=parent.community_id,
            parent=parent,
            subject=subject[:MAX_SUBJECT],
            body_md=text,
            body_html=markdown.render(text),
            from_community=False,
            ip=ip,
            created=datetime.now(UTC),
        )
    )


def mentioned(comment: Comment) -> set[str]:
    """Lowercased names a comment mentions, or none if it names too many, as on r2."""
    names = {name.lower() for name in MENTION.findall(comment.body_html)}
    return names if len(names) <= MAX_MENTIONS else set()


def recipient_of(comment: Comment, parent: Comment | None, link: Link) -> int | None:
    """Whose inbox a comment lands in as a reply, if anyone's."""
    if parent is not None:
        owner, wanted = parent.author_id, parent.sendreplies
    else:
        owner, wanted = link.author_id, link.sendreplies
    return owner if wanted and owner != comment.author_id else None


async def deliver_comment(
    messages: MessageRepo,
    accounts: AccountRepo,
    communities: CommunityRepo,
    comment: Comment,
    parent: Comment | None,
    link: Link,
    community: Community,
) -> set[int]:
    """Record the mentions a new comment makes; return every account whose inbox it reached."""
    reached = set()
    if (replied := recipient_of(comment, parent, link)) is not None:
        reached.add(replied)
    if comment.deleted or comment.removed or comment.spam:
        return reached
    for name in mentioned(comment):
        account = await accounts.by_name(name)
        if account is None or account.deleted or account.id == comment.author_id:
            continue
        if not account.prefs.monitor_mentions or account.id == replied:
            continue
        mod, contributor, banned = await communities.standing(community.id, account.id)
        if not can(account, Action.VIEW, community, standing=Standing(mod, contributor, banned)):
            continue
        if await messages.add_mention(account.id, comment.id):
            reached.add(account.id)
    return reached


async def recount(messages: MessageRepo, accounts: AccountRepo, account_id: int) -> int:
    """Rewrite an account's unread count from its inbox; safe to repeat."""
    count = await messages.unread_count(account_id)
    await accounts.set_inbox_count(account_id, count)
    return count
