from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode

from litestar.status_codes import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from app.services.communities import LINK_TYPES, SPAM_LEVELS, TYPES, WIKI_MODES
from app.services.prefs import COMMENT_SORTS
from app.views.base import Page
from app.views.listing import listing_json, prefixed
from app.views.prefs import COMMENT_SORT_LABELS

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Community, Moderator
    from app.services.communities import CommunityForm
    from app.views.flair import FlairBox


def community_data(
    community: Community, *, is_subscriber: bool, is_moderator: bool
) -> dict[str, JSON]:
    """The ``data`` of a ``t5``, with r2's field names."""
    settings = community.settings
    created = community.created.timestamp()
    return {
        'id': community.fullname.id36,
        'name': str(community.fullname),
        'display_name': community.name,
        'display_name_prefixed': prefixed(community.name),
        'title': community.title,
        'url': community.path,
        'created': created,
        'created_utc': created,
        'subscribers': community.subscriber_count,
        'community_type': community.type,
        'over18': community.nsfw,
        'public_description': community.description_md,
        'public_description_html': community.description_html or None,
        'description': community.sidebar_md,
        'description_html': community.sidebar_html or None,
        'submit_text': settings.submit_text,
        'submit_text_html': settings.submit_text_html or None,
        'submit_link_label': settings.submit_link_label or None,
        'submit_text_label': settings.submit_text_label or None,
        'submission_type': settings.link_type,
        'lang': settings.lang,
        'quarantine': settings.quarantine,
        'show_media': settings.show_media,
        'show_media_preview': settings.show_media_preview,
        'public_traffic': settings.public_traffic,
        'collapse_deleted_comments': settings.collapse_deleted_comments,
        'comment_score_hide_mins': settings.comment_score_hide_mins,
        'suggested_comment_sort': settings.suggested_comment_sort,
        'wiki_enabled': settings.wikimode != 'disabled',
        'icon_img': settings.icon_img,
        'banner_img': settings.banner_img,
        'header_size': list(settings.header_size) if settings.header_size else None,
        'user_is_subscriber': is_subscriber,
        'user_is_moderator': is_moderator,
    }


# r2's words when search was down.
UNAVAILABLE = "search isn't answering right now; try again in a minute"


@dataclass(frozen=True, slots=True, kw_only=True)
class Sidebar:
    """What the community sidebar shows about the community and the viewer's place in it."""

    moderators: tuple[Moderator, ...]
    is_subscriber: bool
    is_moderator: bool
    can_configure: bool
    # May act on links and comments: remove, approve, sticky, lock.
    can_moderate: bool = False
    # May ban and approve submitters.
    can_manage_access: bool = False
    # May ban from the wiki and approve wiki contributors.
    can_manage_wiki: bool = False
    can_manage_mail: bool = False
    # Moderators, or anyone when the community makes it public.
    can_see_traffic: bool = False
    # May set anyone's flair and keep the flair templates.
    can_manage_flair: bool = False
    # The wiki tab shows: the wiki is open, or the viewer moderates it.
    wiki_enabled: bool = False
    # "show my flair on this community", on the community's own listings.
    flair_box: FlairBox | None = None


@dataclass(frozen=True, kw_only=True)
class CommunityPage(Page):
    """A page inside a community, with its sidebar."""

    template: ClassVar[str] = 'community.html'
    has_json: ClassVar[bool] = True

    community: Community
    sidebar: Sidebar

    @property
    def title(self) -> str:
        return self.community.title or self.community.name

    def to_json(self) -> JSON:
        modhash = self.viewer.modhash if self.viewer else ''
        return listing_json([], after=None, before=None, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class CommunityAbout(CommunityPage):
    """``/in/{name}/about``: the same page, or the community as a ``t5``."""

    def to_json(self) -> JSON:
        data = community_data(
            self.community,
            is_subscriber=self.sidebar.is_subscriber,
            is_moderator=self.sidebar.is_moderator,
        )
        return {'kind': 't5', 'data': data}


@dataclass(frozen=True, slots=True)
class CommunityRow:
    community: Community
    is_subscriber: bool


@dataclass(frozen=True, kw_only=True)
class CommunitiesPage(Page):
    """``/ins`` and its tabs: popular, new, the viewer's own, and search results."""

    template: ClassVar[str] = 'communities.html'
    has_json: ClassVar[bool] = True

    where: str
    rows: tuple[CommunityRow, ...]
    after: str | None
    # /ins/search only: the query, how many results came before this page, and whether
    # search was down.
    query: str = ''
    offset: int = 0
    unavailable: bool = False

    unavailable_message: ClassVar[str] = UNAVAILABLE

    tabs: ClassVar[tuple[tuple[str, str], ...]] = (('popular', 'popular'), ('new', 'new'))
    mine_tabs: ClassVar[tuple[tuple[str, str], ...]] = (
        ('mine/subscriber', 'joined'),
        ('mine/moderator', 'moderating'),
    )

    @property
    def title(self) -> str:
        return 'find communities' if self.where == 'search' else 'communities'

    @property
    def status_code(self) -> int:
        return HTTP_503_SERVICE_UNAVAILABLE if self.unavailable else HTTP_200_OK

    @property
    def next_query(self) -> str | None:
        if self.after is None:
            return None
        if self.where != 'search':
            return '?' + urlencode({'after': self.after})
        count = self.offset + len(self.rows)
        return '?' + urlencode({'q': self.query, 'count': count, 'after': self.after})

    def to_json(self) -> JSON:
        children: list[JSON] = [
            {
                'kind': 't5',
                'data': community_data(
                    row.community, is_subscriber=row.is_subscriber, is_moderator=False
                ),
            }
            for row in self.rows
        ]
        modhash = self.viewer.modhash if self.viewer else ''
        return listing_json(children, after=self.after, before=None, modhash=modhash)


@dataclass(frozen=True, kw_only=True)
class CommunityEditPage(Page):
    """``/ins/create`` and ``/in/{name}/about/edit``: the same form."""

    template: ClassVar[str] = 'community_edit.html'
    has_json: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    community: Community | None
    form: CommunityForm
    errors: tuple[FieldError, ...] = ()
    done: bool = False

    types: ClassVar[tuple[str, ...]] = TYPES
    link_types: ClassVar[tuple[str, ...]] = LINK_TYPES
    spam_levels: ClassVar[tuple[str, ...]] = SPAM_LEVELS
    wiki_modes: ClassVar[tuple[str, ...]] = WIKI_MODES
    comment_sorts: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        (sort, COMMENT_SORT_LABELS[sort]) for sort in COMMENT_SORTS
    )

    @property
    def title(self) -> str:
        if self.community is None:
            return 'start a community'
        return f'{self.community.name}: settings'

    def to_json(self) -> JSON:
        """``about/edit.json``: the current settings, shaped as r2's settings JSON."""
        form = self.form
        return {
            'kind': 'community_settings',
            'data': {
                'community_id': str(self.community.fullname) if self.community else None,
                'title': form.title,
                'public_description': form.public_description,
                'description': form.description,
                'submit_text': form.submit_text,
                'submit_link_label': form.submit_link_label,
                'submit_text_label': form.submit_text_label,
                'community_type': form.type,
                'content_options': form.link_type,
                'over_18': bool(form.over_18),
                'show_media': bool(form.show_media),
                'show_media_preview': bool(form.show_media_preview),
                'allow_top': bool(form.allow_top),
                'collapse_deleted_comments': bool(form.collapse_deleted_comments),
                'exclude_banned_modqueue': bool(form.exclude_banned_modqueue),
                'public_traffic': bool(form.public_traffic),
                'spam_links': form.spam_links,
                'spam_selfposts': form.spam_selfposts,
                'spam_comments': form.spam_comments,
                'comment_score_hide_mins': int(form.comment_score_hide_mins or 0),
                'suggested_comment_sort': form.suggested_comment_sort or None,
                'wikimode': form.wikimode,
                'wiki_edit_karma': int(form.wiki_edit_karma or 0),
                'wiki_edit_age': int(form.wiki_edit_age or 0),
            },
        }
