"""The ``trees`` queue: precompute the comment order of threads too large to sort per request."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.infra.cache import Cache
from app.jobs.context import redis, transaction
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.links import PgLinkRepo
from app.services import threads


async def rebuild(link_fullname: str) -> None:
    async with transaction() as session:
        link = await PgLinkRepo(session).by_id(Fullname.parse(link_fullname).id)
        if link is not None:
            await threads.rebuild_trees(PgCommentRepo(session), Cache(redis()), link)


rebuild_tree = dramatiq.actor(actor_name='rebuild_tree', queue_name='trees')(rebuild)
