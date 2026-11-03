"""Search: links across the site or in one community, communities by name, and name lookups."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import Any

from litestar import Request, get, post
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.status_codes import HTTP_404_NOT_FOUND

from app.controllers.auth import CSRF_EXEMPT, to_viewer
from app.controllers.communities import sidebar, visible
from app.controllers.deps import (
    CacheDep,
    CommunitiesDep,
    DetailsDep,
    LinksDep,
    SearchDep,
    SubscriptionsDep,
    ViewerDep,
    VotesDep,
)
from app.controllers.details import Details
from app.controllers.listings import likes, over_18
from app.controllers.moderation import truthy
from app.controllers.render import Rendered, wants_json
from app.errors import ErrorCode, InaError
from app.infra.cache import Cache
from app.infra.opensearch import SearchClient, SearchUnavailableError
from app.repos.base import Community, CommunityRepo, LinkRepo, Prefs, SubscriptionRepo, VoteRepo
from app.search.query import fielded
from app.services import search as service
from app.services.listings import MAX_LIMIT, TIME_FILTERS
from app.services.search import (
    COMMUNITY_RESULTS,
    DEFAULT_SORT,
    DEFAULT_TIME_FILTER,
    MAX_NAME_QUERY,
    MAX_QUERY,
    SORTS,
    LinkResults,
    LinkSearch,
)
from app.services.sessions import Authenticated
from app.views.communities import CommunitiesPage, CommunityRow, Sidebar
from app.views.search import CommunityNames, SearchPage

log = logging.getLogger(__name__)
TYPES = frozenset({'link', 'community'})


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchRequest:
    """``/search``'s query parameters, as r2 named them."""

    q: str
    sort: str
    t: str
    restrict_community: str
    type: str
    after: str
    before: str
    count: int
    limit: int | None


@dataclass
class NamesForm:
    query: str = ''
    include_over_18: str = 'true'
    exact: str = 'false'


def result_types(asked: str, *, api: bool) -> frozenset[str]:
    """r2's ``type``: comma-separated; the API defaults to links, the site to both."""
    chosen = frozenset(part.strip() for part in asked.split(',')) & TYPES
    return chosen or (frozenset({'link'}) if api else TYPES)


def offset_of(request: SearchRequest, limit: int) -> int:
    """Where this page starts. ``after``/``before`` only say which way; ``count`` says how far."""
    count = max(request.count, 0)
    if request.before:
        return max(count - 1 - limit, 0)
    return count if request.after else 0


async def community_rows(
    subscriptions: SubscriptionRepo, viewer: Authenticated | None, found: list[Community]
) -> tuple[CommunityRow, ...]:
    subscribed = (
        await subscriptions.subscribed_among(viewer.account.id, [s.id for s in found])
        if viewer
        else set()
    )
    return tuple(CommunityRow(s, s.id in subscribed) for s in found)


@dataclass(frozen=True, slots=True, kw_only=True)
class Searching:
    links: LinkRepo
    votes: VoteRepo
    communities: CommunityRepo
    subscriptions: SubscriptionRepo
    cache: Cache
    search: SearchClient
    details: Details
    viewer: Authenticated | None


async def search_page(
    http: Request[Any, Any, Any],
    request: SearchRequest,
    deps: Searching,
    *,
    community: Community | None = None,
    side: Sidebar | None = None,
) -> SearchPage:
    viewer = deps.viewer
    prefs = viewer.account.prefs if viewer else Prefs()
    limit = max(1, min(request.limit or prefs.numsites, MAX_LIMIT))
    text = request.q[:MAX_QUERY]
    sort = request.sort if request.sort in SORTS else DEFAULT_SORT
    time_filter = request.t if request.t in TIME_FILTERS else DEFAULT_TIME_FILTER
    restricted = community is not None and truthy(request.restrict_community)
    types = result_types(request.type, api=wants_json(http))
    if fielded(text):
        # As on r2: a fielded query is about links.
        types -= {'community'}
    offset = offset_of(request, limit)
    # Searching one community shows what its listing shows, NSFW included.
    include_nsfw = restricted or over_18(viewer)
    page = partial(
        SearchPage,
        viewer=to_viewer(viewer),
        query=text,
        sort=sort,
        time_filter=time_filter,
        community=community,
        sidebar=side,
        restrict_community=restricted,
        types=types,
        offset=offset,
    )
    try:
        results = LinkResults(links=[], more=False)
        if 'link' in types:
            excluded = await deps.communities.excluded_from_search(
                viewer.account.id if viewer else None, include_nsfw=include_nsfw
            )
            search = LinkSearch(
                text=text,
                sort=sort,
                time_filter=time_filter,
                community_id=community.id if community and restricted else None,
                excluded=tuple(excluded),
                include_nsfw=include_nsfw,
                offset=offset,
                limit=limit,
            )
            results = await service.find_links(deps.search, deps.links, deps.cache, search)
        found: list[Community] = []
        # Communities head the first page of a site-wide search, as on r2.
        if 'community' in types and not restricted and offset == 0:
            found, _ = await service.find_communities(
                deps.search,
                deps.communities,
                text,
                include_nsfw=over_18(viewer),
                limit=COMMUNITY_RESULTS if 'link' in types else limit,
            )
    except SearchUnavailableError:
        log.exception('search failed')
        return page(unavailable=True)
    directions = await likes(deps.votes, viewer, results.links)
    extras = await deps.details.of(results.links, viewer)
    rows = tuple(
        extras.row(link, offset + index + 1, directions) for index, link in enumerate(results.links)
    )
    return page(
        rows=rows,
        more=results.more,
        cleaned=results.cleaned,
        community_rows=await community_rows(deps.subscriptions, viewer, found),
    )


@get('/search')
async def show_search(
    request: Request[Any, Any, Any],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    search: SearchDep,
    details: DetailsDep,
    viewer: ViewerDep,
    q: FromQuery[str] = '',
    sort: FromQuery[str] = DEFAULT_SORT,
    t: FromQuery[str] = DEFAULT_TIME_FILTER,
    type: FromQuery[str] = '',  # noqa: A002 - r2's parameter name
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
) -> SearchPage:
    form = SearchRequest(
        q=q,
        sort=sort,
        t=t,
        restrict_community='',
        type=type,
        after=after,
        before=before,
        count=count,
        limit=limit,
    )
    deps = Searching(
        links=links,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        cache=cache,
        search=search,
        details=details,
        viewer=viewer,
    )
    return await search_page(request, form, deps)


@get('/in/{name:str}/search')
async def show_community_search(
    request: Request[Any, Any, Any],
    name: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    search: SearchDep,
    details: DetailsDep,
    viewer: ViewerDep,
    q: FromQuery[str] = '',
    sort: FromQuery[str] = DEFAULT_SORT,
    t: FromQuery[str] = DEFAULT_TIME_FILTER,
    restrict_community: FromQuery[str] = '',
    type: FromQuery[str] = '',  # noqa: A002 - r2's parameter name
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
) -> SearchPage:
    """As ``/search``; ``restrict_community`` keeps it here, off by default as on r2."""
    community, standing = await visible(communities, viewer, name)
    form = SearchRequest(
        q=q,
        sort=sort,
        t=t,
        restrict_community=restrict_community,
        type=type,
        after=after,
        before=before,
        count=count,
        limit=limit,
    )
    deps = Searching(
        links=links,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        cache=cache,
        search=search,
        details=details,
        viewer=viewer,
    )
    side = await sidebar(communities, subscriptions, viewer, community, standing)
    return await search_page(request, form, deps, community=community, side=side)


@get('/ins/search')
async def show_community_results(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    search: SearchDep,
    viewer: ViewerDep,
    q: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
) -> CommunitiesPage:
    """Communities by name, title and description; ``count`` pages, as on ``/search``."""
    prefs = viewer.account.prefs if viewer else Prefs()
    limit = max(1, min(limit or prefs.numsites, MAX_LIMIT))
    offset = max(count, 0)
    text = q[:MAX_QUERY]
    page = partial(
        CommunitiesPage, viewer=to_viewer(viewer), where='search', query=text, offset=offset
    )
    try:
        found, more = await service.find_communities(
            search, communities, text, include_nsfw=over_18(viewer), offset=offset, limit=limit
        )
    except SearchUnavailableError:
        log.exception('community search failed')
        return page(rows=(), after=None, unavailable=True)
    return page(
        rows=await community_rows(subscriptions, viewer, found),
        after=str(found[-1].fullname) if more and found else None,
    )


async def community_names(
    form: NamesForm, communities: CommunityRepo, search: SearchClient
) -> CommunityNames:
    """r2's search_reddit_names: one exact name, or the names starting with ``query``."""
    text = form.query.strip().removeprefix('/').removeprefix('r/')[:MAX_NAME_QUERY]
    if not text:
        return CommunityNames(names=())
    if truthy(form.exact):
        # r2 ignored include_over_18 for an exact match.
        community = await communities.by_name(text)
        if community is None:
            raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'query', status_code=HTTP_404_NOT_FOUND)
        return CommunityNames(names=(community.name,))
    try:
        names = await service.names_starting(
            search, communities, text, include_nsfw=truthy(form.include_over_18)
        )
    except SearchUnavailableError:
        log.exception('name search failed')
        names = []
    return CommunityNames(names=tuple(names))


@get('/api/search_community_names')
async def get_community_names(
    request: Request[Any, Any, Any], communities: CommunitiesDep, search: SearchDep
) -> Rendered:
    # ``query`` is a name Litestar keeps for itself, so the parameters are read directly.
    params = request.query_params
    form = NamesForm(
        query=params.get('query', ''),
        include_over_18=params.get('include_over_18', 'true'),
        exact=params.get('exact', 'false'),
    )
    return Rendered(content=await community_names(form, communities, search))


# It only reads, so r2 exempted it from the CSRF check.
@post('/api/search_community_names', opt={CSRF_EXEMPT: True})
async def post_community_names(
    data: URLEncodedBody[NamesForm], communities: CommunitiesDep, search: SearchDep
) -> Rendered:
    return Rendered(content=await community_names(data, communities, search))
