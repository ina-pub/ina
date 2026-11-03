"""The inbox, conversations and the compose form, as templates and r2's JSON draw them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from app.ids import Fullname, Kind
from app.repos.base import Box, EntryKind
from app.views.base import Page
from app.views.links import permalink
from app.views.listing import listing_json, prefixed

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Comment, Link, Message
    from app.services.messaging import ComposeForm

SUBJECTS = {
    EntryKind.COMMENT_REPLY: 'comment reply',
    EntryKind.POST_REPLY: 'post reply',
    EntryKind.MENTION: 'username mention',
}


@dataclass(frozen=True, slots=True)
class MessageItem:
    message: Message
    unread: bool

    @property
    def author(self) -> str:
        """Who it is from: the community itself when a moderator wrote as it."""
        m = self.message
        return f'/in/{m.community_name}' if m.from_community and m.community_name else m.from_name

    @property
    def dest(self) -> str:
        """r2's ``dest``: the recipient's name, or ``#community`` for modmail."""
        return self.message.to_name or f'#{self.message.community_name}'

    @property
    def permalink(self) -> str:
        first = Fullname(Kind.MESSAGE, self.message.first_message_id)
        return f'/message/messages/{first.id36}'

    def to_json(self) -> JSON:
        m = self.message
        created = m.created.timestamp()
        first = Fullname(Kind.MESSAGE, m.first_message_id)
        parent = str(Fullname(Kind.MESSAGE, m.parent_id)) if m.parent_id else None
        return {
            'kind': 't4',
            'data': {
                'id': m.fullname.id36,
                'name': str(m.fullname),
                'author': None if m.from_community else m.from_name,
                'dest': self.dest,
                'subject': m.subject,
                'body': m.body_md,
                'body_html': m.body_html,
                'created': created,
                'created_utc': created,
                'new': self.unread,
                'was_comment': False,
                'first_message': first.id if m.parent_id else None,
                'first_message_name': str(first) if m.parent_id else None,
                'parent_id': parent,
                'replies': '',
                'context': '',
                'community': m.community_name,
                'community_name_prefixed': prefixed(m.community_name),
                'distinguished': 'moderator' if m.from_community else None,
            },
        }


@dataclass(frozen=True, slots=True)
class InboxComment:
    """A comment shown in an inbox: a reply to the viewer, or a mention of them."""

    comment: Comment
    link: Link
    kind: EntryKind
    unread: bool
    likes: bool | None

    @property
    def subject(self) -> str:
        return SUBJECTS[self.kind]

    @property
    def permalink(self) -> str:
        return f'{permalink(self.link)}{self.comment.fullname.id36}/'

    @property
    def context(self) -> str:
        return f'{self.permalink}?context=3'

    def to_json(self) -> JSON:
        c = self.comment
        created = c.created.timestamp()
        return {
            'kind': 't1',
            'data': {
                'id': c.fullname.id36,
                'name': str(c.fullname),
                'author': c.author_name,
                'dest': None,
                'subject': self.subject,
                'type': self.kind.value,
                'body': c.body_md,
                'body_html': c.body_html,
                'created': created,
                'created_utc': created,
                'new': self.unread,
                'was_comment': True,
                'link_title': self.link.title,
                'context': self.context,
                'parent_id': str(c.parent_fullname),
                'community': c.community_name,
                'community_name_prefixed': prefixed(c.community_name),
                'likes': self.likes,
                'replies': '',
                'distinguished': c.distinguished,
            },
        }


type InboxItem = MessageItem | InboxComment


@dataclass(frozen=True, kw_only=True)
class MessagesPage(Page):
    """``/message/{box}``."""

    template: ClassVar[str] = 'messages.html'
    has_json: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    box: Box
    items: tuple[InboxItem, ...]
    after: str | None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    tabs: ClassVar[tuple[tuple[Box, str], ...]] = (
        (Box.INBOX, 'everything'),
        (Box.UNREAD, 'unread'),
        (Box.MESSAGES, 'messages'),
        (Box.COMMENTS, 'replies to comments'),
        (Box.SELFREPLY, 'replies to posts'),
        (Box.MENTIONS, 'mentions'),
    )

    @property
    def title(self) -> str:
        return {Box.SENT: 'sent', Box.MODERATOR: 'modmail'}.get(self.box, 'inbox')

    def to_json(self) -> JSON:
        children: list[JSON] = [item.to_json() for item in self.items]
        modhash = self.viewer.modhash if self.viewer else ''
        return listing_json(children, after=self.after, before=None, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class ConversationPage(Page):
    """``/message/messages/{id36}``: a conversation, oldest first, with a reply form."""

    template: ClassVar[str] = 'conversation.html'
    has_json: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    items: tuple[MessageItem, ...]
    reply_text: str = ''
    reply_errors: tuple[FieldError, ...] = ()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return f'messages: {self.items[0].message.subject}' if self.items else 'messages'

    @property
    def reply_to(self) -> str:
        return str(self.items[-1].message.fullname)

    def to_json(self) -> JSON:
        modhash = self.viewer.modhash if self.viewer else ''
        first, *rest = self.items
        data = first.to_json()
        assert isinstance(data, dict)  # noqa: S101 - to_json builds a dict
        inner = data['data']
        assert isinstance(inner, dict)  # noqa: S101 - likewise
        replies: list[JSON] = [item.to_json() for item in rest]
        inner['replies'] = (
            listing_json(replies, after=None, before=None, modhash=modhash) if replies else ''
        )
        return listing_json([data], after=None, before=None, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class ComposePage(Page):
    """``/message/compose``."""

    template: ClassVar[str] = 'compose.html'
    cacheable: ClassVar[bool] = False

    form: ComposeForm
    # Communities the viewer may write as.
    from_communities: tuple[str, ...] = ()
    errors: tuple[FieldError, ...] = ()

    @property
    def title(self) -> str:
        return 'messages: compose'
