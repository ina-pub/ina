from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Integer,
    and_,
    exists,
    false,
    func,
    insert,
    literal,
    or_,
    select,
    true,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.orm import aliased

from app.ids import Kind
from app.repos.base import Box, EntryKind, InboxEntry, Message
from app.repos.pg.tables import (
    accounts,
    comments,
    communities,
    links,
    mentions,
    messages,
    relations,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement, Row, Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ids import Fullname
    from app.repos.base import NewMessage

sender = aliased(accounts, name='sender')
recipient = aliased(accounts, name='recipient')
COLUMNS = (
    messages.c.id,
    messages.c.from_id,
    sender.c.name.label('from_name'),
    messages.c.to_id,
    recipient.c.name.label('to_name'),
    messages.c.community_id,
    communities.c.name.label('community_name'),
    messages.c.parent_id,
    messages.c.first_message_id,
    messages.c.subject,
    messages.c.body_md,
    messages.c.body_html,
    messages.c.read,
    messages.c.created,
    messages.c.from_community,
    messages.c.del_on_recipient,
)
JOINED = (
    messages.join(sender, sender.c.id == messages.c.from_id)
    .outerjoin(recipient, recipient.c.id == messages.c.to_id)
    .outerjoin(communities, communities.c.id == messages.c.community_id)
)
# A tiebreak between entries created in the same instant; only the order matters.
RANKS = {
    EntryKind.MESSAGE: 0,
    EntryKind.COMMENT_REPLY: 1,
    EntryKind.POST_REPLY: 1,
    EntryKind.MENTION: 1,
}
COMMENT_KINDS = [EntryKind.COMMENT_REPLY.value, EntryKind.POST_REPLY.value, EntryKind.MENTION.value]
parent = aliased(comments, name='parent')


def to_message(row: Row[*tuple[Any, ...]]) -> Message:
    return Message(**row._asdict())


def _live(table: Any) -> ColumnElement[bool]:  # noqa: ANN401 - a comments table or alias
    return and_(table.c.deleted == false(), table.c.removed == false(), table.c.spam == false())


def _not_blocked(account_id: int, author: ColumnElement[int]) -> ColumnElement[bool]:
    """Not from someone the account blocked: r2 kept blocked accounts out of the inbox."""
    blocked = select(relations.c.target_id).where(
        relations.c.account_id == account_id,
        relations.c.target_id == author,
        relations.c.type == 'enemy',
    )
    return ~exists(blocked)


def _entries(
    kind: EntryKind,
    statement: Select[*tuple[Any, ...]],
    thing_id: ColumnElement[int],
    created: ColumnElement[Any],
    unread: ColumnElement[bool],
) -> Select[*tuple[Any, ...]]:
    return statement.with_only_columns(
        literal(kind.value).label('kind'),
        literal(RANKS[kind], Integer).label('rank'),
        thing_id.label('thing_id'),
        created.label('created'),
        unread.label('unread'),
    )


def _comment_replies(account_id: int) -> Select[*tuple[Any, ...]]:
    """Replies to the account's comments, from anyone else, where it asked for replies."""
    base = (
        select(comments.c.id)
        .select_from(comments.join(parent, parent.c.id == comments.c.parent_id))
        .where(
            parent.c.author_id == account_id,
            parent.c.sendreplies == true(),
            comments.c.author_id != account_id,
            _live(comments),
            _not_blocked(account_id, comments.c.author_id),
        )
    )
    return _entries(
        EntryKind.COMMENT_REPLY, base, comments.c.id, comments.c.created, comments.c.new
    )


def _post_replies(account_id: int) -> Select[*tuple[Any, ...]]:
    """Top-level comments on the account's links, from anyone else."""
    base = (
        select(comments.c.id)
        .select_from(comments.join(links, links.c.id == comments.c.link_id))
        .where(
            comments.c.parent_id.is_(None),
            links.c.author_id == account_id,
            links.c.sendreplies == true(),
            comments.c.author_id != account_id,
            _live(comments),
            _not_blocked(account_id, comments.c.author_id),
        )
    )
    return _entries(EntryKind.POST_REPLY, base, comments.c.id, comments.c.created, comments.c.new)


def _mentions(account_id: int) -> Select[*tuple[Any, ...]]:
    base = (
        select(mentions.c.comment_id)
        .select_from(mentions.join(comments, comments.c.id == mentions.c.comment_id))
        .where(
            mentions.c.account_id == account_id,
            _live(comments),
            _not_blocked(account_id, comments.c.author_id),
        )
    )
    return _entries(
        EntryKind.MENTION, base, mentions.c.comment_id, comments.c.created, ~mentions.c.read
    )


def _received(account_id: int) -> Select[*tuple[Any, ...]]:
    base = select(messages.c.id).where(
        messages.c.to_id == account_id,
        messages.c.del_on_recipient == false(),
        _not_blocked(account_id, messages.c.from_id),
    )
    return _entries(EntryKind.MESSAGE, base, messages.c.id, messages.c.created, ~messages.c.read)


def _parts(account_id: int, box: Box, moderated: list[int]) -> list[Select[*tuple[Any, ...]]]:
    if box is Box.SENT:
        sent = select(messages.c.id).where(messages.c.from_id == account_id)
        return [_entries(EntryKind.MESSAGE, sent, messages.c.id, messages.c.created, false())]
    if box is Box.MODERATOR:
        modmail = select(messages.c.id).where(messages.c.community_id.in_(moderated or [-1]))
        return [_entries(EntryKind.MESSAGE, modmail, messages.c.id, messages.c.created, false())]
    return {
        Box.MESSAGES: [_received(account_id)],
        Box.COMMENTS: [_comment_replies(account_id)],
        Box.SELFREPLY: [_post_replies(account_id)],
        Box.MENTIONS: [_mentions(account_id)],
    }.get(
        box,
        [
            _received(account_id),
            _comment_replies(account_id),
            _post_replies(account_id),
            _mentions(account_id),
        ],
    )


class PgMessageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select[*tuple[Any, ...]]:
        return select(*COLUMNS).select_from(JOINED)

    async def create(self, message: NewMessage) -> Message:
        first = message.parent.first_message_id if message.parent else None
        statement = (
            insert(messages)
            .values(
                from_id=message.from_id,
                to_id=message.to_id,
                community_id=message.community_id,
                parent_id=message.parent.id if message.parent else None,
                first_message_id=first,
                subject=message.subject,
                body_md=message.body_md,
                body_html=message.body_html,
                from_community=message.from_community,
                ip=message.ip,
                created=message.created,
            )
            .returning(messages.c.id)
        )
        message_id = (await self.session.execute(statement)).scalar_one()
        if first is None:
            # A new conversation starts with itself.
            await self.session.execute(
                update(messages)
                .where(messages.c.id == message_id)
                .values(first_message_id=message_id)
            )
        created = await self.by_id(message_id)
        assert created is not None  # noqa: S101 - just inserted in this transaction
        return created

    async def by_id(self, message_id: int) -> Message | None:
        statement = self._select().where(messages.c.id == message_id)
        row = (await self.session.execute(statement)).first()
        return to_message(row) if row else None

    async def by_ids(self, message_ids: list[int]) -> dict[int, Message]:
        if not message_ids:
            return {}
        rows = await self.session.execute(self._select().where(messages.c.id.in_(message_ids)))
        return {message.id: message for message in map(to_message, rows)}

    async def conversation(self, first_message_id: int) -> list[Message]:
        statement = (
            self._select()
            .where(messages.c.first_message_id == first_message_id)
            .order_by(messages.c.id)
        )
        return [to_message(row) for row in await self.session.execute(statement)]

    async def inbox(
        self,
        account_id: int,
        box: Box,
        *,
        limit: int,
        after: InboxEntry | None,
        moderated: list[int],
    ) -> list[InboxEntry]:
        entries = union_all(*_parts(account_id, box, moderated)).subquery('entries')
        statement = select(entries).order_by(
            entries.c.created.desc(), entries.c.rank.desc(), entries.c.thing_id.desc()
        )
        if box is Box.UNREAD:
            statement = statement.where(entries.c.unread == true())
        if after is not None:
            position = tuple_(entries.c.created, entries.c.rank, entries.c.thing_id)
            statement = statement.where(
                position < (after.created, RANKS[after.kind], after.thing_id)
            )
        rows = await self.session.execute(statement.limit(limit))
        return [
            InboxEntry(
                kind=EntryKind(row.kind),
                thing_id=row.thing_id,
                created=row.created,
                unread=bool(row.unread),
            )
            for row in rows
        ]

    async def entry(self, account_id: int, thing: Fullname) -> InboxEntry | None:
        entries = union_all(*_parts(account_id, Box.INBOX, [])).subquery('entries')
        kinds = [EntryKind.MESSAGE.value] if thing.kind is Kind.MESSAGE else COMMENT_KINDS
        statement = select(entries).where(entries.c.thing_id == thing.id, entries.c.kind.in_(kinds))
        row = (await self.session.execute(statement.limit(1))).first()
        if row is None:
            # Not received: a message the account sent, or modmail, continues sent or modmail.
            other = await self.by_id(thing.id) if thing.kind is Kind.MESSAGE else None
            if other is None or (other.from_id != account_id and other.community_id is None):
                return None
            return InboxEntry(
                kind=EntryKind.MESSAGE, thing_id=other.id, created=other.created, unread=False
            )
        return InboxEntry(
            kind=EntryKind(row.kind), thing_id=row.thing_id, created=row.created, unread=row.unread
        )

    async def mark(self, account_id: int, things: list[Fullname], *, read: bool) -> None:
        message_ids = [t.id for t in things if t.kind is Kind.MESSAGE]
        comment_ids = [t.id for t in things if t.kind is Kind.COMMENT]
        if message_ids:
            await self.session.execute(
                update(messages)
                .where(messages.c.id.in_(message_ids), messages.c.to_id == account_id)
                .values(read=read)
            )
        if comment_ids:
            await self._mark_comments(account_id, comment_ids, read=read)

    async def _mark_comments(self, account_id: int, comment_ids: list[int], *, read: bool) -> None:
        replied_to = exists().where(
            parent.c.id == comments.c.parent_id, parent.c.author_id == account_id
        )
        posted_on = exists().where(
            links.c.id == comments.c.link_id, links.c.author_id == account_id
        )
        await self.session.execute(
            update(comments)
            .where(
                comments.c.id.in_(comment_ids),
                comments.c.author_id != account_id,
                or_(replied_to, and_(comments.c.parent_id.is_(None), posted_on)),
            )
            .values(new=not read)
        )
        await self.session.execute(
            update(mentions)
            .where(mentions.c.account_id == account_id, mentions.c.comment_id.in_(comment_ids))
            .values(read=read)
        )

    async def mark_all_read(self, account_id: int) -> None:
        unread = await self.inbox(account_id, Box.UNREAD, limit=10_000, after=None, moderated=[])
        await self.mark(account_id, [entry.fullname for entry in unread], read=True)

    async def unread_count(self, account_id: int) -> int:
        entries = union_all(*_parts(account_id, Box.INBOX, [])).subquery('entries')
        statement = select(func.count()).select_from(entries).where(entries.c.unread == true())
        return int((await self.session.execute(statement)).scalar_one())

    async def add_mention(self, account_id: int, comment_id: int) -> bool:
        statement = (
            upsert(mentions)
            .values(account_id=account_id, comment_id=comment_id)
            .on_conflict_do_nothing()
            .returning(mentions.c.comment_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def modmail_since(self, community_ids: list[int], since: datetime | None) -> bool:
        if not community_ids:
            return False
        condition: ColumnElement[bool] = messages.c.community_id.in_(community_ids)
        if since is not None:
            condition = and_(condition, messages.c.created > since)
        return bool((await self.session.execute(select(exists().where(condition)))).scalar())
