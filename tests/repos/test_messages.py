"""The MessageRepo contract. Every implementation runs the same tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from app.ids import Fullname, Kind
from app.repos.base import Box, Comment, EntryKind, InboxEntry, MessageRepo, NewComment, NewMessage
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.messages import PgMessageRepo

if TYPE_CHECKING:
    from tests.repos.conftest import World

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


@dataclass
class Mail:
    world: World
    repo: MessageRepo
    alice: int
    bob: int
    carol: int
    clock: int = 0

    def tick(self) -> datetime:
        self.clock += 1
        return T0 + timedelta(minutes=self.clock)

    async def send(
        self,
        sender: int,
        to: int | None,
        subject: str = 'hi',
        *,
        parent_id: int | None = None,
        community_id: int | None = None,
    ) -> int:
        parent = await self.repo.by_id(parent_id) if parent_id else None
        message = await self.repo.create(
            NewMessage(
                from_id=sender,
                to_id=to,
                community_id=community_id,
                parent=parent,
                subject=subject,
                body_md='body',
                body_html='<p>body</p>\n',
                from_community=False,
                ip='127.0.0.1',
                created=self.tick(),
            )
        )
        return message.id

    async def comment(self, author: int, link_id: int, parent: Comment | None) -> Comment:
        return await PgCommentRepo(self.world.session).create(
            NewComment(
                link_id=link_id,
                parent=parent,
                author_id=author,
                body_md='c',
                body_html='<p>c</p>\n',
                sendreplies=True,
                ip='127.0.0.1',
                created=self.tick(),
                upvotes=1,
                downvotes=0,
                confidence=0.0,
                controversy_score=0.0,
            )
        )


@pytest.fixture
async def mail(world: World) -> Mail:
    accounts = PgAccountRepo(world.session)
    bob = (await accounts.create(name='bob', password_hash='h', email=None)).id
    carol = (await accounts.create(name='carol', password_hash='h', email=None)).id
    return Mail(world, PgMessageRepo(world.session), world.account_id, bob, carol)


def kinds(entries: list[InboxEntry]) -> list[tuple[str, int]]:
    return [(entry.kind.value, entry.thing_id) for entry in entries]


async def test_conversations(mail: Mail) -> None:
    first = await mail.send(mail.alice, mail.bob, 'hello')
    second = await mail.send(mail.bob, mail.alice, 're: hello', parent_id=first)
    found = await mail.repo.by_id(second)
    assert found is not None
    assert (found.first_message_id, found.parent_id) == (first, first)
    assert (found.from_name, found.to_name) == ('bob', 'alice')
    assert [m.id for m in await mail.repo.conversation(first)] == [first, second]


async def test_inbox_mixes_messages_replies_and_mentions(mail: Mail) -> None:
    link = await mail.world.link('alices post')
    message = await mail.send(mail.bob, mail.alice)
    post_reply = await mail.comment(mail.bob, link.id, None)
    own = await mail.comment(mail.alice, link.id, post_reply)
    comment_reply = await mail.comment(mail.carol, link.id, own)
    await mail.comment(mail.alice, link.id, comment_reply)
    mentioned = await mail.comment(mail.carol, link.id, post_reply)
    assert await mail.repo.add_mention(mail.alice, mentioned.id) is True
    assert await mail.repo.add_mention(mail.alice, mentioned.id) is False

    inbox = await mail.repo.inbox(mail.alice, Box.INBOX, limit=10, after=None, moderated=[])
    assert kinds(inbox) == [
        ('mention', mentioned.id),
        ('comment_reply', comment_reply.id),
        ('post_reply', post_reply.id),
        ('message', message),
    ]
    assert all(entry.unread for entry in inbox)
    assert await mail.repo.unread_count(mail.alice) == 4
    for box, expected in (
        (Box.MESSAGES, [('message', message)]),
        (Box.COMMENTS, [('comment_reply', comment_reply.id)]),
        (Box.SELFREPLY, [('post_reply', post_reply.id)]),
        (Box.MENTIONS, [('mention', mentioned.id)]),
    ):
        assert kinds(
            await mail.repo.inbox(mail.alice, box, limit=10, after=None, moderated=[])
        ) == (expected)

    page = await mail.repo.inbox(mail.alice, Box.INBOX, limit=2, after=None, moderated=[])
    rest = await mail.repo.inbox(mail.alice, Box.INBOX, limit=10, after=page[-1], moderated=[])
    assert kinds(page + rest) == kinds(inbox)
    anchor = await mail.repo.entry(mail.alice, Fullname(Kind.COMMENT, comment_reply.id))
    assert anchor is not None
    assert anchor.kind is EntryKind.COMMENT_REPLY


async def test_marking_touches_only_your_own_inbox(mail: Mail) -> None:
    link = await mail.world.link('alices post')
    message = await mail.send(mail.bob, mail.alice)
    reply = await mail.comment(mail.bob, link.id, None)
    things = [Fullname(Kind.MESSAGE, message), Fullname(Kind.COMMENT, reply.id)]
    await mail.repo.mark(mail.carol, things, read=True)
    assert await mail.repo.unread_count(mail.alice) == 2
    await mail.repo.mark(mail.alice, things, read=True)
    assert await mail.repo.unread_count(mail.alice) == 0
    await mail.repo.mark(mail.alice, things[:1], read=False)
    unread = await mail.repo.inbox(mail.alice, Box.UNREAD, limit=10, after=None, moderated=[])
    assert kinds(unread) == [('message', message)]
    await mail.repo.mark_all_read(mail.alice)
    assert await mail.repo.unread_count(mail.alice) == 0


async def test_sent_and_modmail(mail: Mail) -> None:
    pics = mail.world.community_ids['pics']
    to_mods = await mail.send(mail.bob, None, 'question', community_id=pics)
    answer = await mail.send(
        mail.alice, mail.bob, 're: question', parent_id=to_mods, community_id=pics
    )
    sent = await mail.repo.inbox(mail.bob, Box.SENT, limit=10, after=None, moderated=[])
    assert kinds(sent) == [('message', to_mods)]
    modmail = await mail.repo.inbox(
        mail.alice, Box.MODERATOR, limit=10, after=None, moderated=[pics]
    )
    assert kinds(modmail) == [('message', answer), ('message', to_mods)]
    assert kinds(
        await mail.repo.inbox(mail.bob, Box.MESSAGES, limit=10, after=None, moderated=[])
    ) == [('message', answer)]
    assert await mail.repo.modmail_since([pics], None) is True
    assert await mail.repo.modmail_since([pics], T0 + timedelta(hours=1)) is False
    assert await mail.repo.modmail_since([], None) is False
