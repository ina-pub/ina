"""The ``media`` queue: make a link's thumbnail, and find the player it opens in."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.infra.cache import Cache
from app.jobs.context import fetcher, redis, store, transaction
from app.repos.pg.links import PgLinkRepo
from app.services import embeds, media


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


async def link_embed(link_fullname: str) -> None:
    async with transaction() as session:
        link = await PgLinkRepo(session).by_id(Fullname.parse(link_fullname).id)
    if link is None or link.is_self:
        return
    # Most sites need no request; short links and Bluesky handles do, outside any transaction.
    found = await embeds.find(fetcher(), link.url)
    if found is None or found == link.embed:
        return
    async with transaction() as session:
        await PgLinkRepo(session).set_embed(link.id, found)
    await Cache(redis()).forget_things([link_fullname])


make_thumbnail = dramatiq.actor(actor_name='make_thumbnail', queue_name='media')(link_thumbnail)
find_embed = dramatiq.actor(actor_name='find_embed', queue_name='media')(link_embed)
