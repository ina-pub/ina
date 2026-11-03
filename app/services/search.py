"""Searching links and communities, and keeping their OpenSearch documents current.

OpenSearch returns ids; the things themselves come from the cache or Postgres, so a result is the
same object every other listing shows. Results page by offset (``count``), as r2's search did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.ids import Fullname, Kind
from app.repos.base import Link
from app.search import query
from app.search.documents import COMMUNITIES, LINKS, community_document, link_document, searchable
from app.services.listings import TIME_FILTERS

if TYPE_CHECKING:
    from app.infra.cache import Cache
    from app.infra.opensearch import Body, SearchClient
    from app.repos.base import Community, CommunityRepo, LinkRepo

# Newer first among equals, then by id: ``created`` is to the second.
_NEWER = [{'created': 'desc'}, {'id': 'desc'}]
SORTS: dict[str, list[Body]] = {
    'relevance': [{'_score': 'desc'}, *_NEWER],
    'hot': [{'hot': 'desc'}, *_NEWER],
    'top': [{'score': 'desc'}, *_NEWER],
    'new': _NEWER,
    'comments': [{'comment_count': 'desc'}, *_NEWER],
}
DEFAULT_SORT = 'relevance'
DEFAULT_TIME_FILTER = 'all'
MAX_QUERY = 512
# r2 showed at most three communities above the first page of links.
COMMUNITY_RESULTS = 3
# Deeper pages cost OpenSearch more for results nobody reads.
MAX_OFFSET = 1000
MAX_NAMES = 10
MAX_NAME_QUERY = 50


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkSearch:
    text: str
    sort: str = DEFAULT_SORT
    time_filter: str = DEFAULT_TIME_FILTER
    # Search one community only.
    community_id: int | None = None
    # Communities to leave out: private ones closed to the viewer, NSFW ones unless shown.
    excluded: tuple[int, ...] = ()
    include_nsfw: bool = False
    offset: int = 0
    limit: int = 25


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkResults:
    links: list[Link]
    # Whether another page follows.
    more: bool
    # What was searched instead when the query could not be read.
    cleaned: str | None = None


def _clause(text: str) -> tuple[Body | None, str | None]:
    try:
        return query.parse(text), None
    except query.QueryError:
        cleaned = query.clean(text)
        return (query.parse(cleaned) if cleaned else None), cleaned


async def find_links(
    client: SearchClient,
    links: LinkRepo,
    cache: Cache,
    search: LinkSearch,
    *,
    now: datetime | None = None,
) -> LinkResults:
    """A page of links matching the query; an empty query lists a community's links, as r2 did."""
    clause, cleaned = _clause(search.text)
    if clause is None and search.community_id is None:
        return LinkResults(links=[], more=False, cleaned=cleaned)
    filters: list[Body] = []
    excluded: list[Body] = []
    if search.community_id is not None:
        filters.append({'term': {'community_id': search.community_id}})
    if search.excluded:
        excluded.append({'terms': {'community_id': list(search.excluded)}})
    if not search.include_nsfw:
        excluded.append({'term': {'nsfw': True}})
    if window := TIME_FILTERS.get(search.time_filter):
        since = (now or datetime.now(UTC)) - window
        filters.append({'range': {'created': {'gte': int(since.timestamp())}}})
    offset = max(0, min(search.offset, MAX_OFFSET))
    body: Body = {
        'query': {
            'bool': {'must': [clause or {'match_all': {}}], 'filter': filters, 'must_not': excluded}
        },
        'sort': SORTS.get(search.sort, SORTS[DEFAULT_SORT]),
        'from': offset,
        # One more than a page tells whether the results go on.
        'size': search.limit + 1,
        '_source': False,
    }
    hits = await client.search(LINKS, body)
    ids = [int(doc_id) for doc_id in hits.ids]
    found = await cache.things(
        {link_id: str(Fullname(Kind.LINK, link_id)) for link_id in ids}, links.by_ids, type_=Link
    )
    # The index trails Postgres by a job; drop what has since gone.
    shown = [found[i] for i in ids[: search.limit] if i in found and searchable(found[i])]
    return LinkResults(links=shown, more=len(ids) > search.limit, cleaned=cleaned)


async def find_communities(
    client: SearchClient,
    communities: CommunityRepo,
    text: str,
    *,
    include_nsfw: bool,
    offset: int = 0,
    limit: int = COMMUNITY_RESULTS,
) -> tuple[list[Community], bool]:
    """Communities by name, title and description, bigger ones first among equals."""
    if not text.strip():
        return [], False
    matching: Body = {
        'multi_match': {
            'query': text,
            'fields': ['name^3', 'name.exact^5', 'title^2', 'description'],
            'operator': 'and',
        }
    }
    body: Body = {
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'must': [matching],
                        'must_not': [] if include_nsfw else [{'term': {'nsfw': True}}],
                    }
                },
                'field_value_factor': {
                    'field': 'subscriber_count',
                    'modifier': 'log2p',
                    'missing': 0,
                },
            }
        },
        'from': max(0, min(offset, MAX_OFFSET)),
        'size': limit + 1,
        '_source': False,
    }
    hits = await client.search(COMMUNITIES, body)
    ids = [int(doc_id) for doc_id in hits.ids]
    found = await communities.by_ids(ids[:limit])
    return [found[i] for i in ids[:limit] if i in found], len(ids) > limit


async def names_starting(
    client: SearchClient, communities: CommunityRepo, prefix: str, *, include_nsfw: bool
) -> list[str]:
    """Community names beginning with ``prefix``, biggest first: r2's search_reddit_names."""
    body: Body = {
        'query': {
            'bool': {
                'filter': [{'prefix': {'name.exact': prefix.lower()}}],
                'must_not': [] if include_nsfw else [{'term': {'nsfw': True}}],
            }
        },
        'sort': [{'subscriber_count': 'desc'}],
        'size': MAX_NAMES,
        '_source': False,
    }
    ids = [int(doc_id) for doc_id in (await client.search(COMMUNITIES, body)).ids]
    found = await communities.by_ids(ids)
    return [found[i].name for i in ids if i in found]


async def index_link(
    client: SearchClient, links: LinkRepo, communities: CommunityRepo, link_id: int
) -> None:
    """Make the link's document match Postgres: written if searchable, deleted otherwise."""
    link = await links.by_id(link_id)
    community = await communities.by_id(link.community_id) if link else None
    if link is None or community is None or not searchable(link):
        await client.delete(LINKS, str(link_id))
        return
    await client.put(LINKS, str(link_id), link_document(link, community))


async def index_community(
    client: SearchClient, communities: CommunityRepo, community_id: int
) -> None:
    community = await communities.by_id(community_id)
    if community is None or community.banned:
        await client.delete(COMMUNITIES, str(community_id))
        return
    await client.put(COMMUNITIES, str(community_id), community_document(community))
