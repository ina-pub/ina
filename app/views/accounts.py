from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode

from app.ids import Fullname, Kind
from app.views.base import Page, View
from app.views.links import LinkRow, permalink
from app.views.listing import listing_json, prefixed

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Account, Comment, Link
    from app.services.flair import Shown


def account_data(account: Account, *, own: bool, modhash: str = '') -> dict[str, JSON]:
    """The ``data`` of a ``t2``. The account's own view adds private fields, as on r2."""
    if account.suspended:
        return {'name': account.name, 'is_suspended': True}
    created = account.created.timestamp()
    data: dict[str, JSON] = {
        'id': account.fullname.id36,
        'name': account.name,
        'created': created,
        'created_utc': created,
        'link_karma': account.link_karma,
        'comment_karma': account.comment_karma,
        'has_verified_email': account.email_verified,
        'is_mod': False,
        'hide_from_robots': account.prefs.hide_from_robots,
    }
    if own:
        data.update(
            modhash=modhash,
            inbox_count=account.inbox_count,
            has_mail=account.inbox_count > 0,
            has_mod_mail=False,
            over_18=account.prefs.over_18,
            is_suspended=account.in_timeout,
        )
    return data


@dataclass(frozen=True, kw_only=True)
class Identity(View):
    """``/api/v1/me``: the signed-in account's data, unwrapped."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    account: Account
    modhash: str

    def to_json(self) -> JSON:
        return account_data(self.account, own=True, modhash=self.modhash)


@dataclass(frozen=True, kw_only=True)
class Me(Identity):
    """``/api/me``: the same data wrapped as a ``t2``."""

    def to_json(self) -> JSON:
        return {'kind': 't2', 'data': super().to_json()}


@dataclass(frozen=True, slots=True)
class ProfileComment:
    """A comment on a profile, with the link it was made on, as r2 showed it."""

    comment: Comment
    link: Link
    likes: bool | None
    author_flair: Shown | None = None
    saved: bool = False

    @property
    def link_permalink(self) -> str:
        return permalink(self.link)

    @property
    def permalink(self) -> str:
        return f'{permalink(self.link)}{self.comment.fullname.id36}/'

    @property
    def vote_class(self) -> str:
        return {True: 'likes', False: 'dislikes', None: 'unvoted'}[self.likes]

    def to_json(self) -> JSON:
        """r2's t1 on a profile: the comment, and the title, author and URL of its link."""
        c, link = self.comment, self.link
        created = c.created.timestamp()
        flair = self.author_flair
        return {
            'kind': 't1',
            'data': {
                'id': c.fullname.id36,
                'name': str(c.fullname),
                'parent_id': str(c.parent_fullname),
                'link_id': str(link.fullname),
                'link_title': link.title,
                'link_author': link.author_name,
                'link_url': permalink(link) if link.is_self else link.url,
                'link_permalink': permalink(link),
                'author': c.author_name,
                'author_fullname': str(Fullname(Kind.ACCOUNT, c.author_id)),
                'author_flair_text': flair.text or None if flair else None,
                'author_flair_css_class': flair.css_class or None if flair else None,
                'body': c.body_md,
                'body_html': c.body_html,
                'score': c.score,
                'ups': c.score,
                'downs': 0,
                'likes': self.likes,
                'created': created,
                'created_utc': created,
                'edited': c.edited.timestamp() if c.edited else False,
                'community': c.community_name,
                'community_id': str(Fullname(Kind.COMMUNITY, c.community_id)),
                'community_name_prefixed': prefixed(c.community_name),
                'distinguished': c.distinguished,
                'permalink': self.permalink,
                'saved': self.saved,
            },
        }


type ProfileItem = LinkRow | ProfileComment


@dataclass(frozen=True, kw_only=True)
class ProfilePage(Page):
    """``/user/{name}`` and its tabs: what the account posted, and what it voted on."""

    template: ClassVar[str] = 'user.html'
    has_json: ClassVar[bool] = True

    account: Account
    tab: str = 'overview'
    sort: str = 'new'
    time_filter: str = 'all'
    items: tuple[ProfileItem, ...] = ()
    # The last thing's fullname, when another page follows.
    after: str | None = None
    count: int = 0
    # The vote tabs show: the account's own profile, or its votes are public.
    shows_votes: bool = False
    # The viewer counts this account as a friend.
    is_friend: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    sorts: ClassVar[tuple[str, ...]] = ('new', 'hot', 'top', 'controversial')
    time_filters: ClassVar[tuple[tuple[str, str], ...]] = (
        ('hour', 'past hour'),
        ('day', 'past 24 hours'),
        ('week', 'past week'),
        ('month', 'past month'),
        ('year', 'past year'),
        ('all', 'all time'),
    )

    @property
    def title(self) -> str:
        return f'{self.tab} for {self.account.name}'

    @property
    def own(self) -> bool:
        return self.viewer is not None and self.viewer.name == self.account.name

    @property
    def tabs(self) -> list[str]:
        """r2's profile tabs that this viewer may open."""
        shown = ['overview', 'comments', 'submitted']
        if self.shows_votes:
            shown += ['upvoted', 'downvoted']
        if self.own:
            shown += ['hidden', 'saved']
        return shown

    def tab_path(self, tab: str) -> str:
        base = f'/user/{self.account.name}/'
        return base if tab == 'overview' else f'{base}{tab}/'

    @property
    def timed(self) -> bool:
        return self.sort in {'top', 'controversial'}

    def query(self, **params: str | int) -> str:
        values: dict[str, str | int] = {}
        if self.sort != 'new':
            values['sort'] = self.sort
        if self.timed and self.time_filter != 'all':
            values['t'] = self.time_filter
        values.update(params)
        return '?' + urlencode(values) if values else '?'

    @property
    def next_query(self) -> str | None:
        if self.after is None:
            return None
        return self.query(count=self.count + len(self.items), after=self.after)

    def to_json(self) -> JSON:
        modhash = self.viewer.modhash if self.viewer else ''
        children: list[JSON] = [
            {'kind': 't3', 'data': item.data()} if isinstance(item, LinkRow) else item.to_json()
            for item in self.items
        ]
        return listing_json(children, after=self.after, before=None, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class ProfileAbout(ProfilePage):
    """``/user/{name}/about``: the same page, or the account as a ``t2``."""

    def to_json(self) -> JSON:
        modhash = self.viewer.modhash if self.viewer else ''
        return {'kind': 't2', 'data': account_data(self.account, own=self.own, modhash=modhash)}


@dataclass(frozen=True, kw_only=True)
class LoginPage(Page):
    """``/login`` and ``/register``: both forms side by side, as on r2."""

    template: ClassVar[str] = 'login.html'

    dest: str = '/'
    user: str = ''
    reg_user: str = ''
    reg_email: str = ''
    login_errors: tuple[FieldError, ...] = ()
    reg_errors: tuple[FieldError, ...] = ()

    @property
    def title(self) -> str:
        return 'log in to ina'


@dataclass(frozen=True, kw_only=True)
class PasswordPage(Page):
    """``/password``: ask for a reset email."""

    template: ClassVar[str] = 'password.html'

    name: str = ''
    errors: tuple[FieldError, ...] = ()
    expired: bool = False
    sent: bool = False

    @property
    def title(self) -> str:
        return 'reset your password'


@dataclass(frozen=True, kw_only=True)
class ResetPasswordPage(Page):
    """``/resetpassword/{key}``: choose a new password."""

    template: ClassVar[str] = 'resetpassword.html'
    cacheable: ClassVar[bool] = False

    key: str
    errors: tuple[FieldError, ...] = ()

    @property
    def title(self) -> str:
        return 'choose a new password'
