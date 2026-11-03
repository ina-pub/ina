"""The ``media`` queue: make a link's thumbnail."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.infra.cache import Cache
from app.jobs.context import fetcher, redis, store, transaction
from app.repos.pg.links import PgLinkRepo
from app.services import media


async def link_thumbnail(link_fullname: str) -> None:
    async with transaction() as session:
        link = await PgLinkRepo(session).by_id(Fullname.parse(link_fullname).id)
    if link is None:
        return
    # Fetching takes seconds at worst, so it holds no database connection.
    async with transaction() as session:
        made = await media.thumbnail_link(PgLinkRepo(session), store(), fetcher(), link)
    if made is not None:
        await Cache(redis()).forget_things([link_fullname])


make_thumbnail = dramatiq.actor(actor_name='make_thumbnail', queue_name='media')(link_thumbnail)
