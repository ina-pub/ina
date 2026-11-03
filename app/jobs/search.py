"""The ``search`` queue: make a link's or community's OpenSearch document match Postgres."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.jobs.context import search, transaction
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo
from app.services import search as search_service


async def link_indexing(link_fullname: str) -> None:
    async with transaction() as session:
        await search_service.index_link(
            search(),
            PgLinkRepo(session),
            PgCommunityRepo(session),
            Fullname.parse(link_fullname).id,
        )


async def community_indexing(community_fullname: str) -> None:
    async with transaction() as session:
        await search_service.index_community(
            search(), PgCommunityRepo(session), Fullname.parse(community_fullname).id
        )


index_link = dramatiq.actor(actor_name='index_link', queue_name='search')(link_indexing)
index_community = dramatiq.actor(actor_name='index_community', queue_name='search')(
    community_indexing
)
