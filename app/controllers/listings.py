"""Link listings (front page, r/all, communities), submitting, and a link's own page."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from typing import Any

from litestar import Request, get, post
from litestar.background_tasks import BackgroundTask, BackgroundTasks
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_404_NOT_FOUND

from app.controllers.auth import client_ip, to_viewer
from app.controllers.communities import sidebar, standing_of, visible
from app.controllers.deps import (
    AccountsDep,
    CacheDep,
    CommunitiesDep,
    DetailsDep,
    LinksDep,
    MultisDep,
    RedisDep,
    SettingsDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
    VotesDep,
)
from app.controllers.details import Details
from app.controllers.flair import flair_box
from app.controllers.queue import publish
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.ids import Fullname, Kind
from app.infra.cache import Cache
from app.jobs.bouncer import check_new
from app.jobs.media import make_thumbnail
from app.jobs.scores import recount_link
from app.repos.base import CommunityRepo, Link, LinkRepo, Prefs, Sort, VoteRepo
from app.services import links as links_service
from app.services import listings, ratelimits
from app.services.links import SubmitForm
from app.services.listings import ALL, DEFAULT_TIME_FILTER, MAX_LIMIT, TIME_FILTERS, Scope
from app.services.relations import FRIEND
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.links import CommunityListingPage, FrontPage, Listing, SubmitPage, permalink
from app.views.multis import MultiPage

# r2 joined at most this many communities in one /r/a+b path.
MAX_COMBINED = 100
FRONT_SORTS = {'': Sort.HOT, **{sort.value: sort for sort in Sort}}


@dataclass(frozen=True, slots=True, kw_only=True)
class PageRequest:
    """The query parameters every listing takes, as reddit named them."""

    after: str
    before: str
    count: int
    limit: int | None
    t: str


async def anchor(links: LinkRepo, fullname: str) -> Link | None:
    try:
        parsed = Fullname.parse(fullname)
    except ValueError:
        return None
    return await links.by_id(parsed.id) if parsed.kind is Kind.LINK else None


async def likes(votes: VoteRepo, viewer: Authenticated | None, shown: list[Link]) -> dict[int, int]:
    if viewer is None:
        return {}
    return await votes.directions(viewer.account.id, Kind.LINK, [link.id for link in shown])


def hidden(link: Link, directions: dict[int, int], viewer: Authenticated | None) -> bool:
    """Whether the viewer's prefs hide a link they voted on; never their own, as on r2.

    This filters one page after the query, so the cached listing stays the same for everyone.
    """
    if viewer is None or link.author_id == viewer.account.id:
        return False
    prefs = viewer.account.prefs
    direction = directions.get(link.id, 0)
    return (direction == 1 and prefs.hide_ups) or (direction == -1 and prefs.hide_downs)


async def build_listing(
    scope: Scope,
    sort: Sort,
    request: PageRequest,
    *,
    links: LinkRepo,
    votes: VoteRepo,
    cache: Cache,
    viewer: Authenticated | None,
    base_path: str,
    include_nsfw: bool,
    details: Details,
    pinned: tuple[int, ...] = (),
) -> Listing:
    prefs = viewer.account.prefs if viewer else Prefs()
    limit = max(1, min(request.limit or prefs.numsites, MAX_LIMIT))
    time_filter = request.t if request.t in TIME_FILTERS else DEFAULT_TIME_FILTER
    before = await anchor(links, request.before) if request.before else None
    after = None if before else (await anchor(links, request.after) if request.after else None)
    page = await listings.page(
        links,
        cache,
        scope,
        sort=sort,
        time_filter=time_filter,
        after=after,
        before=before,
        limit=limit,
        include_nsfw=include_nsfw,
        min_score=prefs.min_link_score,
    )
    directions = await likes(votes, viewer, page.links)
    # Ranks continue across pages; a page reached backwards ends where the next one began.
    first_rank = max(request.count - len(page.links), 0) + 1 if before else request.count + 1
    shown = [
        link
        for link in page.links
        if link.id not in pinned and not hidden(link, directions, viewer)
    ]
    stickies: list[Link] = []
    if pinned and not request.after and not request.before:
        # Stickied links head the first page of hot, as on r2, without ranks.
        found = await links.by_ids(list(pinned))
        stickies = [found[i] for i in pinned if i in found and not found[i].removed]
        directions |= await likes(votes, viewer, stickies)
    extras = await details.of([*stickies, *shown], viewer)
    # What the viewer hid stays out of every listing but their own hidden page, as on r2.
    stickies = [link for link in stickies if link.fullname not in extras.hidden]
    shown = [link for link in shown if link.fullname not in extras.hidden]
    rows = tuple(extras.row(link, None, directions) for link in stickies) + tuple(
        extras.row(link, first_rank + index, directions) for index, link in enumerate(shown)
    )
    return Listing(
        rows=rows,
        sort=sort,
        time_filter=time_filter,
        after=str(page.after.fullname) if page.after else None,
        before=str(page.before.fullname) if page.before else None,
        count=first_rank - 1,
        base_path=base_path,
    )


def page_request(after: str, before: str, count: int, limit: int | None, t: str) -> PageRequest:
    return PageRequest(after=after, before=before, count=max(count, 0), limit=limit, t=t)


def over_18(viewer: Authenticated | None) -> bool:
    return viewer is not None and viewer.account.prefs.over_18


@get(['/', '/hot', '/new', '/rising', '/top', '/controversial'])
async def show_front(
    request: Request[Any, Any, Any],
    settings: SettingsDep,
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    details: DetailsDep,
    multis: MultisDep,
    cache: CacheDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> FrontPage:
    sort = FRONT_SORTS[request.url.path.strip('/')]
    account = viewer.account if viewer else None
    scope = await listings.front_scope(settings, communities, subscriptions, cache, account)
    listing = await build_listing(
        scope,
        sort,
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        cache=cache,
        viewer=viewer,
        base_path='/',
        include_nsfw=over_18(viewer),
        details=details,
    )
    mine = await multis.by_owner(viewer.account.id) if viewer else []
    return FrontPage(viewer=to_viewer(viewer), listing=listing, where='front', multis=tuple(mine))


async def community_listing(
    name: str,
    sort: Sort,
    page: PageRequest,
    *,
    links: LinkRepo,
    votes: VoteRepo,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    details: Details,
    cache: Cache,
    viewer: Authenticated | None,
) -> FrontPage | CommunityListingPage | MultiPage:
    if name.lower() == 'all':
        listing = await build_listing(
            ALL,
            sort,
            page,
            links=links,
            votes=votes,
            cache=cache,
            viewer=viewer,
            base_path='/in/all/',
            include_nsfw=over_18(viewer),
            details=details,
        )
        return FrontPage(viewer=to_viewer(viewer), listing=listing, where='all')
    if '+' in name:
        return await combined_listing(
            name,
            sort,
            page,
            links=links,
            votes=votes,
            communities=communities,
            details=details,
            cache=cache,
            viewer=viewer,
        )
    community, standing = await visible(communities, viewer, name)
    listing = await build_listing(
        Scope.community(community.id),
        sort,
        page,
        links=links,
        votes=votes,
        cache=cache,
        viewer=viewer,
        base_path=community.path,
        include_nsfw=True,
        details=details,
        pinned=tuple(Fullname.parse(f).id for f in community.settings.sticky_fullnames)
        if sort is Sort.HOT
        else (),
    )
    side = await sidebar(communities, subscriptions, viewer, community, standing)
    box = await flair_box(details.flair, community, standing, viewer)
    return CommunityListingPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=replace(side, flair_box=box),
        listing=listing,
    )


async def combined_listing(
    name: str,
    sort: Sort,
    page: PageRequest,
    *,
    links: LinkRepo,
    votes: VoteRepo,
    communities: CommunityRepo,
    details: Details,
    cache: Cache,
    viewer: Authenticated | None,
) -> MultiPage:
    """``/in/pics+funny``: several communities at once, as r2 read them, skipping unknown names."""
    names = [part for part in name.split('+') if part][:MAX_COMBINED]
    found = await communities.ids_by_names(names)
    hidden = set(
        await communities.excluded_from_search(
            viewer.account.id if viewer else None, include_nsfw=True
        )
    )
    ids = [community_id for community_id in found if community_id not in hidden]
    if not ids:
        raise NotFoundException
    shown = await communities.by_ids(ids)
    listing = await build_listing(
        Scope(tuple(ids), None),
        sort,
        page,
        links=links,
        votes=votes,
        cache=cache,
        viewer=viewer,
        base_path=f'/in/{"+".join(shown[i].name for i in ids)}/',
        include_nsfw=over_18(viewer),
        details=details,
    )
    return MultiPage(
        viewer=to_viewer(viewer), listing=listing, names=tuple(shown[i].name for i in ids)
    )


@get('/in/random')
async def random_community(
    settings: SettingsDep, communities: CommunitiesDep, cache: CacheDep
) -> Redirect:
    """One of the default communities, chosen at random, as r2's /r/random was."""
    ids = await listings.default_ids(settings, communities, cache)
    chosen = await communities.by_id(secrets.choice(ids)) if ids else None
    if chosen is None:
        raise NotFoundException
    return Redirect(chosen.path)


@get(['/domain/{domain:str}', '/domain/{domain:str}/{sort:str}'])
async def show_domain(
    domain: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    details: DetailsDep,
    cache: CacheDep,
    viewer: ViewerDep,
    sort: FromPath[str] = 'hot',
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> FrontPage:
    """Links to one domain from r/all's communities."""
    if sort not in Sort:
        raise NotFoundException
    scope = Scope.of_domain(domain)
    listing = await build_listing(
        scope,
        Sort(sort),
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        cache=cache,
        viewer=viewer,
        base_path=f'/domain/{scope.domain}/',
        include_nsfw=over_18(viewer),
        details=details,
    )
    return FrontPage(viewer=to_viewer(viewer), listing=listing, where='domain', domain=scope.domain)


@get(['/in/friends', '/in/friends/{sort:str}'])
async def show_friends_links(
    links: LinksDep,
    votes: VotesDep,
    accounts: AccountsDep,
    details: DetailsDep,
    cache: CacheDep,
    user: UserDep,
    sort: FromPath[str] = 'new',
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> FrontPage:
    """What the viewer's friends submitted, newest first, as r2's /r/friends."""
    if sort not in Sort:
        raise NotFoundException
    friends = await accounts.related_ids(user.account.id, FRIEND)
    listing = await build_listing(
        Scope.of_authors(friends),
        Sort(sort),
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        cache=cache,
        viewer=user,
        base_path='/in/friends/',
        include_nsfw=over_18(user),
        details=details,
    )
    return FrontPage(viewer=to_viewer(user), listing=listing, where='friends')


@get('/in/{name:str}')
async def show_community(
    name: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    details: DetailsDep,
    cache: CacheDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> FrontPage | CommunityListingPage | MultiPage:
    return await community_listing(
        name,
        Sort.HOT,
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        details=details,
        cache=cache,
        viewer=viewer,
    )


@get('/in/{name:str}/{sort:str}')
async def show_community_sorted(
    name: FromPath[str],
    sort: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    details: DetailsDep,
    cache: CacheDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> FrontPage | CommunityListingPage | MultiPage:
    if sort not in Sort:
        raise NotFoundException
    return await community_listing(
        name,
        Sort(sort),
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        details=details,
        cache=cache,
        viewer=viewer,
    )


@get(['/submit', '/in/{name:str}/submit'])
async def show_submit(
    communities: CommunitiesDep,
    user: UserDep,
    name: FromPath[str | None] = None,
    url: FromQuery[str] = '',
    title: FromQuery[str] = '',
    selftext: FromQuery[str] = '',
) -> SubmitPage:
    community = (await visible(communities, user, name))[0] if name else None
    form = SubmitForm(
        community=community.name if community else '',
        kind='self' if selftext == 'true' else 'link',
        url=url,
        title=title,
        sendreplies='on',
    )
    return SubmitPage(viewer=to_viewer(user), community=community, form=form)


@post('/api/submit')
async def submit(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[SubmitForm],
    settings: SettingsDep,
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    redis: RedisDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    community = await communities.by_name(data.community) if data.community else None
    try:
        if community is None:
            code = ErrorCode.COMMUNITY_NOEXIST if data.community else ErrorCode.COMMUNITY_REQUIRED
            raise InaError.of(code, 'community', status_code=HTTP_404_NOT_FOUND)
        standing = await standing_of(communities, user, community)
        await ratelimits.enforce(redis, ratelimits.SUBMIT_BY_ACCOUNT, str(user.account.id))
        link = await links_service.submit(
            links, votes, user.account, community, data, standing=standing, ip=client_ip(request)
        )
    except InaError as error:
        page = SubmitPage(
            viewer=to_viewer(user), community=community, form=data, errors=error.errors
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    url = permalink(link)
    result = FormResult(
        redirect=url, data={'url': url, 'id': link.fullname.id36, 'name': str(link.fullname)}
    )
    # After the commit: drop the community's cached listings and recount from the votes table.
    tasks = [
        BackgroundTask(cache.bump, listings.community_scope(community.id)),
        publish(recount_link, str(link.fullname)),
    ]
    if community.bouncer_rules.strip():
        tasks.append(publish(check_new, str(link.fullname)))
    if settings.thumbnails and not link.is_self:
        tasks.append(publish(make_thumbnail, str(link.fullname)))
    return Rendered(content=result, background=BackgroundTasks(tasks))
