"""/message: the inbox and its boxes, conversations, composing, and read state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from litestar import Request, get, post
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody

from app.controllers.auth import WHILE_SUSPENDED, client_ip, local_path, to_viewer
from app.controllers.deps import (
    AccountsDep,
    CommentsDep,
    CommunitiesDep,
    LinksDep,
    MessagesDep,
    RedisDep,
    UserDep,
    VotesDep,
)
from app.controllers.queue import publish
from app.controllers.render import Rendered
from app.errors import InaError
from app.ids import Fullname, Kind, from36
from app.jobs.inbox import deliver_message
from app.repos.base import (
    AccountRepo,
    Box,
    CommentRepo,
    CommunityRepo,
    EntryKind,
    InboxEntry,
    LinkRepo,
    MessageRepo,
    VoteRepo,
)
from app.services import messaging, ratelimits
from app.services.messaging import ComposeForm
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.messages import (
    ComposePage,
    ConversationPage,
    InboxComment,
    InboxItem,
    MessageItem,
    MessagesPage,
)

PAGE_SIZE = 25
# Opening these boxes marks what they show as read, when the viewer's prefs say so.
READING_BOXES = frozenset(
    {Box.INBOX, Box.UNREAD, Box.MESSAGES, Box.COMMENTS, Box.SELFREPLY, Box.MENTIONS}
)


@dataclass
class ReadForm:
    # Comma-separated fullnames.
    id: str = ''
    dest: str = ''


def fullnames(text: str) -> list[Fullname]:
    found = []
    for part in text.split(','):
        try:
            found.append(Fullname.parse(part.strip()))
        except ValueError:
            continue
    return found


async def from_communities(communities: CommunityRepo, user: Authenticated) -> tuple[str, ...]:
    names = []
    for community_id in user.mail_communities:
        community = await communities.by_id(community_id)
        if community is not None:
            names.append(community.name)
    return tuple(sorted(names, key=str.lower))


@get('/message/compose')
async def show_compose(
    communities: CommunitiesDep,
    user: UserDep,
    to: FromQuery[str] = '',
    subject: FromQuery[str] = '',
    message: FromQuery[str] = '',
) -> ComposePage:
    return ComposePage(
        viewer=to_viewer(user),
        form=ComposeForm(to=to, subject=subject, text=message),
        from_communities=await from_communities(communities, user),
    )


@post('/api/compose')
async def compose(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[ComposeForm],
    messages: MessagesDep,
    accounts: AccountsDep,
    communities: CommunitiesDep,
    redis: RedisDep,
    user: UserDep,
) -> Rendered:
    try:
        await ratelimits.enforce(redis, ratelimits.MESSAGE_BY_ACCOUNT, str(user.account.id))
        sent = await messaging.compose(
            messages, accounts, communities, user.account, data, ip=client_ip(request)
        )
    except InaError as error:
        page = ComposePage(
            viewer=to_viewer(user),
            form=data,
            from_communities=await from_communities(communities, user),
            errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    return Rendered(
        content=FormResult(redirect='/message/sent/'),
        background=publish(deliver_message, str(sent.fullname)),
    )


async def inbox_items(
    entries: list[InboxEntry],
    *,
    messages: MessageRepo,
    comments: CommentRepo,
    links: LinkRepo,
    votes: VoteRepo,
    account_id: int,
) -> tuple[InboxItem, ...]:
    message_ids = [e.thing_id for e in entries if e.kind is EntryKind.MESSAGE]
    comment_ids = [e.thing_id for e in entries if e.kind is not EntryKind.MESSAGE]
    found_messages = await messages.by_ids(message_ids)
    found_comments = await comments.by_ids(comment_ids)
    found_links = await links.by_ids(sorted({c.link_id for c in found_comments.values()}))
    likes = await votes.directions(account_id, Kind.COMMENT, comment_ids)
    items: list[InboxItem] = []
    for entry in entries:
        if entry.kind is EntryKind.MESSAGE:
            if (message := found_messages.get(entry.thing_id)) is not None:
                items.append(MessageItem(message, entry.unread))
            continue
        comment = found_comments.get(entry.thing_id)
        link = found_links.get(comment.link_id) if comment else None
        if comment is not None and link is not None:
            direction = likes.get(comment.id, 0)
            items.append(
                InboxComment(
                    comment, link, entry.kind, entry.unread, {1: True, -1: False}.get(direction)
                )
            )
    return tuple(items)


async def mark_and_recount(
    messages: MessageRepo,
    accounts: AccountRepo,
    user: Authenticated,
    things: list[Fullname],
    *,
    read: bool,
) -> None:
    if things:
        await messages.mark(user.account.id, things, read=read)
    await messaging.recount(messages, accounts, user.account.id)


async def box_page(
    shown_box: Box,
    after: str,
    *,
    messages: MessageRepo,
    accounts: AccountRepo,
    comments: CommentRepo,
    links: LinkRepo,
    votes: VoteRepo,
    user: Authenticated,
) -> MessagesPage:
    anchor = None
    if after:
        try:
            anchor = await messages.entry(user.account.id, Fullname.parse(after))
        except ValueError:
            anchor = None
    entries = await messages.inbox(
        user.account.id,
        shown_box,
        limit=PAGE_SIZE + 1,
        after=anchor,
        moderated=list(user.mail_communities),
    )
    more = len(entries) > PAGE_SIZE
    entries = entries[:PAGE_SIZE]
    items = await inbox_items(
        entries,
        messages=messages,
        comments=comments,
        links=links,
        votes=votes,
        account_id=user.account.id,
    )
    if shown_box is Box.MODERATOR:
        await accounts.set_modmsgtime(user.account.id, datetime.now(UTC))
    elif shown_box in READING_BOXES and user.account.prefs.mark_messages_read:
        unread = [entry.fullname for entry in entries if entry.unread]
        if unread:
            await mark_and_recount(messages, accounts, user, unread, read=True)
    return MessagesPage(
        viewer=to_viewer(user),
        box=shown_box,
        items=items,
        after=str(entries[-1].fullname) if more and entries else None,
    )


@get('/message/{box:str}')
async def show_box(
    box: FromPath[str],
    messages: MessagesDep,
    accounts: AccountsDep,
    comments: CommentsDep,
    links: LinksDep,
    votes: VotesDep,
    user: UserDep,
    after: FromQuery[str] = '',
) -> MessagesPage:
    if box not in Box:
        raise NotFoundException
    return await box_page(
        Box(box),
        after,
        messages=messages,
        accounts=accounts,
        comments=comments,
        links=links,
        votes=votes,
        user=user,
    )


# Its own route: /message/messages/{id36} would otherwise claim the path from /message/{box}.
@get('/message/messages')
async def show_messages_box(
    messages: MessagesDep,
    accounts: AccountsDep,
    comments: CommentsDep,
    links: LinksDep,
    votes: VotesDep,
    user: UserDep,
    after: FromQuery[str] = '',
) -> MessagesPage:
    return await box_page(
        Box.MESSAGES,
        after,
        messages=messages,
        accounts=accounts,
        comments=comments,
        links=links,
        votes=votes,
        user=user,
    )


async def conversation_page(
    messages: MessageRepo,
    accounts: AccountRepo,
    user: Authenticated,
    id36: str,
    *,
    reply_text: str = '',
    reply_errors: tuple[Any, ...] = (),
) -> ConversationPage:
    try:
        first = await messages.by_id(from36(id36))
    except ValueError:
        first = None
    if first is None or not messaging.can_read(user.account, first, list(user.mail_communities)):
        raise NotFoundException
    conversation = await messages.conversation(first.first_message_id)
    unread = [m.fullname for m in conversation if m.to_id == user.account.id and not m.read]
    if unread:
        await mark_and_recount(messages, accounts, user, unread, read=True)
    return ConversationPage(
        viewer=to_viewer(user),
        items=tuple(
            MessageItem(m, m.fullname in unread)
            for m in conversation
            if messaging.can_read(user.account, m, list(user.mail_communities))
        ),
        reply_text=reply_text,
        reply_errors=reply_errors,
    )


@get('/message/messages/{id36:str}')
async def show_conversation(
    id36: FromPath[str], messages: MessagesDep, accounts: AccountsDep, user: UserDep
) -> ConversationPage:
    return await conversation_page(messages, accounts, user, id36)


@post('/api/read_message', opt={WHILE_SUSPENDED: True})
async def read_message(
    data: URLEncodedBody[ReadForm], messages: MessagesDep, accounts: AccountsDep, user: UserDep
) -> Rendered:
    await mark_and_recount(messages, accounts, user, fullnames(data.id), read=True)
    return Rendered(content=FormResult(redirect=local_path(data.dest, '/message/inbox/')))


@post('/api/unread_message', opt={WHILE_SUSPENDED: True})
async def unread_message(
    data: URLEncodedBody[ReadForm], messages: MessagesDep, accounts: AccountsDep, user: UserDep
) -> Rendered:
    await mark_and_recount(messages, accounts, user, fullnames(data.id), read=False)
    return Rendered(content=FormResult(redirect=local_path(data.dest, '/message/inbox/')))


@post('/api/read_all_messages', opt={WHILE_SUSPENDED: True})
async def read_all_messages(
    data: URLEncodedBody[ReadForm], messages: MessagesDep, accounts: AccountsDep, user: UserDep
) -> Rendered:
    await messages.mark_all_read(user.account.id)
    await messaging.recount(messages, accounts, user.account.id)
    return Rendered(content=FormResult(redirect=local_path(data.dest, '/message/inbox/')))
