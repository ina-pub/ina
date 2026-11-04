"""Rebuild the search indices from Postgres.

    uv run python -m scripts.rebuild_search [--recreate] [--things links,communities]
                                            [--from ID] [--batch SIZE]

``--recreate`` drops the indices first, for new mappings. Without it, documents are rewritten in
place and removed links are deleted. Each batch prints the last id done, so a stopped run resumes
with ``--from`` that id plus one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo
from app.search import create_search
from app.search.documents import COMMUNITIES, LINKS, community_document, link_document, searchable
from app.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.infra.opensearch import SearchClient

DEFAULT_BATCH = 500
THINGS = (LINKS, COMMUNITIES)


async def _links(client: SearchClient, session: AsyncSession, first: int, last: int) -> int:
    found = await PgLinkRepo(session).by_ids(list(range(first, last + 1)))
    communities = await PgCommunityRepo(session).by_ids(
        sorted({link.community_id for link in found.values()})
    )
    documents = [
        (str(link.id), link_document(link, communities[link.community_id]))
        for link in found.values()
        if searchable(link) and link.community_id in communities
    ]
    gone = [str(link.id) for link in found.values() if not searchable(link)]
    await client.bulk(LINKS, documents, gone)
    return len(documents)


async def _communities(client: SearchClient, session: AsyncSession, first: int, last: int) -> int:
    found = await PgCommunityRepo(session).by_ids(list(range(first, last + 1)))
    # Banned communities stay out of search.
    documents = [(str(s.id), community_document(s)) for s in found.values() if not s.banned]
    await client.bulk(COMMUNITIES, documents)
    return len(documents)


async def rebuild(
    *,
    recreate: bool = False,
    things: tuple[str, ...] = THINGS,
    start: int = 1,
    batch: int = DEFAULT_BATCH,
) -> int:
    """Index each kind of thing from id ``start`` up; return how many documents were written."""
    settings = get_settings()
    engine = db.create_engine(settings)
    sessions = async_sessionmaker(engine)
    client = create_search(settings)
    total = 0
    try:
        if recreate:
            await client.recreate()
        for thing in things:
            async with sessions() as session:
                repo = PgLinkRepo(session) if thing == LINKS else PgCommunityRepo(session)
                last = await repo.max_id()
            for first in range(start, last + 1, batch):
                through = min(first + batch - 1, last)
                async with sessions() as session:
                    if thing == LINKS:
                        total += await _links(client, session, first, through)
                    else:
                        total += await _communities(client, session, first, through)
                sys.stdout.write(f'{thing}: done through id {through} ({total} documents)\n')
    finally:
        await client.aclose()
        await engine.dispose()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or '').partition('\n')[0])
    parser.add_argument('--recreate', action='store_true', help='drop and recreate the indices')
    parser.add_argument(
        '--things', default=','.join(THINGS), help='comma-separated: links,communities'
    )
    parser.add_argument('--from', dest='start', type=int, default=1)
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    arguments = parser.parse_args()
    things = tuple(name for name in arguments.things.split(',') if name in THINGS)
    asyncio.run(
        rebuild(
            recreate=arguments.recreate, things=things, start=arguments.start, batch=arguments.batch
        )
    )


if __name__ == '__main__':
    main()
