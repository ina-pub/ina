"""Moderator pages: the community's lists, the modlog, and the moderation queues."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from app.ids import Fullname, Kind
from app.repos.base import Queue
from app.services.moderation import PERMISSION_NAMES, permissions_text
from app.views.communities import CommunityPage
from app.views.links import link_data, permalink
from app.views.listing import listing_json

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Comment, Link, Member, ModAction, Moderator

# The lists, by their r2 path names.
LISTS = {
    'moderators': 'innkeepers',
    'contributors': 'approved posters',
    'banned': 'banned',
    'muted': 'muted from modmail',
    'wikicontributors': 'wiki editors',
    'wikibanned': 'banned from the wiki',
}
QUEUE_TITLES = {
    Queue.MODQUEUE: 'needs review: reported, or caught by the spam filter',
    Queue.REPORTS: 'reported',
    Queue.SPAM: 'removed',
    Queue.UNMODERATED: 'links no innkeeper has looked at yet',
}


def _user(member: Moderator | Member, created: datetime, **extra: JSON) -> JSON:
    """An entry in r2's ``UserList``: the account's name and fullname, and when it was added."""
    account = str(Fullname(Kind.ACCOUNT, member.account_id))
    return {'name': member.name, 'id': account, 'date': created.timestamp(), **extra}


@dataclass(frozen=True, kw_only=True)
class MembersPage(CommunityPage):
    """``/in/{name}/about/{moderators,contributors,banned}``."""

    template: ClassVar[str] = 'members.html'
    cacheable: ClassVar[bool] = False

    which: str
    moderators: tuple[Moderator, ...] = ()
    members: tuple[Member, ...] = ()
    invites: tuple[Member, ...] = ()
    # The viewer has a pending invite here.
    invited: bool = False
    can_edit: bool = False
    errors: tuple[FieldError, ...] = ()

    permission_names: ClassVar[tuple[str, ...]] = tuple(PERMISSION_NAMES)
    lists: ClassVar[dict[str, str]] = LISTS

    @property
    def title(self) -> str:
        return f'{self.community.name}: {LISTS[self.which]}'

    @staticmethod
    def permissions(moderator: Moderator | Member) -> list[str]:
        held = moderator.permissions
        return permissions_text(held) if held is not None else []

    def to_json(self) -> JSON:
        """r2's ``UserList``."""
        if self.which == 'moderators':
            children: list[JSON] = [
                _user(m, m.added, mod_permissions=list(self.permissions(m)))
                for m in self.moderators
            ]
        else:
            children = [
                _user(m, m.created, note=m.note)
                if self.which in {'banned', 'wikibanned'}
                else _user(m, m.created, expires=m.expires.timestamp())
                if self.which == 'muted' and m.expires
                else _user(m, m.created)
                for m in self.members
            ]
        return {'kind': 'UserList', 'data': {'children': children}}


@dataclass(frozen=True, kw_only=True)
class ModlogPage(CommunityPage):
    """``/in/{name}/about/log``."""

    template: ClassVar[str] = 'modlog.html'
    cacheable: ClassVar[bool] = False

    actions: tuple[ModAction, ...]
    after: str | None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return f'{self.community.name}: moderation log'

    def to_json(self) -> JSON:
        children: list[JSON] = [
            {
                'kind': 'modaction',
                'data': {
                    'id': f'ModAction_{action.id}',
                    'mod': action.mod_name,
                    'action': action.action,
                    'target_fullname': str(action.target) if action.target else None,
                    'details': action.details,
                    'created_utc': action.created.timestamp(),
                    'community': action.community_name,
                    'community_id36': Fullname(Kind.COMMUNITY, action.community_id).id36,
                },
            }
            for action in self.actions
        ]
        return listing_json(children, after=self.after, before=None, modhash='')


@dataclass(frozen=True, slots=True)
class QueueItem:
    """A link or comment awaiting a moderator, with what the moderator needs to decide."""

    link: Link
    comment: Comment | None
    reasons: tuple[str, ...]

    @property
    def fullname(self) -> Fullname:
        return self.comment.fullname if self.comment else self.link.fullname

    @property
    def thing(self) -> Link | Comment:
        return self.comment or self.link

    @property
    def permalink(self) -> str:
        if self.comment is None:
            return permalink(self.link)
        return f'{permalink(self.link)}{self.comment.fullname.id36}/'

    def to_json(self) -> JSON:
        if self.comment is None:
            data = link_data(self.link, likes=None, moderator=True, reasons=self.reasons)
            return {'kind': 't3', 'data': data}
        c = self.comment
        return {
            'kind': 't1',
            'data': {
                'id': c.fullname.id36,
                'name': str(c.fullname),
                'link_id': str(self.link.fullname),
                'link_title': self.link.title,
                'parent_id': str(c.parent_fullname),
                'author': c.author_name,
                'body': c.body_md,
                'body_html': c.body_html,
                'created_utc': c.created.timestamp(),
                'community': c.community_name,
                'removed': c.removed,
                'spam': c.spam,
                'approved': c.approved_by is not None,
                'num_reports': c.reported,
                'report_reasons': list(self.reasons),
                'permalink': self.permalink,
            },
        }


@dataclass(frozen=True, kw_only=True)
class QueuePage(CommunityPage):
    """``/in/{name}/about/{modqueue,reports,spam,unmoderated}``."""

    template: ClassVar[str] = 'queue.html'
    cacheable: ClassVar[bool] = False

    queue: Queue
    items: tuple[QueueItem, ...]
    after: str | None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    queues: ClassVar[dict[Queue, str]] = QUEUE_TITLES

    @property
    def title(self) -> str:
        return f'{self.community.name}: {self.queue.value}'

    def to_json(self) -> JSON:
        children: list[JSON] = [item.to_json() for item in self.items]
        return listing_json(children, after=self.after, before=None, modhash='')
