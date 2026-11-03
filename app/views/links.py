from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode, urlsplit

from app.ids import Fullname, Kind
from app.repos.base import Sort
from app.services.embeds import video_source
from app.services.links import MAX_TITLE
from app.services.listings import TIME_FILTERS
from app.text.urls import slug
from app.views.base import Page
from app.views.comments import DELETED
from app.views.communities import CommunityPage
from app.views.listing import listing_json, prefixed, upvote_ratio

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Community, FlairTemplate, Link, Multi
    from app.services.embeds import Player
    from app.services.flair import Shown
    from app.services.links import SubmitForm
    from app.views.comments import Thread


def permalink(link: Link) -> str:
    return f'/in/{link.community_name}/comments/{link.fullname.id36}/{slug(link.title)}/'


@dataclass(frozen=True, slots=True)
class Thumb:
    """The thumbnail spot: r2's self, default or nsfw picture, or the link's own image."""

    kind: str
    url: str = ''
    width: int = 0
    height: int = 0


def link_data(
    link: Link,
    *,
    likes: bool | None,
    moderator: bool = False,
    reasons: tuple[str, ...] = (),
    author_flair: Shown | None = None,
    thumbnail: Thumb | None = None,
    saved: bool = False,
    hidden: bool = False,
    player: Player | None = None,
) -> dict[str, JSON]:
    """The ``data`` of a ``t3``, with r2's field names. r2 reported downs as 0, so does this.

    Moderators also see reports and removal state; everyone else gets nulls there, as on r2.
    Every player is served over https, so the ``secure_`` fields repeat the others, as r2's did
    for https embeds.
    """
    media: JSON = player.oembed() if player else None
    media_embed: JSON = player.media_embed() if player else {}
    created = link.created.timestamp()
    url = permalink(link) if link.is_self else link.url
    selftext = DELETED if link.deleted and link.is_self else link.selftext_md
    selftext_html = f'<p>{DELETED}</p>' if link.deleted and link.is_self else link.selftext_html
    return {
        'id': link.fullname.id36,
        'name': str(link.fullname),
        'title': link.title,
        'url': url,
        'permalink': permalink(link),
        'domain': link.domain,
        'is_self': link.is_self,
        'selftext': selftext,
        'selftext_html': selftext_html or None,
        'author': DELETED if link.deleted else link.author_name,
        'author_fullname': None if link.deleted else str(Fullname(Kind.ACCOUNT, link.author_id)),
        'author_flair_text': author_flair.text or None if author_flair else None,
        'author_flair_css_class': author_flair.css_class or None if author_flair else None,
        'community': link.community_name,
        'community_id': str(Fullname(Kind.COMMUNITY, link.community_id)),
        'community_name_prefixed': prefixed(link.community_name),
        'created': created,
        'created_utc': created,
        'edited': link.edited.timestamp() if link.edited else False,
        'score': link.score,
        'ups': link.score,
        'downs': 0,
        'upvote_ratio': upvote_ratio(link.upvotes, link.downvotes),
        'likes': likes,
        'num_comments': link.comment_count,
        'over_18': link.nsfw,
        'spoiler': link.spoiler,
        'stickied': link.stickied,
        'locked': link.locked,
        'contest_mode': link.contest_mode,
        'suggested_sort': link.suggested_sort,
        'link_flair_text': link.flair_text,
        'link_flair_css_class': link.flair_css,
        'thumbnail': (thumbnail.url or thumbnail.kind)
        if thumbnail
        else ('self' if link.is_self else 'default'),
        'thumbnail_width': thumbnail.width or None if thumbnail else None,
        'thumbnail_height': thumbnail.height or None if thumbnail else None,
        'hide_score': False,
        'distinguished': link.distinguished,
        'num_reports': link.reported if moderator else None,
        'report_reasons': list(reasons) if moderator else None,
        'approved': link.approved_by is not None if moderator else None,
        'removed': link.removed if moderator else None,
        'spam': link.spam if moderator else None,
        'saved': saved,
        'hidden': hidden,
        'clicked': False,
        'visited': False,
        'archived': False,
        'media': media,
        'media_embed': media_embed,
        'secure_media': media,
        'secure_media_embed': media_embed,
    }


# Links that are themselves images open in an expando, as r2's media previews did.
IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


@dataclass(frozen=True, slots=True)
class LinkRow:
    link: Link
    rank: int | None
    # True for an upvote by the viewer, False for a downvote, None for neither.
    likes: bool | None
    author_flair: Shown | None = None
    link_flair: Shown | None = None
    thumb: Thumb | None = None
    saved: bool = False
    hidden: bool = False
    # The author is the viewer's friend.
    friend: bool = False
    # The site player the link opens in, if it is on one of the sites in app/services/embeds.py.
    player: Player | None = None
    # The viewer said they are over eighteen, so NSFW links open in place too.
    over_18: bool = False

    def data(self) -> dict[str, JSON]:
        """The link as a ``t3``'s data, as this viewer sees it."""
        return link_data(
            self.link,
            likes=self.likes,
            author_flair=self.author_flair,
            thumbnail=self.thumbnail,
            saved=self.saved,
            hidden=self.hidden,
            player=self.player,
        )

    @property
    def thumbnail(self) -> Thumb:
        return self.thumb or Thumb('self' if self.link.is_self else 'default')

    @property
    def flair_classes(self) -> str:
        """r2's linkflair classes on the thing, so community styles can colour flaired links."""
        if self.link_flair is None or not self.link_flair.css_class:
            return ''
        return ' linkflair ' + ' '.join(
            f'linkflair-{name}' for name in self.link_flair.css_class.split()
        )

    @property
    def permalink(self) -> str:
        return permalink(self.link)

    @property
    def expando(self) -> str | None:
        """The expando button's kind, or None for no button.

        ``selftext`` for text, ``image`` for an image, ``video`` for a video file, or the kind of
        the link's site player: ``video``, ``audio``, ``gif`` or ``post``.
        """
        link = self.link
        if link.deleted or link.removed or link.spam:
            return None
        if link.is_self:
            return 'selftext' if link.selftext_html else None
        # NSFW media opens in place only for viewers who said they are over eighteen.
        if link.nsfw and not self.over_18:
            return None
        if self.player:
            return self.player.provider.kind
        if self.video_src:
            return 'video'
        # Images over https only, as the page's CSP allows.
        image = link.url.startswith('https://') and urlsplit(link.url).path.lower().endswith(
            IMAGE_SUFFIXES
        )
        return 'image' if image else None

    @property
    def video_src(self) -> str | None:
        """What a <video> plays for a link to a video file; None for anything else."""
        return None if self.link.is_self or self.player else video_source(self.link.url)

    @property
    def author(self) -> str | None:
        """Who submitted it; None once they deleted it."""
        return None if self.link.deleted else self.link.author_name

    @property
    def href(self) -> str:
        return self.permalink if self.link.is_self else self.link.url

    @property
    def vote_class(self) -> str:
        return {True: 'likes', False: 'dislikes', None: 'unvoted'}[self.likes]


@dataclass(frozen=True, slots=True, kw_only=True)
class Listing:
    """A page of links with the tabs and navigation around it."""

    rows: tuple[LinkRow, ...]
    sort: Sort
    time_filter: str
    # Fullnames for the next and previous pages, when there are any.
    after: str | None
    before: str | None
    count: int
    # Where the sort tabs point, e.g. '/in/pics/'.
    base_path: str

    sorts: ClassVar[tuple[Sort, ...]] = (
        Sort.HOT,
        Sort.NEW,
        Sort.RISING,
        Sort.CONTROVERSIAL,
        Sort.TOP,
    )
    time_filters: ClassVar[tuple[tuple[str, str], ...]] = (
        ('hour', 'past hour'),
        ('day', 'past 24 hours'),
        ('week', 'past week'),
        ('month', 'past month'),
        ('year', 'past year'),
        ('all', 'all time'),
    )

    @property
    def windowed(self) -> bool:
        return self.sort in {Sort.TOP, Sort.CONTROVERSIAL} and self.time_filter in TIME_FILTERS

    def sort_path(self, sort: Sort) -> str:
        return self.base_path if sort is Sort.HOT else f'{self.base_path}{sort}/'

    def _query(self, **params: str | int) -> str:
        if self.windowed:
            params = {'t': self.time_filter, **params}
        return f'?{urlencode(params)}'

    @property
    def next_query(self) -> str | None:
        if self.after is None:
            return None
        return self._query(count=self.count + len(self.rows), after=self.after)

    @property
    def prev_query(self) -> str | None:
        if self.before is None:
            return None
        return self._query(count=self.count + 1, before=self.before)

    def to_json(self, modhash: str) -> JSON:
        children: list[JSON] = [{'kind': 't3', 'data': row.data()} for row in self.rows]
        return listing_json(children, after=self.after, before=self.before, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class FrontPage(Page):
    """``/`` and ``/in/all``: links from many communities."""

    template: ClassVar[str] = 'front.html'
    has_json: ClassVar[bool] = True

    listing: Listing
    # 'front', 'all', 'domain' or 'friends'.
    where: str
    # The viewer's multis, for the sidebar.
    multis: tuple[Multi, ...] = ()
    # For /domain/{domain}: the domain listed.
    domain: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        if self.where == 'all':
            return 'all ins'
        if self.domain is not None:
            return self.domain
        if self.where == 'friends':
            return 'friends'
        return 'ina'

    def to_json(self) -> JSON:
        return self.listing.to_json(self.viewer.modhash if self.viewer else '')


@dataclass(frozen=True, kw_only=True)
class CommunityListingPage(CommunityPage):
    """``/in/{name}`` and its sorts."""

    template: ClassVar[str] = 'community.html'

    listing: Listing
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> JSON:
        return self.listing.to_json(self.viewer.modhash if self.viewer else '')


@dataclass(frozen=True, kw_only=True)
class LinkPage(CommunityPage):
    """``/in/{name}/comments/{id36}/{slug}``: the link and its comments.

    With a comment id after the slug it is that comment's permalink: its thread alone, with
    ``context`` parents above it.
    """

    template: ClassVar[str] = 'link.html'

    row: LinkRow
    thread: Thread
    can_comment: bool
    locked: bool
    archived: bool
    # A reply that failed, shown again with its errors when there is no JS.
    reply_parent: str | None = None
    reply_text: str = ''
    reply_errors: tuple[FieldError, ...] = ()
    # The author's self post or comment being edited, without JS or with errors.
    edit_thing: str | None = None
    edit_text: str = ''
    edit_errors: tuple[FieldError, ...] = ()
    # The viewer may flair the link: its moderators, or its submitter where that is allowed.
    can_flair: bool = False
    link_flair_choices: tuple[FlairTemplate, ...] = ()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return f'{self.row.link.title} : {self.community.name}'

    def to_json(self) -> JSON:
        modhash = self.viewer.modhash if self.viewer else ''
        link: list[JSON] = [{'kind': 't3', 'data': self.row.data()}]
        return [
            listing_json(link, after=None, before=None, modhash=modhash),
            self.thread.to_json(modhash),
        ]


@dataclass(frozen=True, kw_only=True)
class SubmitPage(Page):
    """``/submit`` and ``/in/{name}/submit``."""

    template: ClassVar[str] = 'submit.html'
    cacheable: ClassVar[bool] = False

    community: Community | None
    form: SubmitForm
    errors: tuple[FieldError, ...] = ()

    max_title: ClassVar[int] = MAX_TITLE

    @property
    def title(self) -> str:
        return f'submit to {self.community.name}' if self.community else 'submit to ina'


@dataclass(frozen=True, kw_only=True)
class DuplicatesPage(CommunityPage):
    """``/in/{name}/duplicates/{id36}``: a link, then other discussions of its URL."""

    template: ClassVar[str] = 'duplicates.html'

    row: LinkRow
    others: tuple[LinkRow, ...]
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return f'{self.row.link.title} : {self.community.name}'

    def to_json(self) -> JSON:
        """As r2's: the link's listing, then the others'."""
        modhash = self.viewer.modhash if self.viewer else ''
        link: list[JSON] = [{'kind': 't3', 'data': self.row.data()}]
        others: list[JSON] = [{'kind': 't3', 'data': row.data()} for row in self.others]
        return [
            listing_json(link, after=None, before=None, modhash=modhash),
            listing_json(others, after=None, before=None, modhash=modhash),
        ]
