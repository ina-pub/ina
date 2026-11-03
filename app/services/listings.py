"""Pages of links: which communities a listing covers, its sort and window, and its cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.ids import Fullname, Kind
from app.infra.cache import LISTING_TTL
from app.repos.base import Link, ListingQuery, Sort

if TYPE_CHECKING:
    from app.infra.cache import Cache
    from app.repos.base import Account, CommunityRepo, LinkRepo, SubscriptionRepo
    from app.settings import Settings

TIME_FILTERS: dict[str, timedelta | None] = {
    'hour': timedelta(hours=1),
    'day': timedelta(days=1),
    'week': timedelta(weeks=1),
    'month': timedelta(days=30),
    'year': timedelta(days=365),
    'all': None,
}
DEFAULT_TIME_FILTER = 'day'
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
# A front page merges at most this many subscriptions, and this many defaults.
MAX_FRONT_PAGE_COMMUNITIES = 100
DEFAULT_COMMUNITIES = 50
DEFAULTS_TTL = 5 * 60
# Rising ranks the last day's links, and keeps a list this long, as r2's did.
RISING_WINDOW = timedelta(days=1)
RISING_SIZE = 200
RISING_TTL = 60


@dataclass(frozen=True, slots=True)
class Scope:
    """The communities a listing covers (None: r/all) and the name it is cached under, if any."""

    community_ids: tuple[int, ...] | None
    cache_name: str | None
    # Only links to this domain, in r/all's communities.
    domain: str | None = None
    # Only links by these accounts, in r/all's communities.
    author_ids: tuple[int, ...] | None = None

    @classmethod
    def community(cls, community_id: int) -> Scope:
        return cls((community_id,), community_scope(community_id))

    @classmethod
    def of_domain(cls, domain: str) -> Scope:
        return cls(None, None, domain.lower())

    @classmethod
    def of_authors(cls, author_ids: list[int]) -> Scope:
        return cls(None, None, author_ids=tuple(author_ids))


ALL = Scope(None, 'all')


def community_scope(community_id: int) -> str:
    return f'community:{community_id}'


@dataclass(frozen=True, slots=True, kw_only=True)
class ListingPage:
    links: list[Link]
    # The last link, when the listing continues after it.
    after: Link | None
    # The first link, when the listing has links before it.
    before: Link | None


async def default_ids(settings: Settings, communities: CommunityRepo, cache: Cache) -> list[int]:
    async def load() -> list[int]:
        if settings.default_communities:
            return await communities.ids_by_names(list(settings.default_communities))
        return await communities.defaults(limit=DEFAULT_COMMUNITIES)

    return await cache.get_or_load('defaults', load, type_=list[int], ttl=DEFAULTS_TTL)


# How many communities the bar across the top of every page names.
BAR_SIZE = 50


async def bar_names(
    settings: Settings,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    cache: Cache,
    account: Account | None,
) -> tuple[str, ...]:
    """The communities across the top of every page: the viewer's subscriptions, or the defaults."""
    if account is not None:
        mine = await subscriptions.subscribed(account.id, limit=BAR_SIZE, after=None)
        if mine:
            return tuple(community.name for community in mine)

    async def load() -> list[str]:
        found = await communities.by_ids(await default_ids(settings, communities, cache))
        return sorted((community.name for community in found.values()), key=str.lower)

    names = await cache.get_or_load('default_names', load, type_=list[str], ttl=DEFAULTS_TTL)
    return tuple(names)


async def front_scope(
    settings: Settings,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    cache: Cache,
    account: Account | None,
) -> Scope:
    """The viewer's subscriptions, or the defaults for anyone without any."""
    if account is not None:
        ids = await subscriptions.subscribed_ids(account.id, limit=MAX_FRONT_PAGE_COMMUNITIES)
        if ids:
            return Scope(tuple(ids), None)
    return Scope(tuple(await default_ids(settings, communities, cache)), 'front')


async def page(
    links: LinkRepo,
    cache: Cache,
    scope: Scope,
    *,
    sort: Sort,
    time_filter: str = DEFAULT_TIME_FILTER,
    after: Link | None = None,
    before: Link | None = None,
    limit: int = DEFAULT_LIMIT,
    include_nsfw: bool = True,
    min_score: int | None = None,
    now: datetime | None = None,
) -> ListingPage:
    window = TIME_FILTERS.get(time_filter) if sort in {Sort.TOP, Sort.CONTROVERSIAL} else None
    since = (now or datetime.now(UTC)) - window if window else None
    # One more than a page tells whether the listing goes on.
    if sort is Sort.RISING:
        since = (now or datetime.now(UTC)) - RISING_WINDOW
    query = ListingQuery(
        community_ids=scope.community_ids,
        sort=sort,
        since=since,
        after=None if sort is Sort.RISING else after,
        before=None if sort is Sort.RISING else before,
        limit=RISING_SIZE if sort is Sort.RISING else limit + 1,
        include_nsfw=include_nsfw,
        min_score=min_score,
        domain=scope.domain,
        author_ids=scope.author_ids,
    )
    ids = await _ids(links, cache, scope, query, time_filter)
    if sort is Sort.RISING:
        ids = _positioned(ids, after, before, limit)
    found = await cache.things(
        {link_id: str(Fullname(Kind.LINK, link_id)) for link_id in ids}, links.by_ids, type_=Link
    )
    ordered = [found[link_id] for link_id in ids if link_id in found]
    more = len(ordered) > limit
    if before is not None:
        shown = list(reversed(ordered[:limit]))
        return ListingPage(
            links=shown, after=shown[-1] if shown else None, before=shown[0] if more else None
        )
    shown = ordered[:limit]
    return ListingPage(
        links=shown,
        after=shown[-1] if more else None,
        before=shown[0] if after is not None and shown else None,
    )


def _positioned(ids: list[int], after: Link | None, before: Link | None, limit: int) -> list[int]:
    """A page of a ranked list by position, shaped as a keyset query would return it.

    That is one more than a page past ``after``, or reversed, one more than a page before
    ``before``. Rising has no keyset: its order moves with the clock.
    """
    if before is not None and before.id in ids:
        end = ids.index(before.id)
        return list(reversed(ids[max(end - limit - 1, 0) : end]))
    start = ids.index(after.id) + 1 if after is not None and after.id in ids else 0
    return ids[start : start + limit + 1]


async def _ids(
    links: LinkRepo, cache: Cache, scope: Scope, query: ListingQuery, time_filter: str
) -> list[int]:
    load = links.rising if query.sort is Sort.RISING else links.listing
    if scope.cache_name is None:
        return await load(query)
    generation = await cache.generation(scope.cache_name)
    name = ':'.join(
        str(part)
        for part in (
            'listing',
            scope.cache_name,
            generation,
            query.sort,
            time_filter if query.since else '',
            query.after.id if query.after else '',
            query.before.id if query.before else '',
            query.limit,
            int(query.include_nsfw),
            '' if query.min_score is None else query.min_score,
        )
    )
    ttl = RISING_TTL if query.sort is Sort.RISING else LISTING_TTL
    return await cache.get_or_load(name, lambda: load(query), type_=list[int], ttl=ttl)
