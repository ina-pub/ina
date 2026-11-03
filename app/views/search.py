"""Search results: ``/search``, ``/in/{name}/search`` and ``/ins/search``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlencode

from litestar.status_codes import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from app.services.search import DEFAULT_SORT, DEFAULT_TIME_FILTER, SORTS
from app.views.base import Page, View
from app.views.communities import UNAVAILABLE, community_data
from app.views.links import Listing
from app.views.listing import listing_json

if TYPE_CHECKING:
    from app.jsontypes import JSON
    from app.repos.base import Community
    from app.views.communities import CommunityRow, Sidebar
    from app.views.links import LinkRow


@dataclass(frozen=True, kw_only=True)
class SearchPage(Page):
    """Links matching a query, with up to three communities above the first page."""

    template: ClassVar[str] = 'search.html'
    has_json: ClassVar[bool] = True

    query: str
    sort: str = DEFAULT_SORT
    time_filter: str = DEFAULT_TIME_FILTER
    # The community the search started from, and whether it searched only there.
    community: Community | None = None
    sidebar: Sidebar | None = None
    restrict_community: bool = False
    rows: tuple[LinkRow, ...] = ()
    community_rows: tuple[CommunityRow, ...] = ()
    # Which results the request asked for: 'link', 'community' or both.
    types: frozenset[str] = frozenset({'link'})
    offset: int = 0
    more: bool = False
    # What was searched instead of a query that could not be read.
    cleaned: str | None = None
    unavailable: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    sorts: ClassVar[tuple[str, ...]] = tuple(SORTS)
    time_filters: ClassVar[tuple[tuple[str, str], ...]] = Listing.time_filters
    unavailable_message: ClassVar[str] = UNAVAILABLE

    @property
    def title(self) -> str:
        where = f'{self.community.name}: ' if self.community and self.restrict_community else ''
        return f'{where}search results'

    @property
    def status_code(self) -> int:
        return HTTP_503_SERVICE_UNAVAILABLE if self.unavailable else HTTP_200_OK

    @property
    def action(self) -> str:
        return f'{self.community.path}search' if self.community else '/search'

    def _query(self, **changes: str | int) -> str:
        params: dict[str, str | int] = {'q': self.query}
        if self.restrict_community:
            params['restrict_community'] = 'on'
        if self.sort != DEFAULT_SORT:
            params['sort'] = self.sort
        if self.time_filter != DEFAULT_TIME_FILTER:
            params['t'] = self.time_filter
        params.update(changes)
        return '?' + urlencode(params)

    def sort_query(self, sort: str) -> str:
        return self._query(sort=sort)

    def time_query(self, time_filter: str) -> str:
        return self._query(t=time_filter)

    @property
    def next_query(self) -> str | None:
        if not self.more or not self.rows:
            return None
        return self._query(
            count=self.offset + len(self.rows), after=str(self.rows[-1].link.fullname)
        )

    @property
    def prev_query(self) -> str | None:
        if self.offset <= 0 or not self.rows:
            return None
        return self._query(count=self.offset + 1, before=str(self.rows[0].link.fullname))

    def _links_json(self, modhash: str) -> JSON:
        children: list[JSON] = [{'kind': 't3', 'data': row.data()} for row in self.rows]
        after = str(self.rows[-1].link.fullname) if self.more and self.rows else None
        before = str(self.rows[0].link.fullname) if self.offset > 0 and self.rows else None
        return listing_json(children, after=after, before=before, modhash=modhash)

    def _communities_json(self, modhash: str) -> JSON:
        children: list[JSON] = [
            {
                'kind': 't5',
                'data': community_data(
                    row.community, is_subscriber=row.is_subscriber, is_moderator=False
                ),
            }
            for row in self.community_rows
        ]
        return listing_json(children, after=None, before=None, modhash=modhash)

    def to_json(self) -> JSON:
        """A ``Listing`` of links; of communities for ``type=community``; both, links first."""
        modhash = self.viewer.modhash if self.viewer else ''
        if 'link' not in self.types:
            return self._communities_json(modhash)
        if 'community' not in self.types:
            return self._links_json(modhash)
        return [self._links_json(modhash), self._communities_json(modhash)]


@dataclass(frozen=True, kw_only=True)
class CommunityNames(View):
    """``/api/search_community_names``: community names starting with the query."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    names: tuple[str, ...]

    def to_json(self) -> JSON:
        return {'names': list(self.names)}
