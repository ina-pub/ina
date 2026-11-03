"""Wiki pages: a page, its editor, its history and settings, and the list of pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from litestar.status_codes import HTTP_200_OK, HTTP_404_NOT_FOUND

from app.views.communities import CommunityPage
from app.views.listing import listing_json

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Member, WikiRevision

# r2 named revisions this way in its listings' after and before.
REVISION_PREFIX = 'WikiRevision_'
PERMLEVELS = (
    (0, "as the wiki's settings say"),
    (1, "only this page's approved editors may edit"),
    (2, 'only innkeepers may see or edit'),
)


def user_json(name: str | None) -> JSON:
    return {'kind': 't2', 'data': {'name': name}} if name else None


def revision_json(revision: WikiRevision) -> JSON:
    """r2's wiki revision object: not a thing, so no ``kind``."""
    return {
        'id': str(revision.id),
        'page': revision.page_name,
        'author': user_json(revision.author_name),
        'reason': revision.reason or None,
        'timestamp': revision.created.timestamp(),
        'revision_hidden': revision.hidden,
    }


@dataclass(frozen=True, kw_only=True)
class WikiBase(CommunityPage):
    """r2's wikibasepage: a title, the page's actions, and the content below."""

    template: ClassVar[str] = 'wiki_page.html'
    has_json: ClassVar[bool] = True

    page: str | None = None
    # Which action is current: the page itself, edit, revisions, settings or pages.
    action: str = 'view'
    may_revise: bool = False
    is_mod: bool = False
    # A line above the page: which revision it is, or what a config page does.
    alert: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return (
            f'{self.page} - {self.community.name}' if self.page else f'wiki - {self.community.name}'
        )

    @property
    def base_url(self) -> str:
        return f'{self.community.path}wiki'

    @property
    def actions(self) -> list[tuple[str, str, str]]:
        """(href, label, action), as r2's pageactions."""
        if not self.page:
            return []
        base, page = self.base_url, self.page
        found = [(f'{base}/{page}', 'view', 'view')]
        if self.may_revise:
            found.append((f'{base}/edit/{page}', 'edit', 'edit'))
        found.append((f'{base}/revisions/{page}', 'history', 'revisions'))
        if self.is_mod and self.may_revise:
            found.append((f'{base}/settings/{page}', 'settings', 'settings'))
        return found


@dataclass(frozen=True, kw_only=True)
class WikiPageView(WikiBase):
    """``/in/{name}/wiki/{page}``, also as it was at a revision (``v``) or a comparison (``v2``)."""

    content_md: str = ''
    content_html: str = ''
    # Shown as source, not rendered: Bouncer's rules and the stylesheet.
    code: bool = False
    revision_id: str | None = None
    revised_by: str | None = None
    revised_at: datetime | None = None
    # A comparison table between two revisions, built by difflib from escaped text.
    diff: str | None = None
    # The page does not exist; ``may_revise`` says whether the viewer may create it.
    missing: bool = False

    @property
    def status_code(self) -> int:
        return HTTP_404_NOT_FOUND if self.missing else HTTP_200_OK

    def to_json(self) -> JSON:
        return {
            'kind': 'wikipage',
            'data': {
                'content_md': self.content_md,
                'content_html': self.content_html,
                'may_revise': self.may_revise,
                'revision_id': self.revision_id,
                'revision_by': user_json(self.revised_by),
                'revision_date': self.revised_at.timestamp() if self.revised_at else None,
            },
        }


@dataclass(frozen=True, kw_only=True)
class WikiEditPage(WikiBase):
    """``/in/{name}/wiki/edit/{page}``, and the same form again after an error."""

    template: ClassVar[str] = 'wiki_edit.html'
    has_json: ClassVar[bool] = False
    cacheable: ClassVar[bool] = False

    action: str = 'edit'
    content: str = ''
    reason: str = ''
    # The revision the edit starts from, so a later edit by someone else is noticed.
    previous: str = ''
    # After a conflict: what the page says now.
    current: str | None = None
    errors: tuple[FieldError, ...] = ()


@dataclass(frozen=True, kw_only=True)
class WikiRevisionsPage(WikiBase):
    """``/in/{name}/wiki/revisions`` and ``/in/{name}/wiki/revisions/{page}``."""

    template: ClassVar[str] = 'wiki_revisions.html'

    action: str = 'revisions'
    revisions: tuple[WikiRevision, ...] = ()
    after: str | None = None

    @property
    def title(self) -> str:
        return f'revisions - {self.page or self.community.name}'

    @property
    def extended(self) -> bool:
        """r2 showed hide and revert to moderators on one page's history."""
        return self.page is not None and self.is_mod

    def to_json(self) -> JSON:
        children: list[JSON] = [revision_json(revision) for revision in self.revisions]
        return listing_json(children, after=self.after, before=None, modhash='')


@dataclass(frozen=True, kw_only=True)
class WikiPagesPage(WikiBase):
    """``/in/{name}/wiki/pages``."""

    template: ClassVar[str] = 'wiki_pages.html'

    action: str = 'pages'
    names: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return f'pages - {self.community.name}'

    def to_json(self) -> JSON:
        return {'kind': 'wikipagelisting', 'data': list(self.names)}


@dataclass(frozen=True, kw_only=True)
class WikiSettingsPage(WikiBase):
    """``/in/{name}/wiki/settings/{page}``: who may edit it, and whether it is listed."""

    template: ClassVar[str] = 'wiki_settings.html'
    cacheable: ClassVar[bool] = False

    action: str = 'settings'
    permlevel: int = 0
    listed: bool = True
    editors: tuple[Member, ...] = ()
    # Config pages' settings are fixed; restricted pages take no editors.
    show_settings: bool = True
    show_editors: bool = True
    errors: tuple[FieldError, ...] = ()

    permlevels: ClassVar[tuple[tuple[int, str], ...]] = PERMLEVELS

    def to_json(self) -> JSON:
        return {
            'kind': 'wikipagesettings',
            'data': {
                'permlevel': self.permlevel,
                'listed': self.listed,
                'editors': [user_json(editor.name) for editor in self.editors],
            },
        }
